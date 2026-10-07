"""A desktop pet shared by Claude Code and Codex, modelled on the Codex pet.

One always-on-top, per-pixel-transparent window (a Win32 layered window
whose whole picture, pet and activity cards, is drawn with Pillow) that:

- shows a Codex pet (~/.codex/pets) and animates it with the most urgent
  conversation across both agents (needs input > blocked > ready > running);
- keeps an activity stack: one card per Claude Code session or Codex thread
  with its title, status and what it is doing; hovering the pet expands it;
- opens the conversation when a card is clicked (claude:// and codex://
  deep links) and clears a finished card once it has been opened;
- has a badge for what needs you, a right-click menu (size, activity, wave,
  reload, close), Win+Alt+O to show or hide, a first-time greeting, and
  still frames when Windows' animation effects are off.

Only one instance runs; a newer version takes over from an older one.
"""

import ctypes
import ctypes.wintypes as wt
import json
import os
import re
import sys
import time
import tkinter as tk
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "bake"))
sys.path.insert(0, str(HERE))
import bake_pet  # noqa: E402  (pet folders, Codex's selection, cloud ids)
import sources  # noqa: E402

VERSION = 4
SHARED = sources.SHARED
PREFS = SHARED / "overlay.json"
LOCK = SHARED / "overlay.lock"
TAKEOVER = SHARED / "takeover"

CELL_W, CELL_H = bake_pet.CELL_W, bake_pet.CELL_H
ROW = {name: row for row, (name, _) in enumerate(bake_pet.STATES)}
DURATIONS = dict(bake_pet.STATES)

# The Codex pet's order when several conversations have activity.
ORDER = {"waiting": 0, "blocked": 1, "ready": 2, "running": 3, "idle": 9}
STATUS_TEXT = {"waiting": "需要你", "blocked": "卡住了", "ready": "完成", "running": "工作中", "idle": "待命"}
# Light surfaces like the Codex pet's (--color-surface, 18px corners, soft elevation).
STATUS_COLOR = {
    "waiting": (204, 122, 0), "blocked": (214, 48, 64), "ready": (22, 140, 74),
    "running": (24, 104, 219), "idle": (140, 140, 146),
}
SURFACE = (255, 255, 255, 252)
SURFACE_HOVER = (244, 244, 246, 252)
HAIRLINE = (0, 0, 0, 22)
TEXT = (24, 24, 27)
TEXT_SECONDARY = (110, 110, 118)
AGENT_TEXT = {"cc": "CC", "codex": "Codex"}
AGENT_COLOR = {"cc": (217, 119, 87), "codex": (120, 132, 150)}
SIZES = {"小": 0.45, "中": 0.62, "大": 0.8}
MAX_CARDS = 6
DONE_JUMP_S = 2.5

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
GWL_EXSTYLE = -20
WS_EX_LAYERED, WS_EX_TOOLWINDOW = 0x00080000, 0x00000080
ULW_ALPHA, AC_SRC_ALPHA = 2, 1
SPI_GETCLIENTAREAANIMATION = 0x1042
VK_LWIN, VK_RWIN, VK_MENU, VK_O = 0x5B, 0x5C, 0x12, 0x4F


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


user32.GetDC.restype = wt.HDC
user32.GetDC.argtypes = [wt.HWND]
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.GetParent.restype = wt.HWND
user32.GetParent.argtypes = [wt.HWND]
user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
user32.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, wt.LONG]
user32.UpdateLayeredWindow.argtypes = [
    wt.HWND, wt.HDC, ctypes.POINTER(wt.POINT), ctypes.POINTER(wt.SIZE), wt.HDC,
    ctypes.POINTER(wt.POINT), wt.COLORREF, ctypes.POINTER(BLENDFUNCTION), wt.DWORD]
