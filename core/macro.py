"""
core/macro.py — Macro recording and playback for mouse and keyboard events
"""

import time
import threading
from typing import Optional, List, Dict, Any

from PySide6.QtCore import Signal, QThread

from keys import (
    get_pos, mouse_move, mouse_down, mouse_up,
    key_down, key_up, resolve as kb_resolve,
    INTERCEPTION_OK, WIN32_OK, KEYBOARD_OK
)

try:
    import win32api
    import win32con
except ImportError:
    win32api = None
    win32con = None

try:
    import keyboard as _kb
except ImportError:
    _kb = None


def _interception_listen_hook(on_down, on_up):
    """Try to register per-key callbacks via the Interception driver."""
    return None

def _interception_unlisten_hook(hook):
    """Unregister a hook returned by _interception_listen_hook."""
    pass


class MacroRecorder:
    """Records mouse moves + mouse clicks + key events with timestamps."""

    def __init__(self):
        self._recording     = False
        self._events: list  = []
        self._start_t       = 0.0
        self._start_pos     = (0, 0)
        self._mouse_thread  = None
        self._stop_mouse    = False
        self._rec_mouse     = True
        self._rec_keyboard  = True
        self._ic_hook       = None
        self._hook_down     = None
        self._hook_up       = None

    def start_recording(self, record_mouse: bool = True, record_keyboard: bool = True):
        self._rec_mouse    = record_mouse
        self._rec_keyboard = record_keyboard
        self._recording    = True
        self._events       = []
        self._start_t      = time.monotonic()
        self._start_pos    = get_pos()

        # ── Mouse capture ─────────────────────────────────────────────────
        if record_mouse:
            self._stop_mouse   = False
            self._mouse_thread = threading.Thread(target=self._mouse_loop, daemon=True)
            self._mouse_thread.start()

        # ── Keyboard capture ──────────────────────────────────────────────
        if record_keyboard:
            if INTERCEPTION_OK:
                try:
                    hook = _interception_listen_hook(self._on_key_down_raw, self._on_key_up_raw)
                    if hook is not None:
                        self._ic_hook = hook
                        return
                except Exception:
                    pass
            if KEYBOARD_OK and _kb:
                try:
                    self._hook_down = _kb.on_press(self._on_key_down)
                    self._hook_up   = _kb.on_release(self._on_key_up)
                except Exception as e:
                    print(f"[MacroRecorder] keyboard hook failed: {e}")

    def stop_recording(self):
        self._recording  = False
        self._stop_mouse = True
        if KEYBOARD_OK and _kb:
            try:
                if self._hook_down: _kb.unhook(self._hook_down)
                if self._hook_up:   _kb.unhook(self._hook_up)
            except Exception:
                pass
        if self._ic_hook is not None:
            try:
                _interception_unlisten_hook(self._ic_hook)
            except Exception:
                pass
            self._ic_hook = None

    def _mouse_loop(self):
        """Poll mouse position AND left/right button state every ~16 ms."""
        last_pos  = None
        btn_state = {"left": False, "right": False}
        while not self._stop_mouse:
            if not self._recording:
                break
            cx, cy = get_pos()
            rx = cx - self._start_pos[0]
            ry = cy - self._start_pos[1]
            t  = time.monotonic() - self._start_t
            if last_pos is None or (rx, ry) != last_pos:
                self._events.append({"type": "mouse_move", "rx": rx, "ry": ry, "t": t})
                last_pos = (rx, ry)
            if WIN32_OK and win32api and win32con:
                try:
                    l_down = bool(win32api.GetKeyState(win32con.VK_LBUTTON) & 0x8000)
                    r_down = bool(win32api.GetKeyState(win32con.VK_RBUTTON) & 0x8000)
                except Exception:
                    l_down = r_down = False
                for btn, is_down in (("left", l_down), ("right", r_down)):
                    was = btn_state[btn]
                    if is_down and not was:
                        self._events.append({"type": "mouse_down", "btn": btn, "t": t})
                        btn_state[btn] = True
                    elif not is_down and was:
                        self._events.append({"type": "mouse_up", "btn": btn, "t": t})
                        btn_state[btn] = False
            time.sleep(0.016)
        t = time.monotonic() - self._start_t
        for btn, was in btn_state.items():
            if was:
                self._events.append({"type": "mouse_up", "btn": btn, "t": t})

    def _on_key_down(self, event):
        if not self._recording: return
        t = time.monotonic() - self._start_t
        self._events.append({"type": "key_down", "key": event.name, "t": t})

    def _on_key_up(self, event):
        if not self._recording: return
        t = time.monotonic() - self._start_t
        self._events.append({"type": "key_up", "key": event.name, "t": t})

    def _on_key_down_raw(self, key_name: str):
        if not self._recording: return
        t = time.monotonic() - self._start_t
        self._events.append({"type": "key_down", "key": key_name, "t": t})

    def _on_key_up_raw(self, key_name: str):
        if not self._recording: return
        t = time.monotonic() - self._start_t
        self._events.append({"type": "key_up", "key": key_name, "t": t})

    @property
    def events(self): return self._events
    @property
    def is_recording(self): return self._recording
    def event_count(self): return len(self._events)


class MacroPlayer(QThread):
    """Replays recorded macro events asynchronously."""
    log_sig = Signal(str)

    def __init__(self, events: List[Dict[str, Any]], loop: bool = True, start_pos: Optional[tuple] = None):
        super().__init__()
        self._events = events
        self._loop = loop
        self._start_pos = start_pos or get_pos()
        self._stop = False

    def stop_playback(self):
        self._stop = True

    def run(self):
        if not self._events: return
        held_keys: set = set()
        try:
            while not self._stop:
                t_offset = time.monotonic()
                idx = 0
                while idx < len(self._events) and not self._stop:
                    ev = self._events[idx]
                    ev_t = ev["t"]
                    now = time.monotonic() - t_offset
                    wait = ev_t - now
                    if wait > 0: time.sleep(wait)
                    if self._stop: break
                    etype = ev["type"]
                    if etype == "mouse_move":
                        nx = self._start_pos[0] + ev["rx"]
                        ny = self._start_pos[1] + ev["ry"]
                        mouse_move(nx, ny)
                    elif etype == "key_down":
                        key_down(kb_resolve(ev["key"]))
                        held_keys.add(ev["key"])
                    elif etype == "key_up":
                        key_up(kb_resolve(ev["key"]))
                        held_keys.discard(ev["key"])
                    elif etype == "mouse_down":
                        mouse_down(ev.get("btn", "left"))
                    elif etype == "mouse_up":
                        mouse_up(ev.get("btn", "left"))
                    idx += 1
                for k in list(held_keys):
                    try: key_up(kb_resolve(k))
                    except Exception: pass
                held_keys.clear()
                if not self._loop: break
        finally:
            for k in list(held_keys):
                try: key_up(kb_resolve(k))
                except Exception: pass
            held_keys.clear()
        self.log_sig.emit("⏹ Macro playback stopped")
