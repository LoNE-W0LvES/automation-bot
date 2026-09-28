"""
core/engine.py — Script execution engine, sandbox environment, and button/control proxies
"""

import time
import datetime
import threading
import math
import traceback
import re
from typing import Optional, Dict, Any, List

from PySide6.QtCore import Signal, QThread

from keys import (
    kb_down, kb_up, kb_press, kb_hold, kb_combo,
    kb_toggleLoop, kb_toggleHold,
    mouse_pos, mouse_move, mouse_smooth, mouse_down, mouse_up,
    mouse_click, click_at_pos, keyboard_press
)

from core.vision import (
    capture, ocr_find_text, ocr_read_number, enhance_ocr,
    pixel_match, color_match, image_match, _detect_highlight, OCR_OK
)
from core.macro import MacroPlayer

try:
    import pytesseract
except ImportError:
    pytesseract = None


class ScriptEngine(QThread):
    """Worker thread running user scripts in a safe sandbox."""
    log_sig    = Signal(str)
    status_sig = Signal(str)

    def __init__(self, script_text: str, btn_defs: dict, ctrl_defs: dict, cfg: dict, script_name: str = "script", macro_getter=None):
        super().__init__()
        self._script       = script_text
        self._btn_defs     = btn_defs
        self._ctrl_defs    = ctrl_defs
        self._cfg          = cfg
        self._name         = script_name
        self._stop         = False
        self._btn_states: dict = {}
        self._macro_getter = macro_getter  # callable(name) -> events|None

    def stop(self):
        self._stop = True

    def _log(self, msg):
        ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.log_sig.emit(f"[{ts}] {msg}")

    def run(self):
        self.status_sig.emit("Running")
        self._log("▶ Script started")
        try:
            self._exec_script()
        except Exception as e:
            self._log(f"❌ Script error: {e}")
            self._log(traceback.format_exc())
        self.status_sig.emit("Stopped")
        self._log("⏹ Script stopped")

    def _exec_script(self):
        btn_defs = self._btn_defs
        cfg = self._cfg
        engine = self
        btn_states = self._btn_states

        # ── Sentinel flags used as scan-type arguments ──────────────────────────
        class _Flag:
            def __init__(self, name): self.name = name
            def __repr__(self): return self.name
        _F_OCR   = _Flag("o")
        _F_PIXEL = _Flag("p")
        _F_IMAGE = _Flag("i")
        _F_HL    = _Flag("h")
        _F_COLOR = _Flag("c")

        class BtnProxy:
            """Proxy for a single defined button/region."""
            _result = False

            def __init__(self, name, bdef):
                self._name  = name
                self._bdef  = bdef
                self._ocr          = False
                self._pixel        = False
                self._image        = False
                self._hl           = False
                self._color_result = {}
                self._result       = False

            # ── internal runners (force=True skips the enabled checkbox) ─────
            def _run_ocr(self, force=False, img=None):
                if not force and not self._bdef.get("ocr_enabled"): return
                reg = self._bdef.get("region")
                if not reg: return
                target_img = img if img is not None else capture(reg)
                if target_img:
                    self._ocr = ocr_find_text(target_img,
                        self._bdef.get("ocr_text", ""),
                        self._bdef.get("ocr_psm", 6),
                        self._bdef.get("ocr_min_conf", 18),
                        btn_def=self._bdef)

            def _run_pixel(self, force=False, img=None):
                if not force and not self._bdef.get("pixel_enabled"): return
                reg = self._bdef.get("region")
                if not reg: return
                target_img = img if img is not None else capture(reg)
                if target_img:
                    self._pixel = pixel_match(target_img,
                        self._bdef.get("pixel_data"),
                        self._bdef.get("pixel_threshold", 35))

            def _run_image(self, force=False, img=None):
                if not force and not self._bdef.get("image_enabled"): return
                reg = self._bdef.get("region")
                if not reg: return
                target_img = img if img is not None else capture(reg)
                if target_img:
                    self._image = image_match(target_img,
                        self._bdef.get("image_template"),
                        self._bdef.get("image_threshold", 0.8))

            def _run_hl(self, force=False, img=None):
                if not force and not self._bdef.get("hl_enabled"): return
                reg = self._bdef.get("region")
                if not reg: return
                target_img = img if img is not None else capture(reg)
                if target_img:
                    result, *_ = _detect_highlight(target_img, self._bdef)
                    self._hl = result

            def _run_color(self, force=False, img=None):
                if not force and not self._bdef.get("color_enabled"): return
                reg = self._bdef.get("region")
                if not reg: return
                target_img = img if img is not None else capture(reg)
                if target_img:
                    self._color_result = color_match(
                        target_img, self._bdef.get("color_data"))

            def _update_result(self, forced_flags=None):
                forced = forced_flags or set()
                color_bool = self._color_result.get("_any", False) if isinstance(
                    self._color_result, dict) else bool(self._color_result)
                enabled = [
                    self._ocr   if (self._bdef.get("ocr_enabled")   or "ocr"   in forced) else None,
                    self._pixel if (self._bdef.get("pixel_enabled")  or "pixel" in forced) else None,
                    self._image if (self._bdef.get("image_enabled")  or "image" in forced) else None,
                    self._hl    if (self._bdef.get("hl_enabled")     or "hl"    in forced) else None,
                    color_bool  if (self._bdef.get("color_enabled")  or "color" in forced) else None,
                ]
                active = [v for v in enabled if v is not None]
                if not active:
                    self._result = False
                else:
                    mode = self._bdef.get("match_mode", "any1")
                    if   mode == "any1": self._result = any(active)
                    elif mode == "any2": self._result = sum(active) >= 2
                    elif mode == "all":  self._result = all(active)
                    else:                self._result = any(active)
                btn_states[self._name] = {
                    "ocr": self._ocr, "pixel": self._pixel,
                    "image": self._image, "result": self._result,
                    "color": self._color_result,
                }

            # ── scan(flags=None) ──────────────────────────────────────────────
            def scan(self, flags=None):
                reg = self._bdef.get("region")
                shared_img = capture(reg) if reg else None
                if flags is None:
                    self._run_ocr(img=shared_img)
                    self._run_pixel(img=shared_img)
                    self._run_image(img=shared_img)
                    self._run_hl(img=shared_img)
                    self._run_color(img=shared_img)
                    self._update_result()
                else:
                    forced = set()
                    for f in flags:
                        if   f is _F_OCR:   self._run_ocr(force=True, img=shared_img);   forced.add("ocr")
                        elif f is _F_PIXEL: self._run_pixel(force=True, img=shared_img);  forced.add("pixel")
                        elif f is _F_IMAGE: self._run_image(force=True, img=shared_img);  forced.add("image")
                        elif f is _F_HL:    self._run_hl(force=True, img=shared_img);     forced.add("hl")
                        elif f is _F_COLOR: self._run_color(force=True, img=shared_img);  forced.add("color")
                    self._update_result(forced_flags=forced)
                return self

            # ── .ocr / .pixel / .image — bool OR force-scan handle ───────────
            class _TypeHandle:
                __slots__ = ("_proxy", "_kind")
                def __init__(self, proxy, kind):
                    self._proxy = proxy
                    self._kind  = kind

                def scan(self):
                    p = self._proxy
                    if   self._kind == "ocr":   p._run_ocr(force=True)
                    elif self._kind == "pixel":  p._run_pixel(force=True)
                    elif self._kind == "image":  p._run_image(force=True)
                    elif self._kind == "hl":     p._run_hl(force=True)
                    p._update_result(forced_flags={self._kind})
                    return p

                def value(self):
                    if self._kind != "ocr": return ""
                    p = self._proxy
                    reg = p._bdef.get("region")
                    if not reg or not OCR_OK or pytesseract is None: return ""
                    img = capture(reg)
                    if not img: return ""
                    try:
                        pre = enhance_ocr(img, btn_def=p._bdef)
                        psm = p._bdef.get("ocr_psm", 7)
                        return pytesseract.image_to_string(
                            pre, config=f"--psm {psm}").strip()
                    except Exception: return ""

                def _val(self):
                    p = self._proxy
                    if self._kind == "ocr":    return p._ocr
                    if self._kind == "pixel":  return p._pixel
                    if self._kind == "hl":     return p._hl
                    return p._image

                def __bool__(self):      return bool(self._val())
                def __eq__(self, o):     return self._val() == o
                def __ne__(self, o):     return self._val() != o
                def __repr__(self):      return str(bool(self._val()))

            @property
            def ocr(self):   return self._TypeHandle(self, "ocr")
            @property
            def pixel(self): return self._TypeHandle(self, "pixel")
            @property
            def image(self): return self._TypeHandle(self, "image")
            @property
            def hl(self):    return self._TypeHandle(self, "hl")

            @property
            def color(self):
                proxy = self
                class _ColorHandle:
                    def __getattr__(self, name: str):
                        r = proxy._color_result
                        return r.get(name, False) if isinstance(r, dict) else False

                    def __bool__(self):
                        r = proxy._color_result
                        return r.get("_any", False) if isinstance(r, dict) else False

                    @property
                    def result(self):
                        return proxy._color_result

                    def scan(self, *, pixel_ids=None, color=None,
                             threshold=None, min_match=None):
                        reg = proxy._bdef.get("region")
                        if not reg:
                            return {} if (pixel_ids is None and color is None) else False
                        img = capture(reg)
                        if not img:
                            return {} if (pixel_ids is None and color is None) else False
                        cdata = proxy._bdef.get("color_data") or {}
                        if pixel_ids is not None or color is not None:
                            return color_match(img, cdata,
                                               pixel_ids=pixel_ids,
                                               color=color,
                                               threshold=threshold,
                                               min_match=min_match)
                        else:
                            proxy._color_result = color_match(img, cdata)
                            proxy._update_result()
                            return proxy._color_result

                return _ColorHandle()

            def __getattr__(self, name: str):
                if name.startswith("_"):
                    raise AttributeError(name)
                cdata  = self._bdef.get("color_data") or {}
                groups = cdata.get("groups", [])
                match  = next((g for g in groups if g.get("name") == name), None)
                if match is None:
                    raise AttributeError(
                        f"Button '{self._name}' has no group '{name}'. "
                        f"Available: {[g.get('name') for g in groups]}")
                proxy = self
                g     = match

                class _GroupHandle:
                    def color_scan(self, *, color=None, threshold=None, min_match=None):
                        reg = proxy._bdef.get("region")
                        if not reg: return False
                        img = capture(reg)
                        if not img: return False
                        points = cdata.get("points", [])
                        ids    = g.get("pixel_ids", [])
                        tgt    = color     if color     is not None else g.get("color",     [128,128,128])
                        thr    = threshold if threshold is not None else g.get("threshold", 40)
                        need   = min_match if min_match is not None else g.get("min_match", max(1, len(ids)))

                        def _rgb(c):
                            if isinstance(c, (list, tuple)): return c[:3]
                            s = str(c).lstrip("#")
                            return [int(s[0:2],16), int(s[2:4],16), int(s[4:6],16)]

                        matched = 0
                        for i in ids:
                            if i >= len(points): continue
                            try:
                                px   = img.getpixel((int(points[i][0]), int(points[i][1])))
                                dist = math.sqrt(sum((a-b)**2 for a,b in zip(px[:3], _rgb(tgt))))
                                if dist <= thr: matched += 1
                            except Exception: pass
                        return matched >= need

                    def __bool__(self):
                        return self.color_scan()

                    def __repr__(self):
                        return f"<Group '{name}' pts={g.get('pixel_ids',[])}>"

                return _GroupHandle()

            @property
            def result(self): return self._result
            def __bool__(self): return self._result

            def click(self, humanize=False, hold=0.15, repeat=1, duration=0.25):
                """Click the centre of this button's region."""
                reg = self._bdef.get("region")
                if not reg: return
                if self._bdef.get("btn_type", "button") != "button":
                    engine._log(f"⚠ {self._name} is a region (not clickable)")
                    return
                cx = reg[0] + reg[2] // 2
                cy = reg[1] + reg[3] // 2
                do_human = humanize is True or (
                    isinstance(humanize, str) and humanize.lower()[:1] == 'h'
                )
                for _ in range(repeat):
                    click_at_pos(cx, cy, hold, humanize=do_human, duration=duration)
                    time.sleep(0.05)
                mode = "🐭 Human-click" if do_human else "🖱 Clicked"
                engine._log(f"{mode} [{self._name}]")

            def ocr_text(self):
                reg = self._bdef.get("region")
                if not reg or not OCR_OK or pytesseract is None: return ""
                img = capture(reg)
                if not img: return ""
                try:
                    return pytesseract.image_to_string(
                        enhance_ocr(img, btn_def=self._bdef), config="--psm 7").strip()
                except Exception: return ""

            def ocr_int(self):
                reg = self._bdef.get("region")
                if not reg: return None
                img = capture(reg)
                return ocr_read_number(img, btn_def=self._bdef)

            def __repr__(self): return f"BtnProxy({self._name})"

        class BtnGroup:
            """Bulk scan over ALL defined buttons."""
            def __init__(self, proxies):
                self._proxies = proxies

            class _BulkType:
                def __init__(self, proxies, kind): self._p = proxies; self._kind = kind
                def scan(self):
                    for p in self._p.values():
                        if   self._kind == "ocr":   p._run_ocr()
                        elif self._kind == "pixel":  p._run_pixel()
                        elif self._kind == "image":  p._run_image()
                        p._update_result()

            @property
            def ocr(self):   return self._BulkType(self._proxies, "ocr")
            @property
            def pixel(self): return self._BulkType(self._proxies, "pixel")
            @property
            def image(self): return self._BulkType(self._proxies, "image")

            def scan(self):
                for p in self._proxies.values():
                    p.scan()

        proxies = {n: BtnProxy(n, bdef) for n, bdef in btn_defs.items()}

        class CtrlProxy:
            """Live control proxy reading current settings from shared dict."""
            def __init__(self, cdef_ref):
                object.__setattr__(self, '_cdef', cdef_ref)

            def _live(self):
                cd = object.__getattribute__(self, '_cdef')
                if cd.get('ctrl_type', 'selector') == 'selector':
                    opts = cd.get('options', [])
                    return cd.get('selected', opts[0] if opts else '')
                return cd.get('value', cd.get('min', 0))

            def __eq__(self, o):      return self._live() == o
            def __ne__(self, o):      return self._live() != o
            def __lt__(self, o):      return self._live() <  o
            def __le__(self, o):      return self._live() <= o
            def __gt__(self, o):      return self._live() >  o
            def __ge__(self, o):      return self._live() >= o
            def __bool__(self):       return bool(self._live())
            def __int__(self):        return int(self._live())
            def __float__(self):      return float(self._live())
            def __str__(self):        return str(self._live())
            def __repr__(self):       return repr(self._live())
            def __hash__(self):       return hash(self._live())
            def __sub__(self, o):     return self._live() - o
            def __add__(self, o):     return self._live() + o
            def __mul__(self, o):     return self._live() * o
            def __truediv__(self, o): return self._live() / o
            def __rsub__(self, o):    return o - self._live()
            def __radd__(self, o):    return o + self._live()

        ctrl_proxies = {n: CtrlProxy(cdef) for n, cdef in self._ctrl_defs.items()}
        buttons = BtnGroup(proxies)

        def sleep(s):
            """Pause for s seconds; responds to Stop button immediately."""
            end = time.monotonic() + s
            while time.monotonic() < end:
                if engine._stop: raise InterruptedError("Bot stopped")
                rem = end - time.monotonic()
                if rem <= 0: break
                time.sleep(min(0.02, max(0.001, rem)))

        def log(msg): engine._log(str(msg))

        def scan(btn_list, flags=None):
            """Multi-button scan helper."""
            for btn in btn_list:
                btn.scan(flags)

        _macro_getter = engine._macro_getter

        def play_macro(name: str, loop: bool = False):
            """Play a named macro from the Macro Library synchronously."""
            if _macro_getter is None:
                engine._log("⚠ play_macro: no macro source available"); return
            events = _macro_getter(name)
            if events is None:
                engine._log(f"⚠ play_macro: macro not found: '{name}'"); return
            if not events:
                engine._log(f"⚠ play_macro: macro '{name}' is empty"); return
            engine._log(f"▶ play_macro: {name} ({len(events)} events)")
            player = MacroPlayer(events, loop=loop)
            done_evt = threading.Event()
            player.finished.connect(lambda: done_evt.set())
            player.start()
            while not done_evt.is_set():
                if engine._stop:
                    player.stop_playback()
                    done_evt.wait(timeout=1.0)
                    break
                time.sleep(0.05)
            engine._log(f"⏹ play_macro: {name} done")

        def _kb_press(key, hold=0.03):          kb_press(key, hold)
        def _kb_down(key):                       kb_down(key)
        def _kb_up(key):                         kb_up(key)
        def _kb_hold(key, seconds=1.0):          kb_hold(key, seconds, _stop_check=lambda: engine._stop)
        def _kb_combo(combo):                    kb_combo(combo)
        def _kb_toggleLoop(key, on, interval=0.5):
            kb_toggleLoop(key, on, interval=interval, _stop_check=lambda: engine._stop)
        def _kb_toggleHold(key, on):             kb_toggleHold(key, on)

        def _keyboard_press(key, function='c', timer=None, state=None):
            keyboard_press(key, function, timer, state, _stop_check=lambda: engine._stop)

        ns = {
            "__builtins__": {
                "range": range, "len": len, "int": int, "float": float,
                "str": str, "bool": bool, "list": list, "dict": dict,
                "print": engine._log, "abs": abs, "min": min, "max": max,
                "True": True, "False": False, "None": None,
            },
            "sleep":           sleep,
            "log":             log,
            "scan":            scan,
            "play_macro":      play_macro,
            "kb_press":        _kb_press,
            "kb_down":         _kb_down,
            "kb_up":           _kb_up,
            "kb_hold":         _kb_hold,
            "kb_combo":        _kb_combo,
            "kb_toggleLoop":   _kb_toggleLoop,
            "kb_toggleHold":   _kb_toggleHold,
            "mouse_move":      mouse_move,
            "mouse_smooth":    mouse_smooth,
            "mouse_click":     mouse_click,
            "mouse_down":      mouse_down,
            "mouse_up":        mouse_up,
            "mouse_pos":       mouse_pos,
            "keyboard_press":  _keyboard_press,
            "key_down":        kb_down,
            "key_up":          kb_up,
            "buttons":         buttons,
            "o": _F_OCR,
            "p": _F_PIXEL,
            "i": _F_IMAGE,
            "h": _F_HL,
            "c": _F_COLOR,
            **proxies,
            **ctrl_proxies,
        }

        script = self._script.replace("while true:", "while True:")

        try:
            compiled = compile(script, "<script>", "exec")
            exec(compiled, ns)
        except InterruptedError:
            pass
        except SyntaxError as e:
            engine._log(f"❌ Syntax error: {e}")
        except Exception as e:
            if not engine._stop:
                engine._log(f"❌ Runtime error: {e}")
                engine._log(traceback.format_exc())
