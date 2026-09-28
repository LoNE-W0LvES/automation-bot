"""
core/vision.py — High-performance screen capture, OCR, OpenCV template matching, and color detection
"""

import io
import os
import re
import math
import base64
import threading
from typing import Optional, Tuple, Dict, Any, List

# ── Screen Capture Dependencies ───────────────────────────────────────────────
try:
    import mss
    import mss.tools
    MSS_OK = True
except ImportError:
    MSS_OK = False

# ── PIL ───────────────────────────────────────────────────────────────────────
try:
    from PIL import Image, ImageDraw, ImageGrab, ImageEnhance, ImageChops
    PIL_OK = True
except ImportError:
    PIL_OK = False

# ── Tesseract OCR ─────────────────────────────────────────────────────────────
try:
    import pytesseract
    _tp = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    if os.path.exists(_tp):
        pytesseract.pytesseract.tesseract_cmd = _tp
    OCR_OK = PIL_OK
except ImportError:
    OCR_OK = False

# ── OpenCV & NumPy ────────────────────────────────────────────────────────────
try:
    import cv2
    import numpy as np
    CV2_OK = True
except ImportError:
    CV2_OK = False
    try:
        import numpy as np
    except ImportError:
        np = None

# ── Thread-local MSS instance ─────────────────────────────────────────────────
_sct_local = threading.local()

def _get_thread_sct():
    """Retrieve or create an mss instance bound to the current thread."""
    if not MSS_OK:
        return None
    sct = getattr(_sct_local, "sct", None)
    if sct is None:
        try:
            sct = mss.mss()
            _sct_local.sct = sct
        except Exception:
            sct = None
    return sct

def capture(region: Optional[List[int]]) -> Optional[Image.Image]:
    """Capture a screen region [x, y, w, h] as a PIL RGB Image."""
    if not region: return None
    x, y, w, h = region
    if w <= 0 or h <= 0: return None
    s = _get_thread_sct()
    if s:
        try:
            raw = s.grab({"left": int(x), "top": int(y), "width": int(w), "height": int(h)})
            return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        except Exception:
            # Recreate context if display mode changed
            try:
                _sct_local.sct = mss.mss()
                raw = _sct_local.sct.grab({"left": int(x), "top": int(y), "width": int(w), "height": int(h)})
                return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            except Exception:
                pass
    if PIL_OK:
        try:
            return ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True).convert("RGB")
        except Exception:
            pass
    return None

def capture_sct(region: Optional[List[int]], sct=None) -> Optional[Image.Image]:
    """Capture a screen region with an explicit sct handle."""
    if not region: return None
    if not sct: return capture(region)
    x, y, w, h = region
    if w <= 0 or h <= 0: return None
    try:
        raw = sct.grab({"left": int(x), "top": int(y), "width": int(w), "height": int(h)})
        return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
    except Exception:
        return capture(region)

# ── OCR Channels & Preprocessing ──────────────────────────────────────────────
OCR_CHANNELS = [
    "gray",            # 0 — plain grayscale
    "invert",          # 1 — grayscale then invert
    "thresh-dark",     # 2 — invert + threshold (dark text → white, best for outlines)
    "thresh-bright",   # 3 — threshold bright pixels (white/yellow text fill)
    "red",             # 4 — red channel only
    "green",           # 5 — green channel only
    "blue",            # 6 — blue channel only
    "max-RG",          # 7 — max(R,G) — isolates yellow/white cartoon text
    "white-extract",   # 8 — keep only near-white pixels then invert → black text on white
    "selected-invert", # 9 — keep only pixels near a chosen colour then invert
]

OCR_DEFAULT_PASSES = [
    {"channel": "selected-invert", "threshold": 0, "sel_color": [255, 255, 255], "sel_radius": 60},
]

