"""
keys.py  —  Unified keyboard & mouse input module
=================================================
Backends (in priority order):
  1. Interception  — low-level driver, bypasses LLKHF_INJECTED flag
  2. Win32          — win32api.keybd_event / mouse_event
  3. keyboard lib   — pure-Python fallback (needs Admin)

Exports consumed by Automation_bot.py
--------------------------------------
  INTERCEPTION_OK, WIN32_OK, KEYBOARD_OK, INTERCEPTION_ERR
  backend_status, set_kb_backend, set_mouse_backend
  get_kb_backend, get_mouse_backend
  kb_down, kb_up, kb_press, kb_hold, kb_combo
  kb_toggleLoop, kb_toggleHold
  stop_all_loops, release_all_held, cleanup
  key_down, key_up, key_tap, key_combo        (legacy aliases)
  keyboard_press                               (legacy script API)
  mouse_pos, mouse_move, mouse_smooth, mouse_human
  mouse_down, mouse_up, mouse_click, click_at_pos
  get_pos, human_move                          (legacy aliases)

Notes
-----
  mouse_smooth  — cosine-eased, works on all backends
  mouse_human   — raw-input VIRTUALDESK move (best for games), Win32 only
  mouse_click / click_at_pos accept humanize=True (or 'h') to use mouse_human
  resolve, lookup, KEY_MAP, ALIASES
"""

from __future__ import annotations
import atexit
import ctypes
import math
import random
import threading
import time
from typing import Callable, Optional

# Enable 1ms timer resolution on Windows for accurate sleep/movement timing
try:
    ctypes.windll.winmm.timeBeginPeriod(1)
    atexit.register(lambda: ctypes.windll.winmm.timeEndPeriod(1))
except Exception:
    pass

# ─────────────────────────────────────────────────────────────────────────────
# 1.  Key-name → Win32 virtual-key-code mapping (canonical names)
# ─────────────────────────────────────────────────────────────────────────────

KEY_MAP: dict[str, int] = {
    # ── Letters ──────────────────────────────────────────────────────────────
    "a": 0x41, "b": 0x42, "c": 0x43, "d": 0x44, "e": 0x45,
    "f": 0x46, "g": 0x47, "h": 0x48, "i": 0x49, "j": 0x4A,
    "k": 0x4B, "l": 0x4C, "m": 0x4D, "n": 0x4E, "o": 0x4F,
    "p": 0x50, "q": 0x51, "r": 0x52, "s": 0x53, "t": 0x54,
    "u": 0x55, "v": 0x56, "w": 0x57, "x": 0x58, "y": 0x59,
    "z": 0x5A,
    # ── Main-row digits ──────────────────────────────────────────────────────
    "0": 0x30, "1": 0x31, "2": 0x32, "3": 0x33, "4": 0x34,
    "5": 0x35, "6": 0x36, "7": 0x37, "8": 0x38, "9": 0x39,
    # ── Whitespace / Editing ─────────────────────────────────────────────────
    "space":     0x20,
    "backspace":  0x08,
    "tab":        0x09,
    "enter":      0x0D,
    "escape":     0x1B,
    "delete":     0x2E,
    "insert":     0x2D,
    # ── Navigation ───────────────────────────────────────────────────────────
    "home":      0x24,
    "end":       0x23,
    "page up":   0x21,
    "page down": 0x22,
    "left":      0x25,
    "up":        0x26,
    "right":     0x27,
    "down":      0x28,
    # ── Modifiers ────────────────────────────────────────────────────────────
    "shift":       0x10,
    "left shift":  0xA0,
    "right shift": 0xA1,
    "ctrl":        0x11,
    "left ctrl":   0xA2,
    "right ctrl":  0xA3,
    "alt":         0x12,
    "left alt":    0xA4,
    "right alt":   0xA5,
    "windows":     0x5B,
    "right windows": 0x5C,
    "menu":        0x5D,
    "caps lock":   0x14,
    "num lock":    0x90,
    "scroll lock": 0x91,
    # ── Function keys ────────────────────────────────────────────────────────
    "f1":  0x70, "f2":  0x71, "f3":  0x72, "f4":  0x73,
    "f5":  0x74, "f6":  0x75, "f7":  0x76, "f8":  0x77,
    "f9":  0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
    "f13": 0x7C, "f14": 0x7D, "f15": 0x7E, "f16": 0x7F,
    "f17": 0x80, "f18": 0x81, "f19": 0x82, "f20": 0x83,
    "f21": 0x84, "f22": 0x85, "f23": 0x86, "f24": 0x87,
    # ── Numpad ───────────────────────────────────────────────────────────────
    "num 0": 0x60, "num 1": 0x61, "num 2": 0x62, "num 3": 0x63,
    "num 4": 0x64, "num 5": 0x65, "num 6": 0x66, "num 7": 0x67,
    "num 8": 0x68, "num 9": 0x69,
    "num *": 0x6A, "num +": 0x6B, "num -": 0x6D,
    "num .": 0x6E, "num /": 0x6F,
    "num enter": 0x0D,   # same VK as main Enter for win32
    # ── Symbols ──────────────────────────────────────────────────────────────
    "-":  0xBD, "=":  0xBB, "[":  0xDB, "]":  0xDD,
    ";":  0xBA, "'":  0xDE, ",":  0xBC, ".":  0xBE,
    "/":  0xBF, "\\": 0xDC, "`":  0xC0,
    # ── Misc ─────────────────────────────────────────────────────────────────
    "print screen": 0x2C,
    "pause":        0x13,
    "break":        0x13,
    "sleep":        0x5F,
    "help":         0x2F,
    "clear":        0x0C,
    # ── Media ────────────────────────────────────────────────────────────────
    "volume mute":     0xAD,
    "volume down":     0xAE,
    "volume up":       0xAF,
    "next track":      0xB0,
    "previous track":  0xB1,
    "stop media":      0xB2,
    "play/pause media": 0xB3,
}

