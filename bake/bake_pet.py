"""Bake a Codex pet into small animation strips the codex-pet mod can draw.

Reads the same pet folders Codex uses (${CODEX_HOME:-~/.codex}/pets/<id>/pet.json
+ spritesheet), so a pet hatched for Codex shows up in Claude Code unchanged.
Prints one JSON object on stdout; never writes into the Codex folder.
"""

import argparse
import base64
import io
import json
import os
import re
import sys
import time
from pathlib import Path

CELL_W, CELL_H = 192, 208

# Rows 0-8 of the Codex v1/v2 atlas: (state, used frames, per-frame ms).
STATES = [
    ("idle", [280, 110, 110, 140, 140, 320]),
    ("running-right", [120] * 7 + [220]),
    ("running-left", [120] * 7 + [220]),
    ("waving", [140] * 3 + [280]),
    ("jumping", [140] * 4 + [280]),
    ("failed", [140] * 7 + [240]),
    ("waiting", [150] * 5 + [260]),
    ("running", [120] * 5 + [220]),
    ("review", [150] * 5 + [280]),
]


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def list_pets(pets_dir: Path):
    pets = []
    if not pets_dir.is_dir():
        return pets
    for folder in sorted(pets_dir.iterdir()):
        manifest = folder / "pet.json"
        if not manifest.is_file():
            continue
        try:
            meta = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        sheet = folder / meta.get("spritesheetPath", "spritesheet.webp")
        if sheet.is_file():
            pets.append((folder.name, meta, sheet))
    return pets


def cloud_to_local(home: Path) -> dict:
    """Codex uploads a local pet as a cloud pet and keeps the pairing in its
    global state (`migrated-cloud-pet-ids-v1`: account -> {custom:<folder>: pet_<id>}).
    Returns {pet_<id>: <folder>} over every account."""
    try:
        state = json.loads((home / ".codex-global-state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    atoms = state.get("electron-persisted-atom-state", state)
    pairs = {}
    for per_account in (atoms.get("migrated-cloud-pet-ids-v1") or {}).values():
        for local, cloud in (per_account or {}).items():
            if isinstance(cloud, str) and local.startswith("custom:"):
                pairs[cloud] = local[len("custom:"):]
    return pairs


CLOUD_PETS = Path.home() / ".codex-pet" / "cloud-pets"  # ours, not Codex's


def _read_shared(path: Path) -> bytes:
    """Reads a file another process holds open. Chromium keeps its cache index open
    without FILE_SHARE_DELETE, which a plain open() does not ask for, so on Windows
    open it with every share flag."""
    if os.name != "nt":
        return path.read_bytes()
    import ctypes
    import msvcrt
    from ctypes import wintypes as wt
    create = ctypes.windll.kernel32.CreateFileW
    create.restype = wt.HANDLE
    create.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.LPVOID, wt.DWORD, wt.DWORD, wt.HANDLE]
    handle = create(str(path), 0x80000000, 0x7, None, 3, 0x80, None)  # GENERIC_READ, share all, OPEN_EXISTING
    if handle in (None, wt.HANDLE(-1).value):
        raise OSError(f"cannot open {path}")
    with os.fdopen(msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY), "rb") as fh:
        return fh.read()


def _codex_http_caches():
    """The HTTP cache of the Codex desktop app (Store and plain installs)."""
    local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
    roaming = Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming")
    roots = [p / "LocalCache/Roaming/Codex/web/Codex" for p in (local / "Packages").glob("OpenAI.Codex_*")]
    roots += [roaming / "Codex/web/Codex", roaming / "Codex"]
    return [r / "Default/Cache/Cache_Data" for r in roots if (r / "Default/Cache/Cache_Data").is_dir()]


def _cached_bodies(cache: Path, url_part: bytes):
    """Files in a Chromium blockfile cache that hold the body of a URL containing
    `url_part`. Entries are 256-byte blocks after an 8 KiB header in data_1; the key
    starts at byte 96 and the body's address (stream 1) at byte 60, where
    0x80000000 | n means the separate file f_<n as 6 hex digits>."""
    try:
        index = _read_shared(cache / "data_1")
    except OSError:
        return []
    found = []
    at = index.find(url_part)
    while at != -1:
        start = 8192 + (at - 8192) // 256 * 256
        addr = int.from_bytes(index[start + 60:start + 64], "little")
        if addr & 0x80000000 and not (addr >> 28) & 7:
            body = cache / f"f_{addr & 0x0FFFFFFF:06x}"
            if body.is_file():
                found.append(body)
        at = index.find(url_part, at + 1)
    return found