def _apply_ocr_channel(img: Image.Image, channel: str, threshold: int = 128, extra: Optional[dict] = None) -> Image.Image:
    """
    Apply a single preprocessing pass to a PIL RGB image.
    Returns a grayscale PIL image ready for Tesseract.
    """
    try:
        w, h = img.size
        big = img.resize((w * 3, h * 3), Image.Resampling.LANCZOS)
        ch = channel.lower()
        if ch == "gray":
            g = big.convert("L")
        elif ch == "invert":
            g = big.convert("L")
            g = ImageEnhance.Contrast(g).enhance(2.5)
            g = g.point(lambda x: 255 - x)
        elif ch == "thresh-dark":
            g = big.convert("L")
            g = ImageEnhance.Contrast(g).enhance(2.5)
            g = g.point(lambda x: 255 - x)
        elif ch == "thresh-bright":
            g = big.convert("L")
            g = ImageEnhance.Contrast(g).enhance(2.5)
        elif ch in ("red", "green", "blue"):
            idx = {"red": 0, "green": 1, "blue": 2}[ch]
            g = big.convert("RGB").split()[idx]
            g = ImageEnhance.Contrast(g).enhance(2.5)
        elif ch == "max-rg":
            rgb = big.convert("RGB")
            r, gc, b = rgb.split()
            if np is not None:
                arr = np.maximum(np.array(r), np.array(gc))
                g = Image.fromarray(arr.astype("uint8"), "L")
            else:
                g = ImageChops.lighter(r, gc)
            g = ImageEnhance.Contrast(g).enhance(2.5)
        elif ch == "white-extract":
            thr = threshold if (threshold and threshold > 0) else 200
            r_ch, g_ch, b_ch = big.convert("RGB").split()
            r_mask = r_ch.point(lambda x: 255 if x > thr else 0)
            g_mask = g_ch.point(lambda x: 255 if x > thr else 0)
            b_mask = b_ch.point(lambda x: 255 if x > thr else 0)
            and_rg  = ImageChops.darker(r_mask, g_mask)
            and_rgb = ImageChops.darker(and_rg,  b_mask)
            return and_rgb.point(lambda x: 0 if x > 127 else 255)
        elif ch == "selected-invert":
            ex     = extra or {}
            sc     = ex.get("sel_color",  [255, 255, 255])
            radius = ex.get("sel_radius", 60)
            sr, sg, sb = int(sc[0]), int(sc[1]), int(sc[2])
            if np is not None:
                arr   = np.array(big.convert("RGB")).astype(np.int32)
                tgt   = np.array([sr, sg, sb], dtype=np.int32)
                dist2 = np.sum((arr - tgt) ** 2, axis=2)
                mask  = dist2 <= (radius * radius)
                out   = np.where(mask, 0, 255).astype("uint8")
                return Image.fromarray(out, "L")
            else:
                px  = list(big.convert("RGB").getdata())
                r2  = radius * radius
                out = bytes(0 if (p[0]-sr)**2+(p[1]-sg)**2+(p[2]-sb)**2 <= r2 else 255 for p in px)
                return Image.frombytes("L", big.size, out)
        else:
            g = big.convert("L")

        if threshold and threshold > 0:
            g = g.point(lambda x: 255 if x > threshold else 0)
        return g
    except Exception as _e:
        return img.convert("L")

def enhance_ocr(img: Image.Image, btn_def: Optional[dict] = None) -> Optional[Image.Image]:
    """Run the first configured OCR pass for a button (or default pass)."""
    if not img: return None
    passes = (btn_def or {}).get("ocr_passes", OCR_DEFAULT_PASSES)
    if not passes:
        passes = OCR_DEFAULT_PASSES
    p = passes[0]
    return _apply_ocr_channel(img, p.get("channel", "selected-invert"),
                               p.get("threshold", 0), extra=p)

def ocr_find_text(img: Image.Image, ocr_text: str, ocr_psm: int = 6, min_conf: int = 18, btn_def: Optional[dict] = None) -> bool:
    """Run all configured OCR passes. Returns True on first match."""
    if not OCR_OK or not img or not ocr_text: return False
    targets = [t.strip().lower() for t in ocr_text.split(",") if t.strip()]
    if not targets: return False

    passes = (btn_def or {}).get("ocr_passes", OCR_DEFAULT_PASSES)
    if not passes:
        passes = OCR_DEFAULT_PASSES

    for p in passes:
        try:
            variant = _apply_ocr_channel(img,
                                         p.get("channel", "selected-invert"),
                                         p.get("threshold", 0), extra=p)
            data = pytesseract.image_to_data(
                variant, output_type=pytesseract.Output.DICT,
                config=f"--psm {ocr_psm}")
            words, confs = data["text"], data["conf"]
            full_text = " ".join(w.strip().lower() for w in words if w.strip())
            for tgt in targets:
                if tgt in full_text: return True
            for word, conf_val in zip(words, confs):
                word = word.strip().lower()
                if not word or len(word) < 2: continue
                try: c = int(conf_val)
                except Exception: c = 0
                if c < min_conf: continue
                for tgt in targets:
                    if (tgt == word
                            or (len(word) >= 3 and word in tgt)
                            or (len(tgt) >= 3 and tgt in word)):
                        return True
        except Exception: pass
    return False