# ─────────────────────────────────────────────────────────────────────────────
# 2.  Aliases  (user-friendly → canonical name)
# ─────────────────────────────────────────────────────────────────────────────

ALIASES: dict[str, str] = {
    # spacing / return
    " ":         "space",
    "return":    "enter",
    "num enter": "enter",
    # escape
    "esc":       "escape",
    # navigation
    "pgup":      "page up",
    "pgdn":      "page down",
    "pageup":    "page up",
    "pagedown":  "page down",
    "pg up":     "page up",
    "pg down":   "page down",
    # editing
    "del":       "delete",
    "ins":       "insert",
    "bksp":      "backspace",
    # modifiers — generic → left-sided canonical
    "lshift":    "left shift",
    "rshift":    "right shift",
    "lctrl":     "left ctrl",
    "rctrl":     "right ctrl",
    "lalt":      "left alt",
    "ralt":      "right alt",
    "lwin":      "windows",
    "rwin":      "right windows",
    "win":       "windows",
    "cmd":       "windows",
    "right cmd": "right windows",
    # caps / locks
    "caps":      "caps lock",
    "numlock":   "num lock",
    "scrolllock":"scroll lock",
    # numpad variants
    "numpad0":   "num 0",  "numpad1": "num 1",  "numpad2": "num 2",
    "numpad3":   "num 3",  "numpad4": "num 4",  "numpad5": "num 5",
    "numpad6":   "num 6",  "numpad7": "num 7",  "numpad8": "num 8",
    "numpad9":   "num 9",
    "num decimal": "num .",
    "num add":    "num +",
    "num subtract": "num -",
    "num multiply": "num *",
    "num divide": "num /",
    # media
    "mute":      "volume mute",
    # print-screen
    "prtscn":    "print screen",
    "prtsc":     "print screen",
}

# ─────────────────────────────────────────────────────────────────────────────
# 3.  Key resolution helpers
# ─────────────────────────────────────────────────────────────────────────────

def resolve(name: str) -> str:
    """Normalise any user-facing key name to a canonical KEY_MAP name.

    Steps:
      1. Check the raw value against ALIASES first (before strip), so a literal
         " " (single space) resolves to "space" before stripping removes it.
      2. Lower-case and strip.
      3. Walk ALIASES until no further alias exists.
      4. Return the canonical name.
    """
    # Pre-strip alias check: catches literal " " → "space" etc.
    if name in ALIASES:
        name = ALIASES[name]
    s = name.lower().strip()
    # follow alias chain (max 4 hops to avoid infinite loops)
    for _ in range(4):
        nxt = ALIASES.get(s)
        if nxt is None:
            break
        s = nxt
    return s