user32.GetAsyncKeyState.restype = ctypes.c_short
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateDIBSection.restype = wt.HBITMAP
gdi32.CreateDIBSection.argtypes = [wt.HDC, ctypes.c_void_p, wt.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]
kernel32.CreateMutexW.restype = wt.HANDLE
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
kernel32.ReleaseMutex.argtypes = [wt.HANDLE]
kernel32.CloseHandle.argtypes = [wt.HANDLE]


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def claim_instance():
    """Holds the named mutex; asks an older running version to step aside."""
    for _ in range(40):
        handle = kernel32.CreateMutexW(None, True, "Local\\codex-pet-overlay")
        if ctypes.get_last_error() != 183:  # not ERROR_ALREADY_EXISTS
            # A request to close (/pet overlay off) or step aside was for the pet that
            # ran before; left in place it would close this one at its first poll.
            TAKEOVER.unlink(missing_ok=True)
            LOCK.write_text(json.dumps({"pid": os.getpid(), "version": VERSION}), encoding="utf-8")
            return handle
        kernel32.CloseHandle(handle)
        running = (read_json(LOCK) or {}).get("version", 1)
        if running >= VERSION:
            return None
        TAKEOVER.write_text(str(VERSION), encoding="utf-8")
        time.sleep(0.25)
    return None


def animations_on() -> bool:
    flag = wt.BOOL(True)
    user32.SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(flag), 0)
    return bool(flag.value)


