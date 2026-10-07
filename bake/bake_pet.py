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


def codex_selected(home: Path):
    """Codex's `selected-avatar-id`: `custom:<folder>` for a local pet, `pet_<id>`
    for a cloud one. Returns (local folder or None, the raw selection or None)."""
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
    return cloud_to_local(home).get(raw), raw


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
    pets = list_pets(home / "pets")
    available = [{"id": pid, "displayName": meta.get("displayName") or pid} for pid, meta, _ in pets]
    if not pets:
        print(json.dumps({"ok": False, "error": f"no pets in {home / 'pets'}", "available": []}))
        return

    by_id = {pid: (pid, meta, sheet) for pid, meta, sheet in pets}
    selection = None
    if args.pet == "auto":
        wanted, selection = codex_selected(home)
    else:
        wanted = args.pet
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
