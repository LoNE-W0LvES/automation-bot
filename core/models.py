"""
core/models.py — Default schemas and models for buttons and controls
"""

DEFAULT_BTN = {
    "name": "",
    "btn_type": "button",         # button | region
    "region": None,               # [x,y,w,h]
    "region_visible": False,
    # OCR
    "ocr_enabled": True,
    "ocr_text": "",
    "ocr_psm": 6,
    "ocr_min_conf": 18,
    # Pixel
    "pixel_enabled": False,
    "pixel_data": None,           # {points:[], colors:[], count_required:N}
    "pixel_threshold": 35,
    "pixel_pos_visible": False,
    # Color (multi-color per-point matching)
    "color_enabled": False,
    "color_data": None,           # {points:[], rules:[], groups:[]}
    # Image
    "image_enabled": False,
    "image_template": None,       # base64 PNG
    "image_threshold": 0.8,
    # Highlight detection
    "hl_enabled":   False,        # count hl result in match logic
    "hl_threshold": 0.25,         # global per-edge ratio floor (0.0–1.0)
    "hl_min_edges": 3,            # how many edges must pass to count as highlighted
    "hl_top_min":   0.0,          # per-edge override (0.0 = use global hl_threshold)
    "hl_bot_min":   0.0,
    "hl_left_min":  0.0,
    "hl_right_min": 0.0,
    "image_region": None,         # if None, same as region
    "image_same_as_ocr": True,
    # Match logic
    "match_mode": "any1",         # any1 | any2 | all
    # Snapshot — base64 PNG captured right after region selection.
    # Always saved regardless of whether image detection is enabled.
    # Used to restore pixel picker / template preview when editing.
    "region_snapshot": None,
}

DEFAULT_CTRL = {
    "name": "",
    "ctrl_type": "selector",      # selector | slider
    "options": [],                # for selector
    "selected": "",               # for selector
    "min": 0,                     # for slider
    "max": 100,                   # for slider
    "value": 0,                   # for slider
    "step": 1,                    # for slider
}