def elapsed(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return "剛剛"
    if s < 3600:
        return f"{s // 60} 分鐘"
    return f"{s // 3600} 小時"


class Overlay:
    def __init__(self):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            pass
        self.dpi = user32.GetDpiForSystem() / 96 if hasattr(user32, "GetDpiForSystem") else 1.0
        self.prefs = read_json(PREFS) or {}
        self.home = bake_pet.codex_home()
        self.cc = sources.ClaudeCodeSource()
        self.codex = sources.CodexSource(self.home)
        self.rows = []
        self.cards = []  # what the stack shows, most urgent first
        self.hits = []  # (rect, kind, activity) in window coordinates
        self.pet_id, self.pet_name, self.frames = None, "", {}
        self.mood, self.frame = "waving", 0
        self.wave_until = time.monotonic() + 2.0
        self.greet_until = 0.0
        self.hover = None
        self.hover_seen = 0.0
        self.is_open = False  # the stack expanded
        self.is_hidden = False
        self.drag, self.drag_dir, self.dragged = None, None, False
        self.hotkey_down = False
        self.animate = animations_on()
        self.origin = (0, 0)
        self.size = (1, 1)
        self.card_cache = {}

        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.geometry("1x1+0+0")
        self.root.update_idletasks()
        self.hwnd = user32.GetParent(self.root.winfo_id())
        style = user32.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(self.hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED | WS_EX_TOOLWINDOW)

        self.root.bind("<ButtonPress-1>", self.on_press)
        self.root.bind("<B1-Motion>", self.on_drag)
        self.root.bind("<ButtonRelease-1>", self.on_release)
        self.root.bind("<Double-Button-1>", self.on_double)
        self.root.bind("<Button-3>", self.on_menu)

        self.set_size(self.prefs.get("size", "中"), save=False)
        self.load_pet()
        sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        # The pet's bottom-right corner; the window grows from it.
        self.anchor = self.prefs.get("anchor") or [sw - int(260 * self.dpi), sh - int(100 * self.dpi)]
        self.poll()
        self.watch_pointer()
        self.tick()

    # ---- look ----------------------------------------------------------
    def font(self, size, bold=False):
        names = ("msjhbd.ttc", "msjh.ttc") if bold else ("msjh.ttc", "mingliu.ttc")
        for name in names + ("segoeui.ttf",):
            try:
                return ImageFont.truetype(str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / name), size)
            except OSError:
                continue
        return ImageFont.load_default()

    def set_size(self, name, save=True):
        self.size_name = name if name in SIZES else "中"
        self.scale = SIZES[self.size_name] * self.dpi
        u = self.dpi
        self.f_title = self.font(int(12.5 * u), bold=True)
        self.f_text = self.font(int(11 * u))
        self.f_small = self.font(int(9.5 * u), bold=True)
        self.card_w = int(300 * u)
        self.card_cache.clear()
        if self.pet_id:
            self.load_pet(force=True)
        if save:
            self.save_prefs(size=self.size_name)

    def wanted_pet(self):
        for row in self.rows:
            if row.agent == "cc" and row.pet_id:
                return row.pet_id
        local, _ = bake_pet.codex_selected(self.home)
        return local

    def load_pet(self, force=False):
        pets = bake_pet.list_pets(self.home / "pets")
        if not pets:
            return
        by_id = {pid: (meta, sheet) for pid, meta, sheet in pets}
        pid = self.wanted_pet()
        if pid not in by_id:
            pid = pets[0][0]
        if pid == self.pet_id and not force:
            return
        meta, path = by_id[pid]
        sheet = Image.open(path).convert("RGBA")
        w, h = round(CELL_W * self.scale), round(CELL_H * self.scale)
        frames = {}
        for name, durations in bake_pet.STATES:
            row = ROW[name]
            if (row + 1) * CELL_H > sheet.height:
                continue
            cells = []
            for col in range(len(durations)):
                cell = sheet.crop((col * CELL_W, row * CELL_H, (col + 1) * CELL_W, (row + 1) * CELL_H))
                if col and cell.getchannel("A").getbbox() is None:
                    break
                cells.append(cell.resize((w, h), Image.LANCZOS))
            frames[name] = cells
        # Crop every frame to the box all of them fill, so the badge and the
        # cards sit against the pet rather than its empty cell margins.
        box = None
        for cells in frames.values():
            for cell in cells:
                b = cell.getchannel("A").getbbox()
                if b:
                    box = b if box is None else (min(box[0], b[0]), min(box[1], b[1]), max(box[2], b[2]), max(box[3], b[3]))
        if box:
            frames = {k: [c.crop(box) for c in v] for k, v in frames.items()}
            w, h = box[2] - box[0], box[3] - box[1]
        first = self.pet_id is None
        self.pet_id, self.frames, self.cell = pid, frames, (w, h)
        name = meta.get("displayName") or pid
        inner = re.search(r"[（(]([^）)]+)[）)]", name)
        self.pet_name = (inner.group(1) if inner else name).strip()
        greeted = set(self.prefs.get("greeted", []))
        if pid not in greeted:
            self.greet_until = time.monotonic() + 6
            self.wave_until = time.monotonic() + 2.5
            self.save_prefs(greeted=sorted(greeted | {pid}))
        elif not first:
            self.wave_until = time.monotonic() + 2.0

    # ---- state ---------------------------------------------------------
    def poll(self):
        if TAKEOVER.exists():
            try:
                TAKEOVER.unlink()
            except OSError:
                pass
            self.root.destroy()
            return
        self.rows = self.cc.poll() + self.codex.poll()
        dismissed = self.prefs.get("dismissed", {})
        cards = [r for r in self.rows if r.status != "idle"
                 and not (r.status in ("ready", "blocked") and dismissed.get(r.key, 0) >= r.status_at)]
        cards.sort(key=lambda r: (ORDER.get(r.status, 9), -r.status_at))
        self.cards = cards
        if self.wanted_pet() not in (None, self.pet_id):
            self.load_pet()
        self.root.after(500, self.poll)

    def mascot(self):
        if self.drag_dir:
            return self.drag_dir
        now = time.monotonic()
        if now < self.wave_until:
            return "waving"
        top = self.cards[0] if self.cards else None
        if top is None:
            return "idle"
        if top.status == "ready":
            return "jumping" if time.time() - top.status_at < DONE_JUMP_S else "review"
        return {"waiting": "waiting", "blocked": "failed", "running": "running"}.get(top.status, "idle")

    def dismiss(self, row):
        dismissed = dict(self.prefs.get("dismissed", {}))
        dismissed[row.key] = row.status_at
        cutoff = time.time() - 2 * 86400
        self.save_prefs(dismissed={k: v for k, v in dismissed.items() if v >= cutoff})
        self.cards = [c for c in self.cards if c.key != row.key]

    def open(self, row):
        try:
            os.startfile(row.url)
        except OSError:
            pass
        if row.status in ("ready", "blocked"):
            self.dismiss(row)

    # ---- drawing -------------------------------------------------------
    def panel(self, w, h, hovered=False, radius=18):
        """A white rounded surface with a soft shadow; content goes at (m, m)."""
        u = self.dpi
        m = int(8 * u)
        r = int(radius * u)
        img = Image.new("RGBA", (w + 2 * m, h + 2 * m), (0, 0, 0, 0))
        shadow = Image.new("L", img.size, 0)
        ImageDraw.Draw(shadow).rounded_rectangle((m, m + int(2 * u), m + w, m + h + int(2 * u)), radius=r, fill=60)
        shadow = shadow.filter(ImageFilter.GaussianBlur(int(5 * u)))
        img.putalpha(shadow)
        ImageDraw.Draw(img).rounded_rectangle((m, m, m + w - 1, m + h - 1), radius=r,
                                              fill=SURFACE_HOVER if hovered else SURFACE, outline=HAIRLINE)
        return img, m

    def draw_card(self, row, hovered):
        key = (row.key, row.status, row.title, row.action, elapsed(time.time() - row.status_at),
               hovered, self.size_name)
        if key in self.card_cache:
            return self.card_cache[key]
        u, w = self.dpi, self.card_w
        pad = int(10 * u)
        line1, line2 = int(18 * u), int(16 * u)
        h = pad * 2 + line1 + line2
        img, m = self.panel(w, h, hovered)
        d = ImageDraw.Draw(img)
        x, y = m + pad, m + pad
        color = STATUS_COLOR.get(row.status, STATUS_COLOR["idle"])
        r = int(4 * u)
        d.ellipse((x, y + line1 // 2 - r, x + 2 * r, y + line1 // 2 + r), fill=color)
        x += 2 * r + int(7 * u)
        tag = AGENT_TEXT[row.agent]
        tb = d.textbbox((0, 0), tag, font=self.f_small)
        tw = tb[2] - tb[0] + int(10 * u)
        d.rounded_rectangle((x, y + 1, x + tw, y + line1 - 1), radius=int(6 * u), fill=AGENT_COLOR[row.agent])
        d.text((x + int(5 * u) - tb[0], y + (line1 - (tb[3] - tb[1])) // 2 - tb[1]), tag, font=self.f_small,
               fill=(255, 255, 255))
        x += tw + int(7 * u)
        close = int(16 * u) if row.status in ("ready", "blocked") else 0
        d.text((x, y - int(1 * u)), self.fit(d, row.title, self.f_title, m + w - x - pad - close), font=self.f_title,
               fill=TEXT)
        if close:
            cx = m + w - pad - close // 2
            cy = y + line1 // 2
            k = int(4 * u)
            d.line((cx - k, cy - k, cx + k, cy + k), fill=TEXT_SECONDARY, width=max(1, int(1.5 * u)))
            d.line((cx - k, cy + k, cx + k, cy - k), fill=TEXT_SECONDARY, width=max(1, int(1.5 * u)))
        y += line1 + int(2 * u)
        status = STATUS_TEXT.get(row.status, row.status)
        d.text((m + pad, y), status, font=self.f_text, fill=color)
        sx = m + pad + d.textlength(status, font=self.f_text)
        rest = " · ".join(p for p in (row.action, elapsed(time.time() - row.status_at)) if p)
        d.text((sx, y), "  " + self.fit(d, rest, self.f_text, m + w - sx - pad - int(8 * u)), font=self.f_text,
               fill=TEXT_SECONDARY)
        out = (img, close and (m + w - pad - close, m + pad, m + w - pad, m + pad + line1))
        if len(self.card_cache) > 200:
            self.card_cache.clear()
        self.card_cache[key] = out
        return out

    @staticmethod
    def fit(d, text, font, width):
        if d.textlength(text, font=font) <= width:
            return text
        while text and d.textlength(text + "…", font=font) > width:
            text = text[:-1]
        return text + "…"

    def bubble(self, text, sub):
        u = self.dpi
        pad = int(10 * u)
        w = int(240 * u)
        h = pad * 2 + int(40 * u)
        img, m = self.panel(w, h)
        d = ImageDraw.Draw(img)
        d.text((m + pad, m + pad - int(2 * u)), text, font=self.f_title, fill=TEXT)
        d.text((m + pad, m + pad + int(20 * u)), sub, font=self.f_text, fill=TEXT_SECONDARY)
        return img

    def compose(self, cell):
        """The whole window: cards (or the top one) above the pet, the badge on it."""
        u = self.dpi
        gap = int(-4 * u)  # panels already carry a shadow margin
        pw, ph = cell.size
        show = self.cards[:MAX_CARDS] if self.is_open else self.cards[:1]
        pieces = []
        if time.monotonic() < self.greet_until:
            pieces.append(("bubble", self.bubble(f"嗨，我是 {self.pet_name}", "我會幫你盯著 CC 和 Codex 的對話"), None))
        for row in show:
            img, close = self.draw_card(row, self.hover == ("card", row.key))
            pieces.append(("card", img, (row, close)))
        if self.is_open and len(self.cards) > MAX_CARDS:
            label = f"還有 {len(self.cards) - MAX_CARDS} 項"
            lw = int(ImageDraw.Draw(Image.new("L", (1, 1))).textlength(label, font=self.f_text)) + int(20 * u)
            more, m = self.panel(lw, int(24 * u), radius=12)
            ImageDraw.Draw(more).text((m + int(10 * u), m + int(4 * u)), label, font=self.f_text, fill=TEXT_SECONDARY)
            pieces.append(("more", more, None))
        stack_h = sum(p[1].size[1] + gap for p in pieces) + (int(4 * u) if pieces else 0)
        width = max(pw, max((p[1].size[0] for p in pieces), default=0))
        height = stack_h + ph
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        if self.is_open and pieces:
            # A barely visible backdrop keeps the pointer over the stack between cards.
            ImageDraw.Draw(canvas).rectangle((0, 0, width, stack_h), fill=(0, 0, 0, 1))
        right = self.anchor[0] > user32.GetSystemMetrics(0) // 2
        hits = []
        y = 0
        for kind, img, data in pieces:
            x = width - img.size[0] if right else 0
            canvas.alpha_composite(img, (x, y))
            if kind == "card":
                row, close = data
                hits.append(((x, y, x + img.size[0], y + img.size[1]), "card", row))
                if close:
                    cx0, cy0, cx1, cy1 = close
                    hits.insert(0, ((x + cx0 - 4, y + cy0 - 4, x + cx1 + 4, y + cy1 + 4), "close", row))
            y += img.size[1] + gap
        px = width - pw if right else 0
        canvas.alpha_composite(cell, (px, stack_h))
        hits.append(((px, stack_h, px + pw, stack_h + ph), "pet", None))
        need = [c for c in self.cards if c.status in ("waiting", "blocked", "ready")]
        if need:
            r = int(10 * u)
            bx, by = px + pw - r * 2 - int(4 * u), stack_h + int(4 * u)
            d = ImageDraw.Draw(canvas)
            d.ellipse((bx, by, bx + 2 * r, by + 2 * r), fill=STATUS_COLOR[need[0].status] + (255,),
                      outline=(255, 255, 255, 255), width=max(1, int(1.5 * u)))
            n = str(len(need)) if len(need) < 10 else "9+"
            tb = d.textbbox((0, 0), n, font=self.f_small)
            d.text((bx + r - (tb[2] - tb[0]) // 2 - tb[0], by + r - (tb[3] - tb[1]) // 2 - tb[1]), n,
                   font=self.f_small, fill=(255, 255, 255, 255))
        self.hits = hits
        # Window origin: the pet's bottom-right stays at the anchor.
        ox = self.anchor[0] - (px + pw)
        oy = self.anchor[1] - height
        return canvas, (ox, oy)

    def blit(self, img, origin):
        r, g, b, a = img.split()
        bgra = Image.merge("RGBA", (ImageChops.multiply(b, a), ImageChops.multiply(g, a),
                                    ImageChops.multiply(r, a), a)).tobytes()
        w, h = img.size
        if (w, h) != self.size or origin != self.origin:
            # Keep Tk's own window over the picture so it receives the pointer there.
            self.root.geometry(f"{w}x{h}+{origin[0]}+{origin[1]}")
        screen = user32.GetDC(None)
        mem = gdi32.CreateCompatibleDC(screen)
        info = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        bits = ctypes.c_void_p()
        bmp = gdi32.CreateDIBSection(mem, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
        ctypes.memmove(bits, bgra, len(bgra))
        old = gdi32.SelectObject(mem, bmp)
        blend = BLENDFUNCTION(0, 0, 255, AC_SRC_ALPHA)
        user32.UpdateLayeredWindow(self.hwnd, screen, ctypes.byref(wt.POINT(*origin)),
                                   ctypes.byref(wt.SIZE(w, h)), mem, ctypes.byref(wt.POINT(0, 0)),
                                   0, ctypes.byref(blend), ULW_ALPHA)
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(None, screen)
        self.size, self.origin = (w, h), origin

    def redraw(self):
        if self.is_hidden:
            self.blit(Image.new("RGBA", (1, 1), (0, 0, 0, 0)), tuple(self.anchor))
            return
        cells = self.frames.get(self.mood) or self.frames.get("idle") or []
        if cells:
            self.blit(*self.compose(cells[self.frame % len(cells)]))

    def tick(self):
        mood = self.mascot()
        if mood != self.mood:
            self.mood, self.frame = mood, 0
        cells = self.frames.get(mood) or self.frames.get("idle") or []
        wait = 250
        if cells and not self.is_hidden:
            if self.animate:
                self.frame %= len(cells)
                wait = DURATIONS.get(mood, [150] * 8)[self.frame]
            else:
                self.frame, wait = 0, 400  # still frames, like the Codex pet with reduced motion
            self.redraw()
            if self.animate:
                self.frame += 1
        self.root.after(wait, self.tick)

    # ---- pointer -------------------------------------------------------
    def hit(self, x, y):
        for (x0, y0, x1, y1), kind, row in self.hits:
            if x0 <= x < x1 and y0 <= y < y1:
                return kind, row
        return None, None

    def watch_pointer(self):
        """Hover expands the stack; leaving it collapses after a moment. Also the hotkey."""
        pt = wt.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        x, y = pt.x - self.origin[0], pt.y - self.origin[1]
        kind, row = self.hit(x, y) if not self.is_hidden else (None, None)
        over = kind is not None or (self.is_open and 0 <= x < self.size[0] and 0 <= y < self.size[1])
        now = time.monotonic()
        if over:
            self.hover_seen = now
            if not self.is_open and not self.drag and self.cards:
                self.is_open = True
                self.redraw()
        elif self.is_open and not self.prefs.get("pinned") and now - self.hover_seen > 0.6:
            self.is_open = False
            self.redraw()
        hovering = (kind, row.key) if kind == "card" and row else None
        if hovering != self.hover:
            self.hover = hovering
            self.redraw()
        win = (user32.GetAsyncKeyState(VK_LWIN) | user32.GetAsyncKeyState(VK_RWIN)) & 0x8000
        combo = bool(win and user32.GetAsyncKeyState(VK_MENU) & 0x8000 and user32.GetAsyncKeyState(VK_O) & 0x8000)
        if combo and not self.hotkey_down:
            self.toggle_hidden()
        self.hotkey_down = combo
        self.root.after(80, self.watch_pointer)

    def toggle_hidden(self):
        self.is_hidden = not self.is_hidden
        if not self.is_hidden:
            self.wave_until = time.monotonic() + 1.5
        self.redraw()

    def on_press(self, e):
        self.drag = (e.x_root, e.y_root, self.anchor[:], e.x_root)
        self.dragged = False

    def on_drag(self, e):
        if not self.drag:
            return
        sx, sy, start, lastx = self.drag
        if not self.dragged and abs(e.x_root - sx) + abs(e.y_root - sy) < 5:
            return
        self.dragged = True
        dx = e.x_root - lastx
        if abs(dx) > 2:
            self.drag_dir = "running-right" if dx > 0 else "running-left"
            self.drag = (sx, sy, start, e.x_root)
        self.anchor = [start[0] + e.x_root - sx, start[1] + e.y_root - sy]
        self.redraw()

    def on_release(self, e):
        was_drag = self.dragged
        self.drag, self.drag_dir, self.dragged = None, None, False
        if was_drag:
            self.save_prefs(anchor=self.anchor)
            return
        kind, row = self.hit(e.x, e.y)
        if kind == "close":
            self.dismiss(row)
        elif kind == "card":
            self.open(row)
        elif kind == "pet":
            self.is_open = not self.is_open
            self.hover_seen = time.monotonic()
        self.redraw()

    def on_double(self, e):
        kind, _ = self.hit(e.x, e.y)
        if kind == "pet":
            self.wave_until = time.monotonic() + 2.0

    def on_menu(self, e):
        menu = tk.Menu(self.root, tearoff=0)
        sizes = tk.Menu(menu, tearoff=0)
        for name in SIZES:
            sizes.add_radiobutton(label=name, value=name, variable=tk.StringVar(value=self.size_name),
                                  command=lambda n=name: (self.set_size(n), self.redraw()))
        menu.add_cascade(label="大小", menu=sizes)
        pinned = bool(self.prefs.get("pinned"))
        menu.add_command(label=("取消固定活動列表" if pinned else "固定展開活動列表"),
                         command=lambda: self.save_prefs(pinned=not pinned))
        if any(c.status in ("ready", "blocked") for c in self.cards):
            menu.add_command(label="清掉已完成的卡片", command=self.clear_done)
        menu.add_command(label="揮手", command=lambda: setattr(self, "wave_until", time.monotonic() + 2.0))
        menu.add_command(label="重新載入寵物", command=lambda: self.load_pet(force=True))
        menu.add_command(label="隱藏（Win+Alt+O 叫回來）", command=self.toggle_hidden)
        menu.add_separator()
        menu.add_command(label="關閉桌寵", command=self.root.destroy)
        menu.tk_popup(e.x_root, e.y_root)

    def clear_done(self):
        for row in [c for c in self.cards if c.status in ("ready", "blocked")]:
            self.dismiss(row)
        self.redraw()

    def save_prefs(self, **changes):
        self.prefs.update(changes)
        try:
            SHARED.mkdir(exist_ok=True)
            PREFS.write_text(json.dumps(self.prefs, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass


def log_error(kind, value, tb):
    import traceback
    try:
        with open(SHARED / "overlay-error.log", "a", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%d %H:%M:%S ") + "".join(traceback.format_exception(kind, value, tb)) + "\n")
    except OSError:
        pass


def main():
    SHARED.mkdir(exist_ok=True)
    handle = claim_instance()
    if handle is None:
        return
    try:
        overlay = Overlay()
        # pythonw has no console: errors in Tk callbacks go to a log instead.
        overlay.root.report_callback_exception = log_error
        overlay.root.mainloop()
    except Exception:
        log_error(*sys.exc_info())
        raise
    finally:
        kernel32.ReleaseMutex(handle)
        kernel32.CloseHandle(handle)


if __name__ == "__main__":
    main()
