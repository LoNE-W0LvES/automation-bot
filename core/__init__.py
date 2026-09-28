"""
core package — Modular automation bot framework
"""

from .theme import (
    T, _ss, _center_on_screen, _vdesk,
    _mkbtn, _sep, _sep_v, _section_lbl, _section_lbl_small
)

from .models import (
    DEFAULT_BTN, DEFAULT_CTRL
)

from .vision import (
    capture, capture_sct,
    _apply_ocr_channel, enhance_ocr, ocr_find_text, ocr_read_number,
    pixel_match, color_match, image_match, _detect_highlight, detect_button,
    OCR_CHANNELS, OCR_DEFAULT_PASSES,
    MSS_OK, PIL_OK, OCR_OK, CV2_OK
)

from .macro import (
    MacroRecorder, MacroPlayer
)

from .engine import (
    ScriptEngine
)

from .dialogs import (
    RegionSelector, RegionHighlighter, PersistentHighlight, RegionEditBar,
    PixelPickerDlg, ScreenColorPicker, ColorPickerDlg,
    ButtonDefDialog, ControlDefDialog, PythonHighlighter, PopupPreview
)