def lookup(name: str) -> int:
    """Return the Win32 virtual-key code for *name*, raising KeyError if unknown."""
    canonical = resolve(name)
    if canonical in KEY_MAP:
        return KEY_MAP[canonical]
    # single printable ASCII character not in map?
    if len(canonical) == 1:
        return ord(canonical.upper())
    raise KeyError(f"Unknown key: {name!r} (resolved to {canonical!r})")


# ─────────────────────────────────────────────────────────────────────────────
# 4.  Backend availability probes
# ─────────────────────────────────────────────────────────────────────────────

# ── Interception ─────────────────────────────────────────────────────────────
INTERCEPTION_OK: bool = False
INTERCEPTION_ERR: str = ""
_interception = None
try:
    import interception as _interception_mod
    _interception = _interception_mod
    # Only mark OK if we can actually initialise devices
    _interception.auto_capture_devices(keyboard=True, mouse=True)
    INTERCEPTION_OK = True
except Exception as _e:
    INTERCEPTION_ERR = str(_e)
    _interception = None

# ── Win32 ─────────────────────────────────────────────────────────────────────
WIN32_OK: bool = False
_win32api = None
_win32con = None
try:
    import win32api as _win32api
    import win32con as _win32con
    WIN32_OK = True
except ImportError:
    pass

# ── keyboard lib ─────────────────────────────────────────────────────────────
KEYBOARD_OK: bool = False
_keyboard = None
try:
    import keyboard as _keyboard
    KEYBOARD_OK = True
except ImportError:
    pass

# ─────────────────────────────────────────────────────────────────────────────
# 5.  Active-backend state
# ─────────────────────────────────────────────────────────────────────────────

if INTERCEPTION_OK:
    _kb_backend: str = "interception"
    _mouse_backend: str = "interception"
elif WIN32_OK:
    _kb_backend = "win32"
    _mouse_backend = "win32"
elif KEYBOARD_OK:
    _kb_backend = "keyboard"
    _mouse_backend = "win32"          # keyboard lib has no mouse; fallback
else:
    _kb_backend = "none"
    _mouse_backend = "none"


def get_kb_backend() -> str:
    return _kb_backend

def get_mouse_backend() -> str:
    return _mouse_backend

def set_kb_backend(mode: str) -> None:
    global _kb_backend
    _kb_backend = mode

def set_mouse_backend(mode: str) -> None:
    global _mouse_backend
    _mouse_backend = mode

