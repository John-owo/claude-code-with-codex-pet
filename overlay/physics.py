"""The desktop pet's physics mode and size setting, as plain functions.

Positions are the pet's bottom-right corner (the overlay's anchor) in screen
pixels; speeds are pixels per second. The tuning constants are in
device-independent pixels, so callers pass `unit` (the display scale) and a
throw or a fall feels the same at every scaling.
"""

import math
from dataclasses import dataclass

GRAVITY = 2600.0  # DIP/s^2
BOUNCE = 0.6  # share of the speed kept by a bounce: each bounce reaches about a third of the last one's height
STOP_SPEED = 450.0  # a floor hit slower than this lands instead of bouncing, so a fall bounces 2 or 3 times
SLIDE_FRICTION = 2200.0  # DIP/s^2 slowing a pet that slides along the floor
FLOOR_GRIP = 0.85  # share of the sideways speed kept by each bounce
MAX_THROW = 4500.0  # DIP/s
THROW_WINDOW = 0.08  # s of pointer movement before the release that set the throw
STILL_AFTER = 0.06  # s: a pointer held this long before the release throws nothing

SIZE_MIN, SIZE_MAX, SIZE_DEFAULT = 30, 300, 100
SIZE_PRESETS = {"小": 75, "中": 100, "大": 130}
# Before sizes were percentages, prefs held these names for these scales.
LEGACY_SIZES = {"小": 0.45, "中": 0.62, "大": 0.8}
BASE_SCALE = LEGACY_SIZES["中"]  # 100%: the spritesheet cell at this scale


@dataclass(frozen=True)
class Bounds:
    """Where the pet may go: walls, a ceiling and the floor it lands on.

    A side with another monitor beyond it is open (an infinite value).
    """
    left: float
    top: float
    right: float
    floor: float


def parse_size(value) -> int:
    """A size from prefs (a percentage, or an old 小/中/大) as a percentage in range."""
    if isinstance(value, str) and value in LEGACY_SIZES:
        return round(LEGACY_SIZES[value] / BASE_SCALE * 100)
    try:
        pct = float(value)
    except (TypeError, ValueError):
        return SIZE_DEFAULT
    if not math.isfinite(pct):
        return SIZE_DEFAULT
    return int(min(SIZE_MAX, max(SIZE_MIN, round(pct))))


def scale_for(pct: int) -> float:
    """The spritesheet scale for a size percentage, before the display scale."""
    return BASE_SCALE * pct / 100


def throw_velocity(samples, released_at, unit=1.0):
    """The pointer's velocity as the pet was let go, from (time, x, y) drag samples.

    Uses the movement in the last THROW_WINDOW seconds; a pointer that stopped
    before letting go throws nothing. Capped at MAX_THROW.
    """
    if not samples or released_at - samples[-1][0] > STILL_AFTER:
        return 0.0, 0.0
    recent = [s for s in samples if released_at - s[0] <= THROW_WINDOW]
    if len(recent) < 2:
        return 0.0, 0.0
    (t0, x0, y0), (t1, x1, y1) = recent[0], recent[-1]
    dt = t1 - t0
    if dt <= 0:
        return 0.0, 0.0
    vx, vy = (x1 - x0) / dt, (y1 - y0) / dt
    speed, top = math.hypot(vx, vy), MAX_THROW * unit
    if speed > top:
        vx, vy = vx * top / speed, vy * top / speed
    return vx, vy


def step(anchor, velocity, size, bounds: Bounds, dt, unit=1.0):
    """Advances the pet by `dt` seconds.

    Returns (anchor, velocity, resting); `resting` is true once it lies still
    on the floor. Only the pet's own box (`size`) collides, not its cards.
    """
    (x, y), (vx, vy), (w, h) = anchor, velocity, size
    vy += GRAVITY * unit * dt
    x += vx * dt
    y += vy * dt
    if x - w < bounds.left:
        x, vx = bounds.left + w, abs(vx) * BOUNCE
    elif x > bounds.right:
        x, vx = bounds.right, -abs(vx) * BOUNCE
    if y - h < bounds.top:
        y, vy = bounds.top + h, abs(vy) * BOUNCE
    if y >= bounds.floor:
        y = bounds.floor
        if vy > STOP_SPEED * unit:
            vy, vx = -vy * BOUNCE, vx * FLOOR_GRIP
        else:
            vy = 0.0
            slow = SLIDE_FRICTION * unit * dt
            vx = 0.0 if abs(vx) <= slow else vx - math.copysign(slow, vx)
    resting = y == bounds.floor and vx == 0.0 and vy == 0.0
    return (x, y), (vx, vy), resting


def land(anchor, size, bounds: Bounds):
    """Where the pet comes to rest with no motion: on the floor, inside the walls."""
    x, _ = anchor
    w, _ = size
    if x - w < bounds.left:
        x = bounds.left + w
    elif x > bounds.right:
        x = bounds.right
    return x, bounds.floor


def settled(anchor, size, bounds: Bounds) -> bool:
    """Whether the pet already lies where `land` would put it (to the pixel)."""
    x, y = land(anchor, size, bounds)
    return abs(x - anchor[0]) < 1 and abs(y - anchor[1]) < 1