def ocr_read_number(img: Image.Image, btn_def: Optional[dict] = None) -> Optional[int]:
    """Read a numeric value using OCR from an image region."""
    if not OCR_OK or not img: return None
    try:
        text = pytesseract.image_to_string(enhance_ocr(img, btn_def=btn_def), config="--psm 7")
        nums = re.findall(r'\d+', text)
        if nums: return int(nums[0])
    except Exception: pass
    return None

# ── Pixel & Color Detection ───────────────────────────────────────────────────
def pixel_match(img: Image.Image, pixel_cfg: Optional[dict], threshold: int = 35) -> bool:
    """pixel_cfg: {points:[(x,y),...], colors:[(r,g,b),...], count_required:N}"""
    if not img or not pixel_cfg: return False
    points = pixel_cfg.get("points", [])
    colors = pixel_cfg.get("colors", [])
    required = pixel_cfg.get("count_required", max(1, len(points) // 2))
    if not points: return False
    matched = 0
    for pt, col in zip(points, colors):
        try:
            act = img.getpixel((int(pt[0]), int(pt[1])))
            dist = math.sqrt(sum((a-b)**2 for a,b in zip(act[:3], col[:3])))
            if dist < threshold: matched += 1
        except Exception: pass
    return matched >= required

def color_match(img: Image.Image, color_data: Optional[dict], *, pixel_ids=None, color=None, threshold=None, min_match=None):
    """Multi-color pixel checker supporting point rules and grouped conditions."""
    if not img or not color_data:
        return {} if (pixel_ids is None and color is None) else False

    points = color_data.get("points", [])

    def _rgb(c):
        if isinstance(c, (list, tuple)) and len(c) >= 3:
            return c[:3]
        s = str(c).lstrip("#")
        return [int(s[0:2],16), int(s[2:4],16), int(s[4:6],16)]

    def _dist(px, col):
        return math.sqrt(sum((a-b)**2 for a,b in zip(px[:3], _rgb(col))))

    # Ad-hoc call
    if pixel_ids is not None or color is not None:
        ids  = pixel_ids if pixel_ids is not None else list(range(len(points)))
        tgt  = color if color is not None else [128,128,128]
        thr  = threshold if threshold is not None else 40
        need = min_match if min_match is not None else len(ids)
        matched = 0
        for i in ids:
            if i >= len(points): continue
            try:
                px = img.getpixel((int(points[i][0]), int(points[i][1])))
                if _dist(px, tgt) <= thr: matched += 1
            except Exception: pass
        return matched >= need

    # Group-based call
    groups  = color_data.get("groups", [])
    results = {}
    for g in groups:
        ids  = g.get("pixel_ids", [])
        tgt  = g.get("color", [128,128,128])
        thr  = g.get("threshold", 40)
        need = g.get("min_match", max(1, len(ids)))
        matched = 0
        for i in ids:
            if i >= len(points): continue
            try:
                px = img.getpixel((int(points[i][0]), int(points[i][1])))
                if _dist(px, tgt) <= thr: matched += 1
            except Exception: pass
        results[g.get("name", f"group_{len(results)}")] = (matched >= need)
    results["_any"] = any(v for k, v in results.items() if k != "_any")
    return results

# ── OpenCV Template Matching ──────────────────────────────────────────────────
def image_match(img: Image.Image, template_b64: Optional[str], threshold: float = 0.8) -> bool:
    """Template matching using OpenCV TM_CCOEFF_NORMED with fallback to cross-correlation."""
    if not img or not template_b64: return False
    try:
        tpl_data = base64.b64decode(template_b64)
        tpl = Image.open(io.BytesIO(tpl_data)).convert("RGB")

        # 1. OpenCV fast sliding-window matching (preferred)
        if CV2_OK and np is not None:
            img_np = np.array(img)
            tpl_np = np.array(tpl)
            ih, iw = img_np.shape[:2]
            th, tw = tpl_np.shape[:2]
            if th > ih or tw > iw:
                tpl_np = cv2.resize(tpl_np, (min(tw, iw), min(th, ih)))
            img_bgr = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)
            tpl_bgr = cv2.cvtColor(tpl_np, cv2.COLOR_RGB2BGR)
            res = cv2.matchTemplate(img_bgr, tpl_bgr, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(res)
            return float(max_val) >= threshold

        # 2. NumPy / PIL cross-correlation fallback
        if np is not None:
            resized = img.resize(tpl.size, Image.Resampling.LANCZOS).convert("RGB")
            a = np.array(resized, dtype=float)
            b = np.array(tpl, dtype=float)
            norm_a = a - a.mean(); norm_b = b - b.mean()
            denom = (np.std(a) * np.std(b)) + 1e-9
            corr = (norm_a * norm_b).mean() / denom
            return float(corr) >= threshold

        return False
    except Exception: return False

# ── Highlight Detection ───────────────────────────────────────────────────────
def _detect_highlight(img: Image.Image, btn_def: Optional[dict], threshold: float = 0.25) -> Tuple[bool, float, float, float, float, int, int]:
    """Detect highlight border on an already-captured PIL image."""
    try:
        bd = btn_def or {}
        passes = bd.get("ocr_passes") or [
            {"channel": "selected-invert", "threshold": 0,
             "sel_color": [255, 255, 255], "sel_radius": 60}
        ]
        p   = passes[0]
        pre = _apply_ocr_channel(
            img,
            p.get("channel", "selected-invert"),
            p.get("threshold", 0),
            extra=p
        )
        w, h   = pre.size
        pixels = pre.load()
        band   = max(3, min(w, h) // 10)

        glob_thr   = bd.get("hl_threshold", threshold)
        min_edges  = bd.get("hl_min_edges", 3)

        thr_top   = bd.get("hl_top_min",   0.0) or glob_thr
        thr_bot   = bd.get("hl_bot_min",   0.0) or glob_thr
        thr_left  = bd.get("hl_left_min",  0.0) or glob_thr
        thr_right = bd.get("hl_right_min", 0.0) or glob_thr

        def _ratio(xs, ys):
            total = len(xs) * len(ys)
            if total == 0: return 0.0
            black = sum(1 for x in xs for y in ys if pixels[x, y] < 80)
            return black / total

        top    = _ratio(range(w),           range(band))
        bottom = _ratio(range(w),           range(h - band, h))
        left   = _ratio(range(band),        range(h))
        right  = _ratio(range(w - band, w), range(h))

        edges_passed = sum([
            top    >= thr_top,
            bottom >= thr_bot,
            left   >= thr_left,
            right  >= thr_right,
        ])
        highlighted = edges_passed >= min_edges
        return highlighted, top, bottom, left, right, edges_passed, min_edges
    except Exception:
        return False, 0.0, 0.0, 0.0, 0.0, 0, 0

def detect_button(img: Image.Image, btn_def: dict, sct=None) -> Tuple[bool, bool, bool, bool]:
    """Test button detection methods on a given image. Returns (ocr_r, pix_r, img_r, final)."""
    ocr_r = pix_r = img_r = False

    if btn_def.get("ocr_enabled") and img:
        ocr_r = ocr_find_text(img, btn_def.get("ocr_text", ""),
                               btn_def.get("ocr_psm", 6),
                               btn_def.get("ocr_min_conf", 18),
                               btn_def=btn_def)

    if btn_def.get("pixel_enabled") and img:
        pdata = btn_def.get("pixel_data")
        pix_r = pixel_match(img, pdata, btn_def.get("pixel_threshold", 35))

    if btn_def.get("image_enabled") and img:
        img_r = image_match(img, btn_def.get("image_template"),
                             btn_def.get("image_threshold", 0.8))

    enabled = [ocr_r if btn_def.get("ocr_enabled") else None,
               pix_r if btn_def.get("pixel_enabled") else None,
               img_r if btn_def.get("image_enabled") else None]
    active = [v for v in enabled if v is not None]
    if not active: final = False
    else:
        mode = btn_def.get("match_mode", "any1")
        if   mode == "any1": final = any(active)
        elif mode == "any2": final = sum(active) >= 2
        elif mode == "all":  final = all(active)
        else:                final = any(active)

    return ocr_r, pix_r, img_r, final