def backend_status() -> dict:
    return {
        # short aliases (internal use)
        "kb":    _kb_backend,
        "mouse": _mouse_backend,
        # long-form names expected by Automation_bot.py UI
        "kb_backend":    _kb_backend,
        "mouse_backend": _mouse_backend,
        # driver availability
        "interception":     INTERCEPTION_OK,
        "interception_err": INTERCEPTION_ERR,
        "win32":             WIN32_OK,
        "keyboard":          KEYBOARD_OK,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 6.  Low-level keyboard send helpers
# ─────────────────────────────────────────────────────────────────────────────

def _send_key_win32(vk: int, down: bool) -> None:
    """Send a key event via win32api.keybd_event."""
    if _win32api is None:
        return
    flags = 0 if down else _win32con.KEYEVENTF_KEYUP
    _win32api.keybd_event(vk, 0, flags, 0)


def _send_key_kb(name: str, down: bool) -> None:
    """Send a key event via the keyboard library."""
    if _keyboard is None:
        return
    canonical = resolve(name)
    try:
        if down:
            _keyboard.press(canonical)
        else:
            _keyboard.release(canonical)
    except Exception:
        pass


def _send_key_interception(name: str, down: bool) -> None:
    """Send a key event via pyinterception."""
    if _interception is None:
        return
    canonical = resolve(name)
    try:
        if down:
            _interception.key_down(canonical)
        else:
            _interception.key_up(canonical)
    except Exception:
        # pyinterception may use press/release API depending on version
        try:
            if down:
                _interception.press(canonical)
            # key_up is implicit after press() in some versions; handle release separately
        except Exception:
            pass


def _key_down_raw(name: str) -> None:
    vk = lookup(name)
    if _kb_backend == "interception":
        _send_key_interception(name, True)
    elif _kb_backend == "keyboard":
        _send_key_kb(name, True)
    else:  # win32 / fallback
        _send_key_win32(vk, True)


def _key_up_raw(name: str) -> None:
    vk = lookup(name)
    if _kb_backend == "interception":
        _send_key_interception(name, False)
    elif _kb_backend == "keyboard":
        _send_key_kb(name, False)
    else:
        _send_key_win32(vk, False)


# ─────────────────────────────────────────────────────────────────────────────
# 7.  Public keyboard API
# ─────────────────────────────────────────────────────────────────────────────

_held_keys: set[str] = set()          # keys kept down by kb_toggleHold
_loop_threads: dict[str, threading.Event] = {}  # key → stop-event


def kb_down(key: str) -> None:
    """Press key down (no auto-release)."""
    _key_down_raw(resolve(key))


def kb_up(key: str) -> None:
    """Release a key."""
    _key_up_raw(resolve(key))


def kb_press(key: str, hold: float = 0.03) -> None:
    """Tap a key: press down, wait *hold* seconds, release."""
    k = resolve(key)
    _key_down_raw(k)
    if hold > 0:
        time.sleep(hold)
    _key_up_raw(k)


def kb_hold(key: str, seconds: float = 1.0,
            _stop_check: Optional[Callable[[], bool]] = None) -> None:
    """Hold a key for *seconds*, checking *_stop_check* every 50 ms."""
    k = resolve(key)
    _key_down_raw(k)
    deadline = time.perf_counter() + seconds
    try:
        while time.perf_counter() < deadline:
            if _stop_check and _stop_check():
                break
            time.sleep(0.05)
    finally:
        _key_up_raw(k)


def kb_combo(combo: str) -> None:
    """Send a key combination like 'ctrl+c' or 'ctrl+shift+end'."""
    parts = [p.strip() for p in combo.split("+")]
    resolved = [resolve(p) for p in parts]
    # press all modifiers, then the final key, then release in reverse
    for k in resolved:
        _key_down_raw(k)
        time.sleep(0.02)
    time.sleep(0.05)
    for k in reversed(resolved):
        _key_up_raw(k)
        time.sleep(0.02)


def kb_toggleHold(key: str, on: bool) -> None:
    """Hold a key down (on=True) or release it (on=False), persistently."""
    k = resolve(key)
    if on:
        _held_keys.add(k)
        _key_down_raw(k)
    else:
        _held_keys.discard(k)
        _key_up_raw(k)


def kb_toggleLoop(key: str, on: bool, interval: float = 0.5,
                  _stop_check: Optional[Callable[[], bool]] = None) -> None:
    """Repeatedly tap *key* every *interval* seconds until toggled off."""
    k = resolve(key)
    if on:
        if k in _loop_threads:
            return  # already looping
        stop_evt = threading.Event()
        _loop_threads[k] = stop_evt

        def _loop():
            try:
                while not stop_evt.is_set():
                    if _stop_check and _stop_check():
                        break
                    kb_press(k, hold=0.03)
                    stop_evt.wait(interval)
            finally:
                _loop_threads.pop(k, None)

        t = threading.Thread(target=_loop, daemon=True)
        t.start()
    else:
        evt = _loop_threads.pop(k, None)
        if evt:
            evt.set()


def stop_all_loops() -> None:
    """Stop every active kb_toggleLoop."""
    for evt in list(_loop_threads.values()):
        evt.set()
    _loop_threads.clear()


def release_all_held() -> None:
    """Release every key currently held via kb_toggleHold."""
    for k in list(_held_keys):
        try:
            _key_up_raw(k)
        except Exception:
            pass
    _held_keys.clear()


def cleanup() -> None:
    """Best-effort cleanup: stop loops, release held keys."""
    stop_all_loops()
    release_all_held()


# ─────────────────────────────────────────────────────────────────────────────
# 8.  Legacy keyboard API  (keyboard_press + key_* aliases)
# ─────────────────────────────────────────────────────────────────────────────

def keyboard_press(key: str, function: str = "c", timer: Optional[float] = None,
                   state: Optional[str] = None,
                   _stop_check: Optional[Callable[[], bool]] = None) -> None:
    """Legacy script API — routes to modern kb_* functions.

    function:
      'c' / 'click'  — tap (down+up)
      'd' / 'down'   — key down
      'u' / 'up'     — key up
      'h' / 'hold'   — hold for *timer* seconds
      'l' / 'loop'   — toggle loop; *state* = 'start' | 'stop'
    """
    fn = function.lower()
    k = resolve(key)

    if fn in ("c", "click", "tap"):
        kb_press(k, hold=timer if timer else 0.03)

    elif fn in ("d", "down"):
        kb_down(k)

    elif fn in ("u", "up"):
        kb_up(k)

    elif fn in ("h", "hold"):
        kb_hold(k, seconds=timer if timer else 1.0, _stop_check=_stop_check)

    elif fn in ("l", "loop"):
        on = (state or "start").lower() == "start"
        kb_toggleLoop(k, on,
                      interval=timer if timer else 0.5,
                      _stop_check=_stop_check)


# Simple aliases
key_down  = kb_down
key_up    = kb_up
key_tap   = kb_press
key_combo = kb_combo


# ─────────────────────────────────────────────────────────────────────────────
# 9.  Mouse API
# ─────────────────────────────────────────────────────────────────────────────

# Button → win32con flag mapping
_MOUSE_BTN_DOWN: dict[str, int] = {}
_MOUSE_BTN_UP:   dict[str, int] = {}

if WIN32_OK and _win32con is not None:
    _MOUSE_BTN_DOWN = {
        "left":   _win32con.MOUSEEVENTF_LEFTDOWN,
        "right":  _win32con.MOUSEEVENTF_RIGHTDOWN,
        "middle": _win32con.MOUSEEVENTF_MIDDLEDOWN,
    }
    _MOUSE_BTN_UP = {
        "left":   _win32con.MOUSEEVENTF_LEFTUP,
        "right":  _win32con.MOUSEEVENTF_RIGHTUP,
        "middle": _win32con.MOUSEEVENTF_MIDDLEUP,
    }


def mouse_pos() -> tuple[int, int]:
    """Return current cursor position as (x, y)."""
    if WIN32_OK and _win32api:
        return _win32api.GetCursorPos()
    try:
        import ctypes
        pt = ctypes.wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        return (pt.x, pt.y)
    except Exception:
        return (0, 0)


def mouse_move(x: int, y: int) -> None:
    """Move cursor to (x, y) instantly (teleport).

    Used as the low-level primitive by mouse_smooth / mouse_human for each step.
    For interception, individual step moves are sent via move_to so the driver
    sees each intermediate position (no single large teleport).
    """
    if _mouse_backend == "interception" and _interception:
        try:
            _interception.move_to(x, y)
            return
        except Exception:
            pass
    if WIN32_OK and _win32api:
        _win32api.SetCursorPos((x, y))
        return
    try:
        import ctypes
        ctypes.windll.user32.SetCursorPos(x, y)
    except Exception:
        pass


def mouse_smooth(x: int, y: int, duration: float = 0.5) -> None:
    """Move cursor from current pos to (x, y) in *duration* seconds (cosine-eased).

    Unlike the old version, the interception backend now also steps through the
    trajectory instead of teleporting — important for heuristic-detection avoidance.
    For games that need raw-input / virtual-desktop coordinates, use mouse_human().
    """
    sx, sy = mouse_pos()
    steps = max(int(duration / 0.01), 5)
    for i in range(1, steps + 1):
        t = i / steps
        # ease in-out (cosine) — same curve as Automation Bot
        ease = (1 - math.cos(t * math.pi)) / 2
        nx = int(sx + (x - sx) * ease)
        ny = int(sy + (y - sy) * ease)
        mouse_move(nx, ny)
        time.sleep(duration / steps)


# ── Optional Bezier-curve support via pyclick ─────────────────────────────────
try:
    from pyclick import HumanCurve  # pip install pyclick
    _PYCLICK_OK = True
except ImportError:
    _PYCLICK_OK = False


def _mouse_bezier(x: int, y: int, duration: float = 0.4) -> None:
    """Move using a randomised Bezier curve (requires pyclick).  Falls back to
    mouse_smooth when pyclick is not installed."""
    if not _PYCLICK_OK:
        mouse_smooth(x, y, duration)
        return
    sx, sy = mouse_pos()
    curve = HumanCurve((sx, sy), (x, y), targetPoints=max(int(duration / 0.01), 10))
    pts = curve.points
    delay = duration / max(len(pts), 1)
    for px, py in pts:
        mouse_move(int(px), int(py))
        time.sleep(delay)


def mouse_human(x: int, y: int, duration: float = 0.25) -> None:
    """Human-like move using MOUSEEVENTF_MOVE | ABSOLUTE | VIRTUALDESK raw input.

    This is the technique from Automation Bot — uses the full virtual-desktop coordinate
    space so it works correctly on multi-monitor setups and inside games that read
    raw input (e.g. Roblox).  Falls back to mouse_smooth on non-Win32 systems.

    Movement follows a cosine ease-in-out curve with a small random jitter on each
    step to further defeat heuristic detection.
    """
    if not (WIN32_OK and _win32api):
        mouse_smooth(x, y, duration)
        return
    try:
        import win32api as _wa
        # Virtual desktop metrics
        vx = _wa.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
        vy = _wa.GetSystemMetrics(77)   # SM_YVIRTUALSCREEN
        vw = _wa.GetSystemMetrics(78)   # SM_CXVIRTUALSCREEN
        vh = _wa.GetSystemMetrics(79)   # SM_CYVIRTUALSCREEN

        cx, cy = mouse_pos()
        steps = max(int(duration / 0.012), 12)
        for i in range(1, steps + 1):
            t = i / steps
            ease = (1 - math.cos(t * math.pi)) / 2
            # tiny random overshoot / jitter (±1–2 px) to look organic
            jx = random.randint(-1, 1) if i < steps else 0
            jy = random.randint(-1, 1) if i < steps else 0
            nx = int(cx + (x - cx) * ease) + jx
            ny = int(cy + (y - cy) * ease) + jy
            # Scale to 0-65535 over the full virtual desktop
            ax = int((nx - vx) * 65535 / vw)
            ay = int((ny - vy) * 65535 / vh)
            # MOUSEEVENTF_MOVE (0x0001) | MOUSEEVENTF_ABSOLUTE (0x8000) | MOUSEEVENTF_VIRTUALDESK (0x4000)
            _wa.mouse_event(0x8001 | 0x4000, ax, ay, 0, 0)
            time.sleep(duration / steps)
    except Exception:
        # Fallback: plain smooth move
        mouse_smooth(x, y, duration)


def mouse_down(button: str = "left") -> None:
    """Press a mouse button down."""
    btn = button.lower()
    if _mouse_backend == "interception" and _interception:
        try:
            _interception.mouse_down(btn)
            return
        except Exception:
            pass
    if WIN32_OK and _win32api:
        flag = _MOUSE_BTN_DOWN.get(btn, _win32con.MOUSEEVENTF_LEFTDOWN)
        _win32api.mouse_event(flag, 0, 0, 0, 0)


def mouse_up(button: str = "left") -> None:
    """Release a mouse button."""
    btn = button.lower()
    if _mouse_backend == "interception" and _interception:
        try:
            _interception.mouse_up(btn)
            return
        except Exception:
            pass
    if WIN32_OK and _win32api:
        flag = _MOUSE_BTN_UP.get(btn, _win32con.MOUSEEVENTF_LEFTUP)
        _win32api.mouse_event(flag, 0, 0, 0, 0)


def mouse_click(x: int, y: int, button: str = "left",
                hold: float = 0.05,
                humanize: bool = False,
                duration: float = 0.25) -> None:
    """Move to (x, y) then click *button*.

    humanize=True  — move with mouse_human() (raw-input cosine curve + jitter).
    duration       — movement time used when humanize=True.
    """
    if humanize:
        # Small hardware jiggle before the move (mirrors Automation Bot click_at)
        if WIN32_OK and _win32api:
            try:
                _win32api.mouse_event(1, 1, 1, 0, 0)
                time.sleep(0.015)
                _win32api.mouse_event(1, -1, -1, 0, 0)
            except Exception:
                pass
        mouse_human(x, y, duration)
        time.sleep(0.12)
    else:
        mouse_move(x, y)
        time.sleep(0.03)
    mouse_down(button)
    time.sleep(hold)
    mouse_up(button)


def click_at_pos(x: int, y: int, hold: float = 0.05,
                 button: str = "left",
                 humanize: bool = False,
                 duration: float = 0.25) -> None:
    """Move to (x, y) then click.

    Pass humanize=True (or the string 'h') for a human-like trajectory.
    """
    mouse_click(x, y, button=button, hold=hold,
                humanize=bool(humanize), duration=duration)


# Legacy aliases
get_pos    = mouse_pos
human_move = mouse_smooth