def cloud_pet(home: Path, pet_id: str):
    """A cloud pet has no folder under ~/.codex/pets: the Codex app downloads its
    spritesheet into its HTTP cache. Copies it into ~/.codex-pet/cloud-pets/<pet_id>
    as an ordinary pet folder and returns that folder's name, or None."""
    folder = CLOUD_PETS / pet_id
    if (folder / "pet.json").is_file():
        return pet_id
    try:
        state = json.loads((home / ".codex-global-state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    atoms = state.get("electron-persisted-atom-state", state)
    entry = next((p for p in (atoms.get("cloud-pet-artwork-cache") or {}).get("pets") or []
                  if isinstance(p, dict) and p.get("id") == pet_id), None)
    match = re.search(r"/files/([^/?]+)/", (entry or {}).get("spritesheetUrl") or "")
    if not match:
        return None
    url_part = f"/files/{match.group(1)}/".encode()
    bodies = [b for cache in _codex_http_caches() for b in _cached_bodies(cache, url_part)]
    for body in sorted(bodies, key=lambda b: b.stat().st_mtime, reverse=True):
        try:
            data = body.read_bytes()
        except OSError:
            continue
        ext = ".webp" if data[:4] == b"RIFF" and data[8:12] == b"WEBP" else ".png" if data[:4] == b"\x89PNG" else None
        if not ext:
            continue
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"spritesheet{ext}").write_bytes(data)
        meta = {"id": pet_id, "displayName": "Codex 雲端寵物", "spritesheetPath": f"spritesheet{ext}",
                "spriteVersionNumber": entry.get("spriteVersionNumber")}
        (folder / "pet.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        return pet_id
    return None


def all_pets(home: Path):
    """Codex's local pets, then the cloud pets copied out of its cache."""
    return list_pets(home / "pets") + list_pets(CLOUD_PETS)


_selected_cache = {}


def codex_selected(home: Path):
    """Codex's `selected-avatar-id`: `custom:<folder>` for a local pet, `pet_<id>`
    for a cloud one. Returns (pet folder name or None, the raw selection or None).
    A cloud pet resolves to the local pet it was uploaded from, else to its copy
    in ~/.codex-pet/cloud-pets. The overlay asks twice a second, so the answer is
    kept until config.toml or the global state changes."""
    files = (home / "config.toml", home / ".codex-global-state.json")
    try:
        stamp = tuple(f.stat().st_mtime for f in files if f.exists())
    except OSError:
        stamp = None
    if (stamp is not None and _selected_cache.get("stamp") == (str(home), stamp)
            and time.time() < _selected_cache.get("until", 0)):
        return _selected_cache["value"]
    value = _codex_selected(home)
    # An unresolved cloud pet may reach Codex's cache later: look again in 30 s.
    until = float("inf") if value[0] or not value[1] else time.time() + 30
    _selected_cache.update(stamp=(str(home), stamp), value=value, until=until)
    return value


def _codex_selected(home: Path):
    try:
        text = (home / "config.toml").read_text(encoding="utf-8")
    except OSError:
        return None, None
    match = re.search(r'^selected-avatar-id\s*=\s*"([^"]+)"', text, re.M)
    if not match:
        return None, None
    raw = match.group(1)
    if raw.startswith("custom:"):
        return raw[len("custom:"):], raw
    return cloud_to_local(home).get(raw) or cloud_pet(home, raw), raw


def frame_is_empty(cell) -> bool:
    return cell.getchannel("A").getbbox() is None


def bake_state(sheet, row, durations, scale):
    frames = len(durations)
    # Trust the contract, but drop trailing transparent cells of hand-made sheets.
    while frames > 1 and frame_is_empty(
        sheet.crop(((frames - 1) * CELL_W, row * CELL_H, frames * CELL_W, (row + 1) * CELL_H))
    ):
        frames -= 1
    durations = durations[:frames]
    w, h = round(CELL_W * scale), round(CELL_H * scale)
    images = []
    for col in range(frames):
        cell = sheet.crop((col * CELL_W, row * CELL_H, (col + 1) * CELL_W, (row + 1) * CELL_H))
        out = io.BytesIO()
        cell.resize((w, h), Image.LANCZOS).save(out, "WEBP", quality=82, method=4)
        images.append("data:image/webp;base64," + base64.b64encode(out.getvalue()).decode("ascii"))
    return {"frames": images, "durations": durations, "w": w, "h": h}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pet", default="auto", help="pet folder id, or auto")
    parser.add_argument("--scale", type=float, default=0.5)
    args = parser.parse_args()

    home = codex_home()
    selection = None
    if args.pet == "auto":
        wanted, selection = codex_selected(home)
    else:
        wanted = (args.pet.startswith("pet_") and cloud_pet(home, args.pet)) or args.pet
    pets = all_pets(home)
    available = [{"id": pid, "displayName": meta.get("displayName") or pid} for pid, meta, _ in pets]
    if not pets:
        print(json.dumps({"ok": False, "error": f"no pets in {home / 'pets'}", "available": []}))
        return

    by_id = {pid: (pid, meta, sheet) for pid, meta, sheet in pets}
    pid, meta, sheet_path = by_id.get(wanted) or pets[0]
    note = None
    if selection and wanted != pid:
        note = f"Codex 選的寵物 {selection} 在本機沒有 spritesheet，先用 {pid}"

    sheet = Image.open(sheet_path).convert("RGBA")
    rows = sheet.height // CELL_H
    states = {
        name: bake_state(sheet, row, durations, args.scale)
        for row, (name, durations) in enumerate(STATES)
        if row < rows
    }
    print(json.dumps({
        "ok": True,
        "pet": {
            "id": pid,
            "displayName": meta.get("displayName") or pid,
            "description": meta.get("description", ""),
            "followsCodex": args.pet == "auto" and wanted == pid,
            "codexSelection": selection,
        },
        "note": note,
        "available": available,
        "states": states,
    }))


if __name__ == "__main__":
    try:
        from PIL import Image
    except ImportError:
        print(json.dumps({"ok": False, "error": "Pillow is not installed for this Python (pip install pillow)", "available": []}))
        sys.exit(0)
    main()
