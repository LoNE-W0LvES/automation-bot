#!/usr/bin/env python3
"""
Automation Bot — PySide6 · Driver Mode
Modular Edition: Uses core package for Vision, Macros, Engine, Dialogs, and Theme.
"""

import sys
import os
import time
import datetime
import json
import math
import random
import copy
import traceback

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QSpinBox, QDoubleSpinBox, QTextEdit,
    QGroupBox, QSplitter, QDialog, QDialogButtonBox,
    QListWidget, QListWidgetItem, QCheckBox, QMessageBox,
    QTabWidget, QComboBox, QScrollArea, QFrame, QSizePolicy,
    QLineEdit, QSlider, QRadioButton, QButtonGroup, QColorDialog,
    QInputDialog, QFormLayout, QGridLayout, QStackedWidget,
    QFileDialog
)
from PySide6.QtCore import Qt, Signal, QObject, QThread, QRect, QTimer, QPoint
from PySide6.QtGui import (
    QPixmap, QImage, QPainter, QPen, QColor,
    QFont, QBrush, QSyntaxHighlighter, QTextCharFormat, QCursor
)

try:
    from PIL import Image, ImageDraw, ImageGrab, ImageEnhance
except ImportError:
    pass

try:
    import pytesseract
except ImportError:
    pytesseract = None

try:
    import keyboard as _kb
except ImportError:
    _kb = None

from keys import (
    INTERCEPTION_OK, WIN32_OK, KEYBOARD_OK, INTERCEPTION_ERR,
    backend_status, set_kb_backend, set_mouse_backend,
    get_kb_backend, get_mouse_backend,
    kb_down, kb_up, kb_press, kb_hold, kb_combo,
    kb_toggleLoop, kb_toggleHold,
    stop_all_loops, release_all_held, cleanup as keys_cleanup,
    key_down, key_up, key_tap, key_combo,
    keyboard_press,
    mouse_pos, mouse_move, mouse_smooth, mouse_down, mouse_up,
    mouse_click, click_at_pos, mouse_human,
    get_pos, human_move,
    resolve as kb_resolve, lookup as kb_lookup, KEY_MAP, ALIASES,
)

from core import (
    T, _ss, _center_on_screen, _vdesk,
    _mkbtn, _sep, _sep_v, _section_lbl, _section_lbl_small,
    DEFAULT_BTN, DEFAULT_CTRL,
    capture, capture_sct,
    _apply_ocr_channel, enhance_ocr, ocr_find_text, ocr_read_number,
    pixel_match, color_match, image_match, _detect_highlight, detect_button,
    OCR_CHANNELS, OCR_DEFAULT_PASSES,
    MSS_OK, PIL_OK, OCR_OK, CV2_OK,
    MacroRecorder, MacroPlayer,
    ScriptEngine,
    RegionSelector, RegionHighlighter, PersistentHighlight, RegionEditBar,
    PixelPickerDlg, ScreenColorPicker, ColorPickerDlg,
    ButtonDefDialog, ControlDefDialog, PythonHighlighter, PopupPreview
)

CONFIG_FILE = "automation_bot_config.json"
DEFAULT_CFG = os.path.join("configs", "default.cfg")
_REGION_EDIT_LOCKED = True

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Automation Bot  ·  Driver Mode")
        self.resize(1300, 860); self.setMinimumSize(1000, 640)
        self.setStyleSheet(_ss())

        # State
        self._btn_defs: list = []
        self._ctrl_defs: list = []
        self._game_region = None
        # Macro library: [{name:str, events:list}, ...]
        self._macros: list = []
        self._active_macro_idx: int = -1
        self._macro_events: list = []       # recorder buffer (unsaved)
        self._macro_recorder = MacroRecorder()
        self._macro_player: MacroPlayer | None = None
        self._active_highlighters: list = []
        self._active_edit_overlay = None   # editable single-region overlay
        self._active_edit_bar     = None   # its floating toolbar
        self._persistent_highlights: dict = {}
        # Hotkeys: {action_id: combo_string}
        self._hotkeys: dict = {
            "run_active_script":  "",
            "stop_active_script": "",
            "stop_all_scripts":   "",
            "rec_start":          "",
            "rec_stop":           "",
            "play_macro":         "",
            "stop_macro":         "",
        }
        self._hotkey_handles: dict = {}   # action_id -> kb handle
        self._active_config_name: str = ""  # currently loaded config (no extension)

        # Multi-script state — list of {name, code} dicts; engines run independently
        self._scripts: list = []          # [{name, code}, ...]
        self._script_engines: dict = {}   # index -> ScriptEngine | None
        self._active_script_idx: int = -1 # which script is shown in the editor

        self._build_ui()
        self._load_config()

        # Poll for runtime interception errors (set by bg threads) and show popup
        self._drv_err_timer = QTimer(self)
        self._drv_err_timer.timeout.connect(self._check_driver_runtime_err)
        self._drv_err_timer.start(500)
        # If no scripts loaded (first launch), create a default one
        if not self._scripts:
            self._scripts = [{"name": "script_1", "code":
                "# Script 1\n"
                "while True:\n"
                "    buttons.scan()\n"
                "    sleep(0.5)\n"
            }]
            self._refresh_script_list()
            self._script_list.setCurrentRow(0)

    def _check_driver_runtime_err(self):
        pass  # keys.py handles its own runtime fallback logging

    def _log_msg(self, msg: str):
        """Append a timestamped message to the log panel."""
        import datetime
        ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f'<span style="color:#4a5568;">[{ts}]</span> {msg}'
        self._log.append(line)
        # Auto-scroll to bottom
        sb = self._log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _set_mouse_driver(self, mode: str):
        if mode == "interception" and not INTERCEPTION_OK:
            QMessageBox.warning(self, "Interception Unavailable",
                f"Interception not available:\n\n{INTERCEPTION_ERR}\n\nFalling back to Win32.")
            mode = "win32"
        set_mouse_backend(mode)
        self._btn_mouse_ic.setChecked(mode == "interception")
        self._btn_mouse_w32.setChecked(mode == "win32")
        self._log_msg(f"\U0001F5B1 Mouse driver: {mode.upper()}")

    def _set_kb_driver(self, mode: str):
        if mode == "interception" and not INTERCEPTION_OK:
            QMessageBox.warning(self, "Interception Unavailable",
                f"Interception not available:\n\n{INTERCEPTION_ERR}\n\nFalling back to Win32.")
            mode = "win32"
        if mode == "keyboard" and not KEYBOARD_OK:
            QMessageBox.warning(self, "keyboard lib Unavailable",
                "keyboard library not installed.\npip install keyboard\nFalling back to Win32.")
            mode = "win32"
        set_kb_backend(mode)
        self._btn_kb_ic.setChecked(mode == "interception")
        self._btn_kb_w32.setChecked(mode == "win32")
        self._btn_kb_kblib.setChecked(mode == "keyboard")
        self._log_msg(f"\u2328 Keyboard driver: {mode.upper()}")

    def _build_ui(self):
        cw = QWidget(); self.setCentralWidget(cw)
        root = QVBoxLayout(cw); root.setContentsMargins(0,0,0,0); root.setSpacing(0)

        # ── Top bar ──────────────────────────────────────────────────────────
        topbar = QWidget(); topbar.setFixedHeight(50)
        topbar.setStyleSheet(f"""background:qlineargradient(x1:0,y1:0,x2:1,y2:0,
            stop:0 #0a0c14,stop:0.5 {T['card']},stop:1 #0a0c14);
            border-bottom:1px solid {T['border']};""")
        tl = QHBoxLayout(topbar); tl.setContentsMargins(16,0,16,0)
        title = QLabel("AUTOMATION BOT")
        title.setStyleSheet(f"color:{T['accent']};font-size:16px;font-weight:bold;letter-spacing:5px;")
        tl.addWidget(title)
        tl.addSpacing(16)

        # ── Driver selector — Mouse & Keyboard independently ─────────────────
        drv_wrap = QWidget(); drv_wrap.setStyleSheet("background:transparent;")
        drv_hl = QHBoxLayout(drv_wrap); drv_hl.setContentsMargins(0,0,0,0); drv_hl.setSpacing(8)
        _drv_base = "font-size:10px;font-weight:bold;letter-spacing:1px;padding:3px 9px;border:1px solid;"

        # Mouse  🖱 IC | W32
        mouse_grp = QWidget(); mouse_grp.setStyleSheet("background:transparent;")
        _mhl = QHBoxLayout(mouse_grp); _mhl.setContentsMargins(0,0,0,0); _mhl.setSpacing(0)
        _lm = QLabel("🖱"); _lm.setStyleSheet(f"color:{T['dim']};font-size:11px;padding-right:3px;background:transparent;")
        _mhl.addWidget(_lm)
        self._btn_mouse_ic  = QPushButton("IC");  self._btn_mouse_ic.setFixedHeight(24);  self._btn_mouse_ic.setCheckable(True)
        self._btn_mouse_w32 = QPushButton("W32"); self._btn_mouse_w32.setFixedHeight(24); self._btn_mouse_w32.setCheckable(True)
        self._btn_mouse_ic.setEnabled(INTERCEPTION_OK)
        self._btn_mouse_ic.setToolTip("Mouse: Interception driver" if INTERCEPTION_OK else "Interception not available")
        self._btn_mouse_w32.setToolTip("Mouse: win32api")
        _ic_ss  = lambda r: (f"QPushButton{{{_drv_base}background:{T['card']};color:{T['dim']};border-color:{T['border2']};border-radius:{r};}}"
                             f"QPushButton:checked{{background:{T['green2']};color:white;border-color:{T['green']};}}"
                             f"QPushButton:hover:!checked{{color:{T['fg']};border-color:{T['border2']};}}"
                             f"QPushButton:disabled{{color:{T['dim2']};border-color:{T['border']};background:{T['bg']};}}")
        _w32_ss = lambda r: (f"QPushButton{{{_drv_base}background:{T['card']};color:{T['dim']};border-color:{T['border2']};border-radius:{r};}}"
                             f"QPushButton:checked{{background:{T['accent2']};color:white;border-color:{T['accent']};}}"
                             f"QPushButton:hover:!checked{{color:{T['fg']};border-color:{T['border2']};}}")
        _kbl_ss = lambda r: (f"QPushButton{{{_drv_base}background:{T['card']};color:{T['dim']};border-color:{T['border2']};border-radius:{r};}}"
                             f"QPushButton:checked{{background:{T['yellow']};color:#000;border-color:{T['orange']};}}"
                             f"QPushButton:hover:!checked{{color:{T['fg']};border-color:{T['border2']};}}"
                             f"QPushButton:disabled{{color:{T['dim2']};border-color:{T['border']};background:{T['bg']};}}")
        self._btn_mouse_ic.setStyleSheet(_ic_ss("5px 0 0 5px"))
        self._btn_mouse_w32.setStyleSheet(_w32_ss("0 5px 5px 0"))
        self._btn_mouse_grp = QButtonGroup(self); self._btn_mouse_grp.addButton(self._btn_mouse_ic,0); self._btn_mouse_grp.addButton(self._btn_mouse_w32,1)
        if INTERCEPTION_OK: self._btn_mouse_ic.setChecked(True)
        else:               self._btn_mouse_w32.setChecked(True)
        self._btn_mouse_ic.clicked.connect( lambda: self._set_mouse_driver("interception"))
        self._btn_mouse_w32.clicked.connect(lambda: self._set_mouse_driver("win32"))
        _mhl.addWidget(self._btn_mouse_ic); _mhl.addWidget(self._btn_mouse_w32)
        drv_hl.addWidget(mouse_grp)

        # Keyboard  ⌨ IC | W32 | KBL
        kb_grp = QWidget(); kb_grp.setStyleSheet("background:transparent;")
        _khl = QHBoxLayout(kb_grp); _khl.setContentsMargins(0,0,0,0); _khl.setSpacing(0)
        _lk = QLabel("⌨"); _lk.setStyleSheet(f"color:{T['dim']};font-size:11px;padding-right:3px;background:transparent;")
        _khl.addWidget(_lk)
        self._btn_kb_ic     = QPushButton("IC");  self._btn_kb_ic.setFixedHeight(24);     self._btn_kb_ic.setCheckable(True)
        self._btn_kb_w32    = QPushButton("W32"); self._btn_kb_w32.setFixedHeight(24);    self._btn_kb_w32.setCheckable(True)
        self._btn_kb_kblib  = QPushButton("KBL"); self._btn_kb_kblib.setFixedHeight(24);  self._btn_kb_kblib.setCheckable(True)
        self._btn_kb_ic.setEnabled(INTERCEPTION_OK); self._btn_kb_kblib.setEnabled(KEYBOARD_OK)
        self._btn_kb_ic.setToolTip("Keyboard: Interception driver" if INTERCEPTION_OK else "Interception not available")
        self._btn_kb_w32.setToolTip("Keyboard: win32api.keybd_event")
        self._btn_kb_kblib.setToolTip("Keyboard: keyboard lib (fallback)" if KEYBOARD_OK else "keyboard lib not installed")
        self._btn_kb_ic.setStyleSheet(_ic_ss("5px 0 0 5px"))
        self._btn_kb_w32.setStyleSheet(_w32_ss("0"))
        self._btn_kb_kblib.setStyleSheet(_kbl_ss("0 5px 5px 0"))
        self._btn_kb_grp = QButtonGroup(self); self._btn_kb_grp.addButton(self._btn_kb_ic,0); self._btn_kb_grp.addButton(self._btn_kb_w32,1); self._btn_kb_grp.addButton(self._btn_kb_kblib,2)
        if INTERCEPTION_OK:  self._btn_kb_ic.setChecked(True)
        elif WIN32_OK:       self._btn_kb_w32.setChecked(True)
        else:                self._btn_kb_kblib.setChecked(True)
        self._btn_kb_ic.clicked.connect(    lambda: self._set_kb_driver("interception"))
        self._btn_kb_w32.clicked.connect(   lambda: self._set_kb_driver("win32"))
        self._btn_kb_kblib.clicked.connect( lambda: self._set_kb_driver("keyboard"))
        _khl.addWidget(self._btn_kb_ic); _khl.addWidget(self._btn_kb_w32); _khl.addWidget(self._btn_kb_kblib)
        drv_hl.addWidget(kb_grp)

        tl.addWidget(drv_wrap)
        tl.addSpacing(8)
        self._status_lbl = QLabel("IDLE")
        self._status_lbl.setStyleSheet(f"color:{T['dim']};font-size:11px;font-weight:bold;"
                                        f"letter-spacing:2px;border:1px solid {T['border']};"
                                        f"border-radius:10px;padding:3px 12px;")
        tl.addWidget(self._status_lbl)
        tl.addStretch()

        root.addWidget(topbar)

        # ── Main splitter: LEFT tabs | RIGHT (script IDE + log) ──────────────
        spl = QSplitter(Qt.Horizontal)
        spl.setContentsMargins(0,0,0,0)
        spl.setHandleWidth(2)
        spl.setStyleSheet("QSplitter::handle{background:#1c2030;}")
        root.addWidget(spl, 1)

        # ── LEFT: nav tabs (BUTTONS / CONTROLS / MACRO / SETTINGS) ──────────
        left = QWidget()
        left.setMinimumWidth(360); left.setMaximumWidth(480)
        lv = QVBoxLayout(left); lv.setContentsMargins(0,0,0,0); lv.setSpacing(0)
        self._tabs = QTabWidget()
        self._tabs.currentChanged.connect(self._on_tab_changed)
        lv.addWidget(self._tabs, 1)
        spl.addWidget(left)

        # ── RIGHT: vertical splitter (top=content, bottom=log) ───────────────
        right_spl = QSplitter(Qt.Vertical)
        right_spl.setHandleWidth(2)
        right_spl.setStyleSheet("QSplitter::handle{background:#1c2030;}")
        spl.addWidget(right_spl)

        # TOP content: QStackedWidget
        #   page 0 → script IDE  (shown when SCRIPT tab is active)
        #   page 1 → empty dark panel (shown for all other tabs)
        self._right_stack = QStackedWidget()
        right_spl.addWidget(self._right_stack)

        # Page 1: empty panel (shown when not on script tab)
        empty_page = QWidget()
        empty_page.setStyleSheet(f"background:{T['bg']};")
        self._right_stack.addWidget(empty_page)   # index 0

        # Script IDE page is built inside _build_tab_script and
        # added to self._right_stack at index 1

        # BOTTOM: log panel ───────────────────────────────────────────────────
        log_panel = QWidget()
        log_panel.setStyleSheet(f"background:{T['panel']};")
        log_panel.setMinimumHeight(80)
        lp = QVBoxLayout(log_panel); lp.setContentsMargins(8,4,8,6); lp.setSpacing(2)

        log_hdr = QHBoxLayout()
        log_hdr.addWidget(_section_lbl("LOG"))
        log_hdr.addStretch()
        btn_clr = QPushButton("✕ Clear")
        btn_clr.setFixedHeight(20)
        btn_clr.setStyleSheet(
            f"QPushButton{{background:{T['dim2']};color:{T['fg2']};border:none;"
            f"border-radius:3px;font-size:10px;padding:0 8px;}}"
            f"QPushButton:hover{{background:{T['border2']};}}")
        btn_clr.clicked.connect(lambda: self._log.clear())
        log_hdr.addWidget(btn_clr)
        lp.addLayout(log_hdr)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        lp.addWidget(self._log, 1)
        right_spl.addWidget(log_panel)

        right_spl.setSizes([620, 200])
        spl.setSizes([420, 860])

        # Floating popup preview window (created lazily, never destroyed)
        self._popup_preview: PopupPreview | None = None

        # ── Build nav tabs ────────────────────────────────────────────────────
        self._build_tab_buttons()
        self._build_tab_controls()
        self._build_tab_script()     # also inserts IDE page into right_stack
        self._build_tab_macro()
        self._build_tab_settings()

    def _on_tab_changed(self, idx):
        """Switch right panel between IDE and empty based on active tab."""
        tab_text = self._tabs.tabText(idx)
        self._right_stack.setCurrentIndex(1 if tab_text == "SCRIPT" else 0)

    # ─── TAB: BUTTONS ────────────────────────────────────────────────────────

    def _build_tab_buttons(self):
        t = QWidget(); tv = QVBoxLayout(t); tv.setContentsMargins(10,10,10,10); tv.setSpacing(6)
        self._tabs.addTab(t, "BUTTONS")

        # Game window region
        grp_gw = QGroupBox("GAME WINDOW REGION")
        gv = QVBoxLayout(grp_gw); gv.setSpacing(5)
        self._lbl_game_reg = QLabel("Not set — set this to lock all button positions")
        self._lbl_game_reg.setStyleSheet(f"color:{T['fg2']};font-size:10px;")
        gv.addWidget(self._lbl_game_reg)

        gw_row1 = QHBoxLayout()
        gw_row1.addWidget(_mkbtn("📐 Set Region", T['dim2'], self._set_game_region, sz=10, h=26))
        self._btn_toggle_game = QPushButton("👁 Show")
        _tg_ss = (f"QPushButton{{background:{T['dim2']};color:{T['fg2']};border:none;"
                  f"border-radius:4px;font-size:10px;padding:0 10px;font-weight:bold;}}"
                  f"QPushButton:hover{{background:{T['border2']};color:{T['fg']};}}")
        self._btn_toggle_game.setStyleSheet(_tg_ss)
        self._btn_toggle_game.setFixedHeight(26)
        self._btn_toggle_game.clicked.connect(self._toggle_game_region)
        gw_row1.addWidget(self._btn_toggle_game)
        gv.addLayout(gw_row1)

        # Expand / shrink spinboxes (visible once a region is set)
        self._gw_resize_widget = QWidget()
        gw_resize_lay = QHBoxLayout(self._gw_resize_widget)
        gw_resize_lay.setContentsMargins(0,0,0,0); gw_resize_lay.setSpacing(4)
        gw_resize_lay.addWidget(QLabel("X:")); self._sp_gx = QSpinBox()
        self._sp_gx.setRange(-9999,9999); self._sp_gx.setFixedWidth(62)
        gw_resize_lay.addWidget(self._sp_gx)
        gw_resize_lay.addWidget(QLabel("Y:")); self._sp_gy = QSpinBox()
        self._sp_gy.setRange(-9999,9999); self._sp_gy.setFixedWidth(62)
        gw_resize_lay.addWidget(self._sp_gy)
        gw_resize_lay.addWidget(QLabel("W:")); self._sp_gw = QSpinBox()
        self._sp_gw.setRange(1,9999); self._sp_gw.setFixedWidth(62)
        gw_resize_lay.addWidget(self._sp_gw)
        gw_resize_lay.addWidget(QLabel("H:")); self._sp_gh = QSpinBox()
        self._sp_gh.setRange(1,9999); self._sp_gh.setFixedWidth(62)
        gw_resize_lay.addWidget(self._sp_gh)
        btn_apply_gr = QPushButton("Apply")
        btn_apply_gr.setFixedHeight(24)
        btn_apply_gr.setStyleSheet(f"QPushButton{{background:{T['accent2']};color:white;border:none;"
                                    f"border-radius:3px;font-size:10px;padding:0 8px;}}"
                                    f"QPushButton:hover{{background:{T['accent']};color:black;}}")
        btn_apply_gr.clicked.connect(self._apply_game_resize)
        gw_resize_lay.addWidget(btn_apply_gr); gw_resize_lay.addStretch()
        self._gw_resize_widget.setVisible(False)
        gv.addWidget(self._gw_resize_widget)
        tv.addWidget(grp_gw)
        # track game overlay
        self._game_overlay: RegionHighlighter | None = None

        # Button list
        tv.addWidget(_section_lbl("CONFIGURED BUTTONS / REGIONS"))
        self._btn_list = QListWidget(); self._btn_list.setMinimumHeight(180)
        tv.addWidget(self._btn_list)

        list_btns = QHBoxLayout()
        list_btns.addWidget(_mkbtn("＋ Add Button/Region", T['accent2'], self._add_button, sz=11, h=28))
        list_btns.addWidget(_mkbtn("✏ Edit", T['dim2'], self._edit_button, sz=11, h=28))
        list_btns.addWidget(_mkbtn("✕ Remove", T['red2'], self._remove_button, sz=11, h=28))
        tv.addLayout(list_btns)

        vis_btns = QHBoxLayout()
        vis_btns.addWidget(_mkbtn("👁 Show Region",  T['accent2'], self._show_selected_region, sz=10, h=26))
        vis_btns.addWidget(_mkbtn("☰ Multi Show",   T['dim2'],    self._show_multi_regions,   sz=10, h=26))
        vis_btns.addWidget(_mkbtn("👁 Show All",     T['dim2'],    self._show_all_regions,     sz=10, h=26))
        vis_btns.addWidget(_mkbtn("🙈 Hide All",     T['dim2'],    self._hide_all_regions,     sz=10, h=26))
        tv.addLayout(vis_btns)

        tv.addWidget(_sep())

        # ── Quick test + inline preview ───────────────────────────────────────
        test_hdr = QHBoxLayout()
        test_hdr.addWidget(_section_lbl("QUICK TEST"))
        test_hdr.addStretch()
        self._btn_popup_prev = QPushButton("⧉ Pop Out")
        self._btn_popup_prev.setFixedHeight(20)
        self._btn_popup_prev.setStyleSheet(
            f"QPushButton{{background:{T['dim2']};color:{T['fg2']};border:none;"
            f"border-radius:3px;font-size:9px;padding:0 7px;font-weight:bold;}}"
            f"QPushButton:hover{{background:{T['border2']};color:{T['fg']};}}")
        self._btn_popup_prev.clicked.connect(self._toggle_popup_preview)
        test_hdr.addWidget(self._btn_popup_prev)
        tv.addLayout(test_hdr)

        test_row = QHBoxLayout()
        test_row.addWidget(_mkbtn("🔬 Test Selected Button", T['accent2'], self._test_button, sz=11, h=30))
        test_row.addWidget(_mkbtn("🖱 Test Click",           T['green2'],  self._test_click,  sz=11, h=30))
        tv.addLayout(test_row)

        # Inline preview (raw + preprocessed side by side, collapsible)
        _prev_ss = (f"background:{T['card']};border:1px solid {T['border']};"
                    f"border-radius:4px;color:{T['dim']};font-size:9px;")

        prev_row = QHBoxLayout(); prev_row.setSpacing(3)
        self._preview = QLabel("run test to see capture")
        self._preview.setAlignment(Qt.AlignCenter)
        self._preview.setStyleSheet(_prev_ss)
        self._preview.setMinimumHeight(100); self._preview.setMaximumHeight(180)
        prev_row.addWidget(self._preview, 1)

        pre_col = QVBoxLayout(); pre_col.setSpacing(1); pre_col.setContentsMargins(0,0,0,0)
        self._preview_pre_hdr = QLabel("preprocessed")
        self._preview_pre_hdr.setAlignment(Qt.AlignCenter)
        self._preview_pre_hdr.setFixedHeight(13)
        self._preview_pre_hdr.setStyleSheet(
            f"color:{T['dim']};font-size:8px;letter-spacing:1px;")
        pre_col.addWidget(self._preview_pre_hdr)
        self._preview_pre = QLabel("")
        self._preview_pre.setAlignment(Qt.AlignCenter)
        self._preview_pre.setStyleSheet(_prev_ss)
        self._preview_pre.setMinimumHeight(87); self._preview_pre.setMaximumHeight(167)
        pre_col.addWidget(self._preview_pre, 1)
        pre_wrap = QWidget(); pre_wrap.setLayout(pre_col)
        prev_row.addWidget(pre_wrap, 1)

        self._inline_preview_widget = QWidget()
        self._inline_preview_widget.setLayout(prev_row)
        tv.addWidget(self._inline_preview_widget, 1)

    # ─── TAB: CONTROLS ───────────────────────────────────────────────────────

    def _build_tab_controls(self):
        t = QWidget(); tv = QVBoxLayout(t); tv.setContentsMargins(10,10,10,10); tv.setSpacing(6)
        self._tabs.addTab(t, "CONTROLS")

        tv.addWidget(QLabel("Logic controls (selectors, sliders) available in scripts:"))

        self._ctrl_list = QListWidget(); self._ctrl_list.setMinimumHeight(180)
        tv.addWidget(self._ctrl_list)

        ctrl_btns = QHBoxLayout()
        ctrl_btns.addWidget(_mkbtn("＋ Add Control", T['green2'], self._add_control, sz=11, h=28))
        ctrl_btns.addWidget(_mkbtn("✏ Edit", T['dim2'], self._edit_control, sz=11, h=28))
        ctrl_btns.addWidget(_mkbtn("✕ Remove", T['red2'], self._remove_control, sz=11, h=28))
        tv.addLayout(ctrl_btns)

        tv.addWidget(_sep())
        tv.addWidget(_section_lbl("RUNTIME VALUES"))
        self._ctrl_runtime_widget = QWidget()
        self._ctrl_runtime_layout = QVBoxLayout(self._ctrl_runtime_widget)
        self._ctrl_runtime_layout.setContentsMargins(0,0,0,0)
        scroll = QScrollArea(); scroll.setWidget(self._ctrl_runtime_widget)
        scroll.setWidgetResizable(True); scroll.setMinimumHeight(180)
        scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        tv.addWidget(scroll)
        tv.addStretch()

    # ─── TAB: SCRIPT ─────────────────────────────────────────────────────────
    # LEFT tab  = script list + per-script controls
    # RIGHT panel (right_stack page 1) = editor only
    # They are separate widgets in separate panels — the main splitter
    # already divides left from right.

    def _build_tab_script(self):
        # ── LEFT: script list inside the tab ─────────────────────────────────
        t = QWidget()
        tv = QVBoxLayout(t); tv.setContentsMargins(0,0,0,0); tv.setSpacing(0)
        self._tabs.addTab(t, "SCRIPT")

        # Header bar
        hdr = QWidget()
        hdr.setFixedHeight(32)
        hdr.setStyleSheet(f"background:{T['card']};border-bottom:1px solid {T['border']};")
        hl = QHBoxLayout(hdr); hl.setContentsMargins(8,4,8,4); hl.setSpacing(4)
        hl.addWidget(QLabel("SCRIPTS"))
        hl.addStretch()
        hl.addWidget(_mkbtn("＋", T['accent2'], self._new_script,    sz=11, h=22, min_w=28))
        hl.addWidget(_mkbtn("✕", T['red2'],    self._delete_script, sz=11, h=22, min_w=28))
        tv.addWidget(hdr)

        # Script list
        self._script_list = QListWidget()
        self._script_list.setStyleSheet(
            f"QListWidget{{background:{T['panel']};border:none;outline:none;padding:2px;}}"
            f"QListWidget::item{{padding:7px 10px;border-radius:3px;font-size:11px;color:{T['fg2']};}}"
            f"QListWidget::item:selected{{background:{T['accent2']};color:white;}}"
            f"QListWidget::item:hover:!selected{{background:{T['border2']};color:{T['fg']};}}")
        self._script_list.currentRowChanged.connect(self._on_script_selected)
        tv.addWidget(self._script_list, 1)

        # Bottom controls: name + run/stop + run all/stop all
        ctrl = QWidget()
        ctrl.setStyleSheet(f"background:{T['card']};border-top:1px solid {T['border']};")
        cl = QVBoxLayout(ctrl); cl.setContentsMargins(6,6,6,6); cl.setSpacing(4)

        name_row = QHBoxLayout(); name_row.setSpacing(4)
        name_row.addWidget(QLabel("Name:"))
        self._script_name_edit = QLineEdit()
        self._script_name_edit.setPlaceholderText("script name…")
        self._script_name_edit.setStyleSheet(
            f"background:{T['panel']};border:1px solid {T['border2']};border-radius:4px;"
            f"color:{T['fg']};padding:2px 6px;font-size:11px;")
        self._script_name_edit.textEdited.connect(self._on_script_name_edited)
        name_row.addWidget(self._script_name_edit)
        cl.addLayout(name_row)

        run_row = QHBoxLayout(); run_row.setSpacing(4)
        self._btn_run_active  = _mkbtn("▶ Run",      T['green2'], self._run_active_script,  sz=10, h=28)
        self._btn_stop_active = _mkbtn("⏹ Stop",     T['red2'],   self._stop_active_script, sz=10, h=28)
        self._btn_stop_active.setEnabled(False)
        run_row.addWidget(self._btn_run_active, 1)
        run_row.addWidget(self._btn_stop_active, 1)
        cl.addLayout(run_row)

        all_row = QHBoxLayout(); all_row.setSpacing(4)
        all_row.addWidget(_mkbtn("▶▶ Run All", T['dim2'], self._run_all_scripts,  sz=10, h=26), 1)
        all_row.addWidget(_mkbtn("⏹ Stop All", T['dim2'], self._stop_all_scripts, sz=10, h=26), 1)
        cl.addLayout(all_row)
        tv.addWidget(ctrl)

        # ── RIGHT: editor in right_stack (page 1) ────────────────────────────
        editor_root = QWidget()
        editor_root.setStyleSheet(f"background:#0b0d14;")
        ep = QVBoxLayout(editor_root); ep.setContentsMargins(0,0,0,0); ep.setSpacing(0)

        # ── Editor toolbar with Reference Names button ────────────────────────
        editor_toolbar = QWidget()
        editor_toolbar.setFixedHeight(30)
        editor_toolbar.setStyleSheet(
            f"background:{T['card']};border-bottom:1px solid {T['border']};")
        etl = QHBoxLayout(editor_toolbar)
        etl.setContentsMargins(6, 3, 6, 3); etl.setSpacing(4)
        etl.addStretch()
        self._btn_ref_names = QPushButton("📋 Reference Names")
        self._btn_ref_names.setCheckable(True)
        self._btn_ref_names.setFixedHeight(22)
        self._btn_ref_names.setStyleSheet(
            f"QPushButton{{background:{T['border2']};color:{T['fg2']};border:1px solid {T['border2']};"
            f"border-radius:4px;padding:0 10px;font-size:10px;font-weight:bold;letter-spacing:1px;}}"
            f"QPushButton:hover{{border-color:{T['accent']};color:{T['accent']};}}"
            f"QPushButton:checked{{background:{T['accent2']};border-color:{T['accent']};color:white;}}")
        self._btn_ref_names.clicked.connect(self._toggle_ref_names_panel)
        etl.addWidget(self._btn_ref_names)
        ep.addWidget(editor_toolbar)

        # ── Reference Names panel (hidden by default) ─────────────────────────
        self._ref_names_panel = QWidget()
        self._ref_names_panel.setStyleSheet(
            f"background:{T['panel']};border-bottom:1px solid {T['border']};")
        rnp = QVBoxLayout(self._ref_names_panel)
        rnp.setContentsMargins(8, 6, 8, 6); rnp.setSpacing(4)

        rn_hdr = QHBoxLayout()
        rn_title = QLabel("REFERENCE NAMES  —  click any name to copy")
        rn_title.setStyleSheet(
            f"color:{T['dim']};font-size:9px;font-weight:bold;letter-spacing:2px;")
        rn_hdr.addWidget(rn_title)
        rn_hdr.addStretch()
        rn_close = QPushButton("✕")
        rn_close.setFixedSize(18, 18)
        rn_close.setStyleSheet(
            f"QPushButton{{background:transparent;color:{T['dim']};border:none;font-size:11px;padding:0;}}"
            f"QPushButton:hover{{color:{T['red']};}}")
        rn_close.clicked.connect(lambda: (
            self._ref_names_panel.hide(),
            self._btn_ref_names.setChecked(False)
        ))
        rn_hdr.addWidget(rn_close)
        rnp.addLayout(rn_hdr)

        # Scrollable chip area
        self._ref_scroll_area = QScrollArea()
        self._ref_scroll_area.setWidgetResizable(True)
        self._ref_scroll_area.setFixedHeight(120)
        self._ref_scroll_area.setStyleSheet(
            "QScrollArea{border:none;background:transparent;}")
        self._ref_chips_widget = QWidget()
        self._ref_chips_widget.setStyleSheet("background:transparent;")
        self._ref_chips_layout = QVBoxLayout(self._ref_chips_widget)
        self._ref_chips_layout.setContentsMargins(0, 0, 0, 0)
        self._ref_chips_layout.setSpacing(4)
        self._ref_scroll_area.setWidget(self._ref_chips_widget)
        rnp.addWidget(self._ref_scroll_area)

        self._ref_copy_hint = QLabel("")
        self._ref_copy_hint.setStyleSheet(
            f"color:{T['green']};font-size:9px;font-style:italic;")
        self._ref_copy_hint.setFixedHeight(14)
        rnp.addWidget(self._ref_copy_hint)

        self._ref_names_panel.hide()
        ep.addWidget(self._ref_names_panel)

        self._script_edit = QTextEdit()
        self._script_edit.setStyleSheet(
            "background:#0b0d14;color:#d4d4d4;"
            "font-family:'Consolas','Courier New',monospace;"
            "font-size:12px;border:none;padding:10px;")
        self._script_edit.setTabStopDistance(28)
        PythonHighlighter(self._script_edit.document())
        self._script_edit.textChanged.connect(self._on_script_text_changed)
        ep.addWidget(self._script_edit, 1)

        self._script_status_bar = QLabel("")
        self._script_status_bar.setFixedHeight(22)
        self._script_status_bar.setStyleSheet(
            f"background:{T['card']};color:{T['dim']};font-size:10px;"
            f"padding:0 8px;border-top:1px solid {T['border']};")
        ep.addWidget(self._script_status_bar)

        # Register as page 1 in right_stack
        self._right_stack.addWidget(editor_root)  # index 1

    # ─── Reference Names panel helpers ───────────────────────────────────────

    def _toggle_ref_names_panel(self):
        if self._ref_names_panel.isVisible():
            self._ref_names_panel.hide()
            self._btn_ref_names.setChecked(False)
        else:
            self._rebuild_ref_names_chips()
            self._ref_names_panel.show()
            self._btn_ref_names.setChecked(True)

    def _rebuild_ref_names_chips(self):
        """Clear and repopulate the chip rows from current btn_defs + ctrl_defs."""
        # Clear old chips
        while self._ref_chips_layout.count():
            item = self._ref_chips_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Collect names by category
        categories = []

        btn_names  = [b["name"] for b in self._btn_defs
                      if b.get("name") and b.get("btn_type") == "button"]
        reg_names  = [b["name"] for b in self._btn_defs
                      if b.get("name") and b.get("btn_type") == "region"]
        sel_names  = [c["name"] for c in self._ctrl_defs
                      if c.get("name") and c.get("ctrl_type") == "selector"]
        sld_names  = [c["name"] for c in self._ctrl_defs
                      if c.get("name") and c.get("ctrl_type") == "slider"]

        if btn_names:
            categories.append(("BUTTONS",   T['accent'],  btn_names))
        if reg_names:
            categories.append(("REGIONS",   T['yellow'],  reg_names))
        if sel_names:
            categories.append(("SELECTORS", "#c792ea",    sel_names))
        if sld_names:
            categories.append(("SLIDERS",   T['orange'],  sld_names))

        if not categories:
            no_lbl = QLabel("  No buttons or controls defined yet.")
            no_lbl.setStyleSheet(f"color:{T['dim']};font-size:10px;font-style:italic;")
            self._ref_chips_layout.addWidget(no_lbl)
            self._ref_chips_layout.addStretch()
            return

        for cat_label, chip_color, names in categories:
            row_widget = QWidget()
            row_widget.setStyleSheet("background:transparent;")
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(4)

            cat_lbl = QLabel(cat_label)
            cat_lbl.setFixedWidth(130)
            cat_lbl.setStyleSheet(
                f"color:{T['dim']};font-size:8px;font-weight:bold;letter-spacing:1px;")
            row_layout.addWidget(cat_lbl)

            chips_wrap = QWidget()
            chips_wrap.setStyleSheet("background:transparent;")
            chips_flow = QHBoxLayout(chips_wrap)
            chips_flow.setContentsMargins(0, 0, 0, 0)
            chips_flow.setSpacing(4)

            for name in names:
                chip = QPushButton(name)
                chip.setFixedHeight(20)
                chip.setStyleSheet(
                    f"QPushButton{{background:{T['card']};color:{chip_color};"
                    f"border:1px solid {chip_color}33;border-radius:10px;"
                    f"padding:0 10px;font-family:'Consolas','Courier New',monospace;"
                    f"font-size:10px;font-weight:bold;}}"
                    f"QPushButton:hover{{background:{chip_color}22;"
                    f"border-color:{chip_color};color:white;}}"
                    f"QPushButton:pressed{{background:{chip_color}44;}}")
                chip.setToolTip(f"Click to copy: {name}")
                chip.clicked.connect(lambda _=False, n=name: self._copy_ref_name(n))
                chips_flow.addWidget(chip)

            chips_flow.addStretch()
            row_layout.addWidget(chips_wrap, 1)
            self._ref_chips_layout.addWidget(row_widget)

        self._ref_chips_layout.addStretch()

    def _copy_ref_name(self, name):
        QApplication.clipboard().setText(name)
        self._ref_copy_hint.setText(f"✓ Copied: {name}")
        QTimer.singleShot(2000, lambda: self._ref_copy_hint.setText(""))

    # ─── TAB: MACRO (Walk Recorder) ──────────────────────────────────────────

    def _build_tab_macro(self):
        t = QWidget(); tv = QVBoxLayout(t); tv.setContentsMargins(10,10,10,10); tv.setSpacing(8)
        self._tabs.addTab(t, "MACRO")
        info = QLabel("Record mouse + keyboard, save named macros.\nCall from script: play_macro('name')")
        info.setWordWrap(True)
        info.setStyleSheet(f"color:{T['fg2']};font-size:11px;")
        tv.addWidget(info)

        grp_lib = QGroupBox("MACRO LIBRARY")
        lv = QVBoxLayout(grp_lib); lv.setSpacing(5)
        self._macro_list = QListWidget()
        self._macro_list.setFixedHeight(110)
        self._macro_list.currentRowChanged.connect(self._on_macro_selected)
        lv.addWidget(self._macro_list)
        lib_btns = QHBoxLayout()
        lib_btns.addWidget(_mkbtn("+ New",   T['accent2'], self._new_macro,    sz=10, h=26))
        lib_btns.addWidget(_mkbtn("Rename",  T['dim2'],    self._rename_macro, sz=10, h=26))
        lib_btns.addWidget(_mkbtn("Delete",  T['red2'],    self._delete_macro, sz=10, h=26))
        lv.addLayout(lib_btns)
        self._lbl_macro_sel = QLabel("No macro selected")
        self._lbl_macro_sel.setStyleSheet(f"color:{T['fg2']};font-size:10px;")
        lv.addWidget(self._lbl_macro_sel)
        tv.addWidget(grp_lib)

        grp_rec = QGroupBox("RECORDER")
        rv = QVBoxLayout(grp_rec); rv.setSpacing(6)

        # ── Record-mode checkboxes ────────────────────────────────────────
        _cb_style = (
            f"QCheckBox{{color:{T['fg']};font-size:11px;spacing:6px;}}"
            f"QCheckBox::indicator{{width:14px;height:14px;border-radius:3px;"
            f"border:1px solid {T['border2']};background:{T['card']};}}"
            f"QCheckBox::indicator:checked{{background:{T['accent']};border-color:{T['accent']};}}"
        )
        mode_row = QHBoxLayout(); mode_row.setSpacing(14)
        self._cb_rec_keyboard = QCheckBox("Keyboard")
        self._cb_rec_keyboard.setChecked(True)
        self._cb_rec_keyboard.setStyleSheet(_cb_style)
        self._cb_rec_mouse = QCheckBox("Mouse")
        self._cb_rec_mouse.setChecked(True)
        self._cb_rec_mouse.setStyleSheet(_cb_style)
        mode_lbl = QLabel("Record:")
        mode_lbl.setStyleSheet(f"color:{T['fg2']};font-size:11px;")
        mode_row.addWidget(mode_lbl)
        mode_row.addWidget(self._cb_rec_keyboard)
        mode_row.addWidget(self._cb_rec_mouse)
        mode_row.addStretch()
        rv.addLayout(mode_row)

        # ── Status + buttons ──────────────────────────────────────────────
        self._rec_status = QLabel("Ready")
        self._rec_status.setStyleSheet(f"color:{T['fg2']};font-size:11px;font-weight:bold;")
        rv.addWidget(self._rec_status)
        rec_btns = QHBoxLayout()
        self._btn_rec_start = _mkbtn("Start Recording", T['red'],  self._start_record, sz=11, h=30)
        self._btn_rec_stop  = _mkbtn("Stop Recording",  T['dim2'], self._stop_record,  sz=11, h=30)
        self._btn_rec_stop.setEnabled(False)
        rec_btns.addWidget(self._btn_rec_start); rec_btns.addWidget(self._btn_rec_stop)
        rv.addLayout(rec_btns)
        self._rec_info = QLabel("No recording in memory.")
        self._rec_info.setStyleSheet(f"color:{T['fg2']};font-size:10px;")
        rv.addWidget(self._rec_info)
        sv_row = QHBoxLayout()
        sv_row.addWidget(_mkbtn("Save to Selected Macro", T['green2'], self._save_to_selected_macro, sz=10, h=26))
        sv_row.addStretch()
        rv.addLayout(sv_row)
        tv.addWidget(grp_rec)

        grp_play = QGroupBox("PLAYBACK")
        pv = QVBoxLayout(grp_play); pv.setSpacing(6)
        self._play_status = QLabel("Idle")
        self._play_status.setStyleSheet(f"color:{T['fg2']};font-size:11px;font-weight:bold;")
        pv.addWidget(self._play_status)
        loop_row = QHBoxLayout()
        self._cb_loop = QCheckBox("Loop playback")
        self._cb_loop.setChecked(True)
        loop_row.addWidget(self._cb_loop); loop_row.addStretch()
        pv.addLayout(loop_row)
        play_btns = QHBoxLayout()
        self._btn_play_start = _mkbtn("Play Selected", T['green2'], self._play_macro, sz=11, h=30)
        self._btn_play_stop  = _mkbtn("Stop",          T['dim2'],   self._stop_macro, sz=11, h=30)
        self._btn_play_stop.setEnabled(False)
        play_btns.addWidget(self._btn_play_start); play_btns.addWidget(self._btn_play_stop)
        pv.addLayout(play_btns)
        tv.addWidget(grp_play)
        tv.addStretch()

    # ─── TAB: SETTINGS ───────────────────────────────────────────────────────

    def _build_tab_settings(self):
        t = QWidget(); tv = QVBoxLayout(t); tv.setContentsMargins(10,10,10,10); tv.setSpacing(8)
        self._tabs.addTab(t, "SETTINGS")
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        inner = QWidget(); iv = QVBoxLayout(inner); iv.setSpacing(8); iv.setContentsMargins(0,0,4,0)
        scroll.setWidget(inner)
        tv.addWidget(scroll, 1)

        grp_hk = QGroupBox("HOTKEYS  (e.g. ctrl+F9 / F10 / alt+p)")
        hkv = QVBoxLayout(grp_hk); hkv.setSpacing(6)
        hk_note = QLabel("Type combo + press Enter or Set.\nRequires 'keyboard' lib + run as Admin.")
        hk_note.setWordWrap(True); hk_note.setStyleSheet(f"color:{T['fg2']};font-size:10px;")
        hkv.addWidget(hk_note)
        hk_defs = [
            ("run_active_script",  "▶ Run Selected Script"),
            ("stop_active_script", "⏹ Stop Selected Script"),
            ("stop_all_scripts",   "⏹ Stop All Scripts"),
            ("rec_start",          "Start Recording"),
            ("rec_stop",           "Stop Recording"),
            ("play_macro",         "Play Selected Macro"),
            ("stop_macro",         "Stop Macro"),
        ]
        self._hk_edits: dict = {}
        grid = QGridLayout(); grid.setSpacing(5)
        for row_i, (aid, label) in enumerate(hk_defs):
            lbl = QLabel(label)
            lbl.setStyleSheet(f"color:{T['fg']};font-size:11px;min-width:160px;")
            edit = QLineEdit(); edit.setPlaceholderText("e.g. ctrl+F9"); edit.setFixedWidth(120)
            self._hk_edits[aid] = edit
            status_lbl = QLabel(""); status_lbl.setStyleSheet(f"color:{T['dim']};font-size:10px;min-width:70px;")
            btn_set = QPushButton("Set"); btn_set.setFixedSize(44, 26)
            btn_set.setStyleSheet(f"QPushButton{{background:{T['accent2']};color:white;border:none;border-radius:4px;font-size:10px;font-weight:bold;}}QPushButton:hover{{background:{T['accent']};}}")
            btn_set.clicked.connect(lambda _=False, a=aid, e=edit, s=status_lbl: self._set_hotkey(a, e, s))
            edit.returnPressed.connect(lambda a=aid, e=edit, s=status_lbl: self._set_hotkey(a, e, s))
            btn_clr = QPushButton("X"); btn_clr.setFixedSize(26, 26)
            btn_clr.setStyleSheet(f"QPushButton{{background:{T['dim2']};color:{T['fg2']};border:none;border-radius:4px;}}QPushButton:hover{{background:{T['red2']};color:white;}}")
            btn_clr.clicked.connect(lambda _=False, a=aid, e=edit, s=status_lbl: self._clear_hotkey(a, e, s))
            grid.addWidget(lbl, row_i, 0); grid.addWidget(edit, row_i, 1)
            grid.addWidget(btn_set, row_i, 2); grid.addWidget(btn_clr, row_i, 3)
            grid.addWidget(status_lbl, row_i, 4)
        hkv.addLayout(grid); iv.addWidget(grp_hk)

        grp_io = QGroupBox("CONFIG MANAGER")
        cfv = QVBoxLayout(grp_io); cfv.setSpacing(7)

        # ── active config label ──────────────────────────────────────────────
        self._lbl_cfg = QLabel("No config loaded")
        self._lbl_cfg.setStyleSheet(f"color:{T['fg2']};font-size:10px;")
        cfv.addWidget(self._lbl_cfg)

        # ── config list ──────────────────────────────────────────────────────
        self._cfg_list = QListWidget()
        self._cfg_list.setFixedHeight(110)
        self._cfg_list.setStyleSheet(
            f"QListWidget{{background:{T['card']};border:1px solid {T['border']};"
            f"border-radius:4px;font-size:11px;color:{T['fg']};}}"
            f"QListWidget::item:selected{{background:{T['accent2']};color:white;}}"
            f"QListWidget::item:hover{{background:{T['border2']};}}")
        self._cfg_list.itemDoubleClicked.connect(self._cfg_load_selected)
        cfv.addWidget(self._cfg_list)

        # ── row 1: new / load / rename ───────────────────────────────────────
        r1 = QHBoxLayout(); r1.setSpacing(5)
        r1.addWidget(_mkbtn("+ New",    T['dim2'],    self._cfg_new,            sz=10, h=26))
        r1.addWidget(_mkbtn("Load",     T['accent2'], self._cfg_load_selected,  sz=10, h=26))
        r1.addWidget(_mkbtn("Rename",   T['dim2'],    self._cfg_rename,         sz=10, h=26))
        cfv.addLayout(r1)

        # ── row 2: save / save as / delete ───────────────────────────────────
        r2 = QHBoxLayout(); r2.setSpacing(5)
        r2.addWidget(_mkbtn("Save",     T['accent'],  self._cfg_save,          sz=10, h=26))
        r2.addWidget(_mkbtn("Save As",  T['dim2'],    self._cfg_save_as,       sz=10, h=26))
        r2.addWidget(_mkbtn("Delete",   T['dim2'],    self._cfg_delete,        sz=10, h=26))
        cfv.addLayout(r2)

        # ── row 3: import / export ────────────────────────────────────────────
        r3 = QHBoxLayout(); r3.setSpacing(5)
        r3.addWidget(_mkbtn("Export",  T['dim2'],  self._cfg_export,  sz=10, h=26))
        r3.addWidget(_mkbtn("Import",  T['dim2'],  self._cfg_import,  sz=10, h=26))
        r3.addStretch()
        cfv.addLayout(r3)

        iv.addWidget(grp_io)
        self._cfg_refresh_list()

        grp_tess = QGroupBox("TESSERACT")
        tesv = QVBoxLayout(grp_tess); tesv.setSpacing(4)
        tess_lbl = QLabel(f"Status: {'Found' if OCR_OK else 'Not found'}")
        tess_lbl.setStyleSheet(f"color:{T['green'] if OCR_OK else T['red']};font-size:11px;")
        tesv.addWidget(tess_lbl); iv.addWidget(grp_tess)

        grp_input = QGroupBox("INPUT DRIVER")
        inv = QVBoxLayout(grp_input); inv.setSpacing(4)
        _st = backend_status()
        for _line, _ok in [
            (f"Interception: {'OK' if _st['interception'] else 'Not available  — ' + (_st['interception_err'] or '')}", _st['interception']),
            (f"win32api:      {'OK' if WIN32_OK else 'Not available'}", WIN32_OK),
            (f"keyboard lib:  {'OK' if KEYBOARD_OK else 'Not installed  (pip install keyboard)'}", KEYBOARD_OK),
            (f"Mouse driver:  {_st['mouse_backend'].upper()}", True),
            (f"KB driver:     {_st['kb_backend'].upper()}", True),
        ]:
            _l = QLabel(_line)
            _l.setStyleSheet(f"color:{T['green'] if _ok else T['red']};font-size:10px;")
            inv.addWidget(_l)
        iv.addWidget(grp_input)

        grp_about = QGroupBox("SCRIPT REFERENCE")
        av = QVBoxLayout(grp_about); av.setSpacing(3)
        ref = QTextEdit(); ref.setReadOnly(True)
        ref.setStyleSheet(f"background:{T['card']};color:{T['fg2']};font-family:'Consolas';font-size:10px;border:1px solid {T['border']};")
        ref.setFixedHeight(340)
        ref.setPlainText(
            "BUTTON SCANNING\n"
            "  btn.scan()  btn.scan([o,p,i,h])  btn.ocr.scan()\n"
            "  o=OCR  p=Pixel  i=Image  h=Highlight\n\n"
            "RESULTS\n"
            "  btn.ocr / btn.pixel / btn.image / btn.hl  (bool)\n"
            "  bool(btn)  final result\n\n"
            "ACTIONS\n"
            "  btn.click()               instant click\n"
            "  btn.click('h')            human-like move + click\n"
            "  btn.click(hold=0.2)       hold 0.2 s\n"
            "  btn.click('h', repeat=3)  human move, 3 clicks\n"
            "  btn.ocr_int()  btn.ocr_text()\n\n"
            "MACROS\n"
            "  play_macro('name')              play once (blocks)\n"
            "  play_macro('name', loop=True)   loop until stopped\n\n"
            "KEYBOARD\n"
            "  kb_press(key)                  tap (down+up)\n"
            "  kb_down(key)                   key down\n"
            "  kb_up(key)                     key up\n"
            "  kb_hold(key, seconds)           hold for N seconds\n"
            "  kb_combo(combo)                e.g. 'ctrl+c'\n"
            "  kb_toggleLoop(key, True/False, interval=0.5)\n"
            "  kb_toggleHold(key, True/False)\n\n"
            "  Key names: 'a'..'z'  '0'..'9'  'f1'..'f24'\n"
            "    'space' 'enter' 'tab' 'escape' 'backspace'\n"
            "    'shift' 'ctrl' 'alt' 'windows'\n"
            "    'left' 'right' 'up' 'down'\n"
            "    'page up' 'page down' 'home' 'end' 'insert' 'delete'\n"
            "    'num 0'..'num 9'  'num +'  'num -'  etc.\n\n"
            "MOUSE\n"
            "  mouse_move(x, y)                     instant teleport\n"
            "  mouse_smooth(x, y, dur=0.5)          cosine-eased move\n"
            "  mouse_human(x, y, dur=0.25)          raw-input human move (game-safe)\n"
            "  mouse_click(x, y, humanize='h')      move + click\n"
            "  mouse_down(btn)  mouse_up(btn)  mouse_pos()\n\n"
            "CONTROLS  mode / timer_limit / slider values (live)\n\n"
            "FUNCTIONS  sleep(seconds)   log(message)\n"
        )
        av.addWidget(ref); iv.addWidget(grp_about)
        iv.addStretch()

    # ─────────────────────────────────────────────────────────────────────────
    # Button tab logic
    # ─────────────────────────────────────────────────────────────────────────

    def _refresh_btn_list(self):
        self._btn_list.clear()
        for bd in self._btn_defs:
            icon = "🖱" if bd["btn_type"] == "button" else "📦"
            reg = bd.get("region")
            reg_str = f"  [{reg[0]},{reg[1]} {reg[2]}×{reg[3]}]" if reg else "  [no region]"
            methods = []
            if bd.get("ocr_enabled"):   methods.append("OCR")
            if bd.get("pixel_enabled"): methods.append("PIX")
            if bd.get("color_enabled"): methods.append("COL")
            if bd.get("image_enabled"): methods.append("IMG")
            mstr = "+".join(methods) if methods else "—"
            item = QListWidgetItem(f'{icon} {bd["name"]}   {mstr}{reg_str}')
            item.setForeground(QColor(T['accent'] if bd["btn_type"] == "button" else T['yellow']))
            self._btn_list.addItem(item)

    def _add_button(self):
        names = [b["name"] for b in self._btn_defs]
        dlg = ButtonDefDialog(all_names=names, parent=self)
        def _on_saved(data):
            self._btn_defs.append(data)
            self._refresh_btn_list()
            self._refresh_ctrl_runtime()
            self._save_config()
            self._log_msg(f"✅ Added: {data['name']}")
        dlg.saved.connect(_on_saved)
        _center_on_screen(dlg)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        # Keep a reference so it isn't GC'd
        self._open_dlg = dlg

    def _edit_button(self):
        row = self._btn_list.currentRow()
        if row < 0: self._log_msg("⚠ Select a button to edit."); return
        names = [b["name"] for b in self._btn_defs if b is not self._btn_defs[row]]
        dlg = ButtonDefDialog(existing=self._btn_defs[row], all_names=names, parent=self)
        def _on_saved(data, r=row):
            if r < len(self._btn_defs):
                self._btn_defs[r] = data
            self._refresh_btn_list()
            self._save_config()
        dlg.saved.connect(_on_saved)
        _center_on_screen(dlg)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        self._open_dlg = dlg

    def _remove_button(self):
        row = self._btn_list.currentRow()
        if row < 0: return
        name = self._btn_defs[row]["name"]
        if QMessageBox.question(self, "Remove", f"Remove '{name}'?",
                                QMessageBox.Yes|QMessageBox.No) == QMessageBox.Yes:
            self._btn_defs.pop(row)
            self._refresh_btn_list()
            self._save_config()

    def _set_game_region(self):
        RegionSelector.open(self._on_game_region)

    def _on_game_region(self, x, y, w, h):
        self._game_region = [x, y, w, h]
        self._lbl_game_reg.setText(f"📍 {x},{y}  {w}×{h}  (resize game to this)")
        self._lbl_game_reg.setStyleSheet(f"color:{T['green']};font-size:10px;")
        self._save_config()

    def _toggle_game_region(self):
        if self._game_overlay and self._game_overlay.isVisible():
            self._game_overlay.close()
            if hasattr(self._game_overlay, "_edit_bar") and self._game_overlay._edit_bar:
                try: self._game_overlay._edit_bar.close()
                except Exception: pass
            self._game_overlay = None
            self._btn_toggle_game.setText("👁 Show")
            self._gw_resize_widget.setVisible(False)
            return
        reg = self._game_region
        if not reg:
            self._log_msg("⚠ No game region set yet.")
            return
        x, y, w, h = reg
        orig_reg = list(reg)

        # Always start read-only (click-through) regardless of lock state
        overlay = RegionHighlighter(x, y, w, h, label="GAME", color=QColor(T['yellow']),
                                    editable=False)

        # Create the edit bar — always visible, starts with lock active
        bar = RegionEditBar(x, y, w, h, label="GAME")
        bar._overlay = overlay          # so the lock button can call set_editable()
        overlay._edit_bar = bar

        def _on_done():
            if bar.cancelled:
                overlay.update_geometry(*orig_reg)
                self._log_msg("Game region edit cancelled — unchanged.")
            else:
                rx, ry, rw, rh = overlay.region
                self._game_region = [rx, ry, rw, rh]
                self._lbl_game_reg.setText(f"📍 {rx},{ry}  {rw}×{rh}  (resize game to this)")
                self._lbl_game_reg.setStyleSheet(f"color:{T['green']};font-size:10px;")
                self._save_config()
                self._log_msg(f"📐 Game region updated: {rx},{ry} {rw}×{rh}")
            overlay.close()
            bar.close()
            self._game_overlay = None
            self._btn_toggle_game.setText("👁 Show")
            self._gw_resize_widget.setVisible(False)

        overlay.region_changed.connect(bar.update_values)
        bar.region_sig.connect(lambda nx, ny, nw, nh: overlay.update_geometry(nx, ny, nw, nh))
        bar.done_sig.connect(_on_done)

        self._game_overlay = overlay
        overlay.show()
        bar.show(); bar.raise_()
        self._btn_toggle_game.setText("🙈 Hide")
        self._sp_gx.setValue(x); self._sp_gy.setValue(y)
        self._sp_gw.setValue(w); self._sp_gh.setValue(h)
        self._gw_resize_widget.setVisible(True)

    def _apply_game_resize(self):
        x = self._sp_gx.value(); y = self._sp_gy.value()
        w = self._sp_gw.value(); h = self._sp_gh.value()
        self._game_region = [x, y, w, h]
        self._lbl_game_reg.setText(f"📍 {x},{y}  {w}×{h}  (resize game to this)")
        self._lbl_game_reg.setStyleSheet(f"color:{T['green']};font-size:10px;")
        if self._game_overlay and self._game_overlay.isVisible():
            self._game_overlay.update_geometry(x, y, w, h)
        self._save_config()
        self._log_msg(f"📐 Game region updated: {x},{y} {w}×{h}")

    def _show_selected_region(self):
        """Show an overlay for the currently selected button/region.

        Starts read-only (click-through).  Unlock via the topbar 🔓 button
        to drag/resize.  The floating RegionEditBar appears only when unlocked.
        """
        idx = self._btn_list.currentRow()
        if idx < 0 or idx >= len(self._btn_defs):
            self._log_msg("⚠ Select a button/region first.")
            return
        bd  = self._btn_defs[idx]
        reg = bd.get("region")
        if not reg:
            self._log_msg(f"⚠ '{bd['name']}' has no region defined yet.")
            return

        x, y, w, h = reg
        orig_reg   = list(reg)

        # Close any existing single-region overlay
        existing = getattr(self, "_active_edit_overlay", None)
        if existing:
            try:
                if hasattr(existing, "_edit_bar") and existing._edit_bar:
                    existing._edit_bar.close()
                existing.close()
            except Exception: pass
        self._active_edit_overlay = None
        self._active_edit_bar     = None

        # Start read-only
        overlay = RegionHighlighter(x, y, w, h, label=bd["name"],
                                    color=QColor(0, 212, 255), editable=False)
        bar = RegionEditBar(x, y, w, h, label=bd["name"])
        bar._overlay = overlay          # so the lock button can call set_editable()
        overlay._edit_bar = bar

        self._active_edit_overlay = overlay
        self._active_edit_bar     = bar

        def _on_done():
            if bar.cancelled:
                overlay.update_geometry(*orig_reg)
                self._log_msg(f"Region edit cancelled — '{bd['name']}' unchanged.")
            else:
                rx, ry, rw, rh = overlay.region
                bd["region"] = [rx, ry, rw, rh]
                self._save_config()
                self._refresh_btn_list()
                self._log_msg(f"✓ Region saved: '{bd['name']}'  {rx},{ry}  {rw}×{rh}")
            overlay.close()
            bar.close()
            self._active_edit_overlay = None
            self._active_edit_bar     = None

        overlay.region_changed.connect(bar.update_values)
        bar.region_sig.connect(lambda nx, ny, nw, nh: overlay.update_geometry(nx, ny, nw, nh))
        bar.done_sig.connect(_on_done)

        overlay.show()
        bar.show(); bar.raise_()

    def _show_all_regions(self):
        self._hide_all_regions()
        colors = [QColor(0,212,255), QColor(0,230,118), QColor(255,61,90),
                  QColor(255,215,64), QColor(255,0,200), QColor(255,152,0)]
        for i, bd in enumerate(self._btn_defs):
            reg = bd.get("region")
            if reg:
                c = colors[i % len(colors)]
                w = PersistentHighlight(*reg, label=bd["name"], color=c)
                w.show()
                self._active_highlighters.append(w)

    def _show_multi_regions(self):
        """Checkbox dialog — pick any combination of regions to show simultaneously."""
        if not self._btn_defs:
            self._log_msg("⚠ No buttons/regions defined.")
            return

        colors = [QColor(0,212,255), QColor(0,230,118), QColor(255,61,90),
                  QColor(255,215,64), QColor(255,0,200), QColor(255,152,0)]

        dlg = QDialog(self)
        dlg.setWindowTitle("Multi-Region Show")
        dlg.setStyleSheet(f"background:{T['bg']};color:{T['fg']};")
        dlg.setMinimumWidth(300)
        lay = QVBoxLayout(dlg); lay.setSpacing(6); lay.setContentsMargins(14, 12, 14, 12)

        hdr = QLabel("Select regions to show:")
        hdr.setStyleSheet(f"color:{T['accent']};font-size:11px;font-weight:bold;")
        lay.addWidget(hdr)

        # Which names are currently highlighted
        active_names = {getattr(h, '_label', '') for h in self._active_highlighters}

        checks = []
        for i, bd in enumerate(self._btn_defs):
            c = colors[i % len(colors)]
            reg = bd.get("region")
            cb = QCheckBox(bd["name"] + ("" if reg else "  (no region)"))
            cb.setEnabled(bool(reg))
            cb.setChecked(bd["name"] in active_names and bool(reg))
            cb.setStyleSheet(
                f"QCheckBox{{color:{c.name()};font-size:11px;spacing:6px;"
                f"padding:2px 0;}}"
                f"QCheckBox::indicator{{width:14px;height:14px;border-radius:3px;"
                f"border:1px solid {T['border2']};background:{T['panel']};}}"
                f"QCheckBox::indicator:checked{{background:{c.name()};"
                f"border-color:{c.name()};}}"
                f"QCheckBox:disabled{{color:{T['dim2']};}}")
            lay.addWidget(cb)
            checks.append((cb, bd, c))

        lay.addSpacing(4)
        sel_row = QHBoxLayout(); sel_row.setSpacing(6)
        btn_all  = QPushButton("All")
        btn_none = QPushButton("None")
        for b in (btn_all, btn_none):
            b.setFixedHeight(22)
            b.setStyleSheet(
                f"QPushButton{{background:{T['dim2']};color:{T['fg2']};border:none;"
                f"border-radius:3px;font-size:10px;padding:0 12px;font-weight:bold;}}"
                f"QPushButton:hover{{background:{T['border2']};color:{T['fg']};}}")
        btn_all.clicked.connect(
            lambda: [c[0].setChecked(True)  for c in checks if c[0].isEnabled()])
        btn_none.clicked.connect(
            lambda: [c[0].setChecked(False) for c in checks])
        sel_row.addWidget(btn_all); sel_row.addWidget(btn_none); sel_row.addStretch()
        lay.addLayout(sel_row)

        lay.addSpacing(4)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setStyleSheet(
            f"QPushButton{{background:{T['accent2']};color:white;border:none;"
            f"border-radius:4px;padding:4px 16px;font-weight:bold;}}"
            f"QPushButton:hover{{background:{T['accent']};}}")
        btns.button(QDialogButtonBox.Cancel).setStyleSheet(
            f"QPushButton{{background:{T['dim2']};color:{T['fg2']};border:none;"
            f"border-radius:4px;padding:4px 16px;}}"
            f"QPushButton:hover{{background:{T['border2']};color:{T['fg']};}}")
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)

        if dlg.exec() != QDialog.Accepted:
            return

        # Close all current persistent highlights, re-show only selected
        for w in self._active_highlighters:
            try: w.close()
            except Exception: pass
        self._active_highlighters.clear()

        shown = 0
        for cb, bd, c in checks:
            if cb.isChecked():
                reg = bd.get("region")
                if reg:
                    w = PersistentHighlight(*reg, label=bd["name"], color=c)
                    w.show()
                    self._active_highlighters.append(w)
                    shown += 1
        self._log_msg(f"👁 Showing {shown} region(s)")

    def _hide_all_regions(self):
        for w in self._active_highlighters:
            try:
                if hasattr(w, "_edit_bar") and w._edit_bar:
                    w._edit_bar.close()
                w.close()
            except Exception: pass
        self._active_highlighters.clear()
        # also close any single-region edit overlay
        for attr in ("_active_edit_overlay", "_active_edit_bar"):
            w = getattr(self, attr, None)
            if w:
                try: w.close()
                except Exception: pass
                setattr(self, attr, None)

    # ── Preview helpers ───────────────────────────────────────────────────────

    def _update_preview(self, raw_img, pre_img=None):
        """Update the inline preview labels with raw + preprocessed images.

        Also feeds the floating PopupPreview window if it is currently open.
        Stores the last images so the popup can show them when first opened.
        """
        self._last_raw_img = raw_img
        self._last_pre_img = pre_img

        # -- Inline preview (self._preview / self._preview_pre) ---------------
        def _set_lbl(lbl, pil_img, max_h=167):
            if not pil_img:
                lbl.clear()
                return
            try:
                img = pil_img.convert("RGB")
                img.thumbnail((lbl.width() or 320, max_h), Image.LANCZOS)
                data = img.tobytes("raw", "RGB")
                qi = QImage(data, img.width, img.height,
                            img.width * 3, QImage.Format_RGB888).copy()
                lbl.setPixmap(QPixmap.fromImage(qi).scaled(
                    lbl.width() or img.width,
                    lbl.height() or img.height,
                    Qt.KeepAspectRatio, Qt.SmoothTransformation))
                lbl.setText("")
            except Exception:
                pass

        _set_lbl(self._preview, raw_img, max_h=180)

        if pre_img is not None:
            p = pre_img[0] if isinstance(pre_img, list) else pre_img
            _set_lbl(self._preview_pre, p.convert("RGB") if p else None)
            self._preview_pre.show()
            self._preview_pre_hdr.show()
        else:
            self._preview_pre.clear()
            self._preview_pre.hide()
            self._preview_pre_hdr.hide()

        # -- Floating popup (if open) -----------------------------------------
        if self._popup_preview and self._popup_preview.isVisible():
            self._popup_preview.update_images(raw_img, pre_img)

    def _toggle_popup_preview(self):
        """Show or hide the floating PopupPreview window."""
        # Lazily create on first use
        if self._popup_preview is None:
            self._popup_preview = PopupPreview()

        if self._popup_preview.isVisible():
            self._popup_preview.hide()
            self._btn_popup_prev.setText("⧉ Pop Out")
        else:
            # Feed the last captured images if available
            raw = getattr(self, "_last_raw_img", None)
            pre = getattr(self, "_last_pre_img", None)
            if raw is not None:
                self._popup_preview.update_images(raw, pre)
            self._popup_preview.show()
            self._popup_preview.raise_()
            self._btn_popup_prev.setText("✕ Close Pop-Out")

    # ── Button-list actions ───────────────────────────────────────────────────

    def _test_button(self):
        row = self._btn_list.currentRow()
        if row < 0: self._log_msg("⚠ Select a button to test."); return
        bd = self._btn_defs[row]
        reg = bd.get("region")
        if not reg: self._log_msg("⚠ No region set for this button."); return
        img = capture(reg)
        if not img: self._log_msg("⚠ Capture failed."); return
        o, p, i, final = detect_button(img, bd)
        name = bd["name"]
        self._log_msg(f"🔬 Test [{name}]:")
        if bd.get("ocr_enabled"):
            if OCR_OK:
                try:
                    psm  = bd.get("ocr_psm", 6)
                    want = bd.get("ocr_text", "").strip() or "(none set)"
                    self._log_msg(f"   OCR want: \"{want}\"")
                    passes = bd.get("ocr_passes") or OCR_DEFAULT_PASSES
                    for idx, p in enumerate(passes):
                        ch  = p.get("channel",   "selected-invert")
                        thr = p.get("threshold",  155)
                        try:
                            variant = _apply_ocr_channel(img, ch, thr, extra=p)
                            raw = pytesseract.image_to_string(
                                variant, config=f"--psm {psm}"
                            ).strip().replace("\n", " ")
                            if ch == "selected-invert":
                                sc  = p.get("sel_color",  [255,255,255])
                                rad = p.get("sel_radius", 60)
                                self._log_msg(f"   Pass {idx+1} [{ch} rgb{tuple(sc)} rad={rad}]: \"{raw}\"")
                            else:
                                self._log_msg(f"   Pass {idx+1} [{ch} thr={thr}]: \"{raw}\"")
                        except Exception as ex:
                            self._log_msg(f"   Pass {idx+1} error: {ex}")
                except Exception as ex:
                    self._log_msg(f"   OCR setup error: {ex}")
            self._log_msg(f"   OCR: {'✅' if o else '❌'}")
        if bd.get("pixel_enabled"): self._log_msg(f"   Pixel: {'✅' if p else '❌'}")
        if bd.get("image_enabled"): self._log_msg(f"   Image: {'✅' if i else '❌'}")
        self._log_msg(f"   RESULT: {'✅ FOUND' if final else '❌ NOT FOUND'}")

        # ── Highlight detection (border edge test) ────────────────────────────
        # Always runs — independent of OCR/pixel/image checkboxes.
        # Per-edge ratios are logged so you can tune the threshold if needed.
        try:
            hl_result, hl_top, hl_bot, hl_left, hl_right, hl_edges, hl_min = \
                _detect_highlight(img, bd)
            self._log_msg(
                f"   Highlight (hl):  "
                f"top={hl_top:.2f}  bot={hl_bot:.2f}  "
                f"left={hl_left:.2f}  right={hl_right:.2f}  "
                f"edges={hl_edges}/{hl_min}  "
                f"→ {'✅ HIGHLIGHTED' if hl_result else '❌ not highlighted'}"
            )
        except Exception as _hl_ex:
            self._log_msg(f"   Highlight (hl): error — {_hl_ex}")

        # Show original + first preprocessed pass side by side
        pre_img = None
        if OCR_OK:
            try:
                passes = bd.get("ocr_passes") or OCR_DEFAULT_PASSES
                if passes:
                    p = passes[0]
                    pre_img = _apply_ocr_channel(img,
                                                  p.get("channel",  "selected-invert"),
                                                  p.get("threshold", 0), extra=p)
                    ch_lbl  = p.get("channel", "selected-invert")
                    thr_lbl = p.get("threshold", 155)
                    if ch_lbl == "selected-invert":
                        sc  = p.get("sel_color",  [255,255,255])
                        rad = p.get("sel_radius", 60)
                        self._preview_pre_hdr.setText(
                            f"hl·pass 1 · {ch_lbl}  rgb{tuple(sc)} rad={rad}")
                    elif ch_lbl == "white-extract":
                        self._preview_pre_hdr.setText(
                            f"hl·pass 1 · {ch_lbl} thr={thr_lbl}")
                    else:
                        self._preview_pre_hdr.setText(
                            f"hl·pass 1 · {ch_lbl} thr={thr_lbl}")
            except Exception: pass
        self._update_preview(img, pre_img)

    def _test_click(self):
        row = self._btn_list.currentRow()
        if row < 0:
            self._log_msg("⚠ Select a button to test click."); return
        bd  = self._btn_defs[row]
        reg = bd.get("region")
        if not reg:
            self._log_msg("⚠ No region set — can't determine click position."); return
        x, y, w, h = reg
        cx = x + w // 2
        cy = y + h // 2
        name = bd["name"]
        self._log_msg(f"🖱 Test Click [{name}]  →  center ({cx}, {cy})  region ({x},{y} {w}×{h})")
        try:
            click_at_pos(cx, cy)
            self._log_msg(f"   ✅ Click sent to ({cx}, {cy})")
        except Exception as e:
            self._log_msg(f"   ❌ Click failed: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # Controls tab logic
    # ─────────────────────────────────────────────────────────────────────────

    def _refresh_ctrl_list(self):
        self._ctrl_list.clear()
        for cd in self._ctrl_defs:
            ctype = cd["ctrl_type"]
            if ctype == "selector":
                opts = ", ".join(cd.get("options", []))
                txt = f"🎯 {cd['name']}  [{opts}]  sel={cd.get('selected','')}"
            else:
                txt = f"🎚 {cd['name']}  [{cd.get('min',0)}–{cd.get('max',100)}]  val={cd.get('value',0)}"
            item = QListWidgetItem(txt)
            item.setForeground(QColor(T['accent'] if ctype == "selector" else T['yellow']))
            self._ctrl_list.addItem(item)
        self._refresh_ctrl_runtime()

    def _refresh_ctrl_runtime(self):
        # Clear old widgets
        while self._ctrl_runtime_layout.count():
            item = self._ctrl_runtime_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()

        for cd in self._ctrl_defs:
            name = cd["name"]
            ctype = cd["ctrl_type"]
            row = QHBoxLayout()
            lbl = QLabel(f"{name}:"); lbl.setFixedWidth(120)
            lbl.setStyleSheet(f"color:{T['fg2']};font-size:11px;")
            row.addWidget(lbl)

            if ctype == "selector":
                combo = QComboBox()
                for opt in cd.get("options", []):
                    combo.addItem(opt)
                sel = cd.get("selected", "")
                if sel in cd.get("options", []):
                    combo.setCurrentText(sel)
                combo.currentTextChanged.connect(lambda v, n=name: self._on_ctrl_selector(n, v))
                row.addWidget(combo)
            else:
                sld = QSlider(Qt.Horizontal)
                sld.setRange(cd.get("min", 0), cd.get("max", 100))
                sld.setSingleStep(cd.get("step", 1))
                sld.setValue(cd.get("value", 0))
                val_lbl = QLabel(str(cd.get("value", 0)))
                val_lbl.setFixedWidth(50)
                val_lbl.setStyleSheet(f"color:{T['fg']};font-size:11px;")
                sld.valueChanged.connect(lambda v, n=name, l=val_lbl: self._on_ctrl_slider(n, v, l))
                row.addWidget(sld); row.addWidget(val_lbl)

            row.addStretch()
            container = QWidget()
            container.setLayout(row)
            self._ctrl_runtime_layout.addWidget(container)

        self._ctrl_runtime_layout.addStretch()

    def _on_ctrl_selector(self, name, value):
        for cd in self._ctrl_defs:
            if cd["name"] == name:
                cd["selected"] = value
                self._save_config()
                break

    def _on_ctrl_slider(self, name, value, lbl):
        lbl.setText(str(value))
        for cd in self._ctrl_defs:
            if cd["name"] == name:
                cd["value"] = value
                self._save_config()
                break

    def _add_control(self):
        names = [c["name"] for c in self._ctrl_defs]
        dlg = ControlDefDialog(all_names=names, parent=self)
        _center_on_screen(dlg)
        if dlg.exec() == QDialog.Accepted:
            self._ctrl_defs.append(dlg.get_data())
            self._refresh_ctrl_list()
            self._save_config()

    def _edit_control(self):
        row = self._ctrl_list.currentRow()
        if row < 0: return
        names = [c["name"] for c in self._ctrl_defs if c is not self._ctrl_defs[row]]
        dlg = ControlDefDialog(existing=self._ctrl_defs[row], all_names=names, parent=self)
        _center_on_screen(dlg)
        if dlg.exec() == QDialog.Accepted:
            self._ctrl_defs[row] = dlg.get_data()
            self._refresh_ctrl_list()
            self._save_config()

    def _remove_control(self):
        row = self._ctrl_list.currentRow()
        if row < 0: return
        self._ctrl_defs.pop(row)
        self._refresh_ctrl_list()
        self._save_config()

    # ─────────────────────────────────────────────────────────────────────────
    # Script IDE logic
    # ─────────────────────────────────────────────────────────────────────────

    # ── helpers ──────────────────────────────────────────────────────────────

    def _script_engine_for(self, idx):
        return self._script_engines.get(idx)

    def _is_running(self, idx):
        eng = self._script_engine_for(idx)
        return eng is not None and eng.isRunning()

    def _make_engine(self, idx, code):
        idx = int(idx)
        all_btns  = {b["name"]: b for b in self._btn_defs}
        ctrl_dict = {c["name"]: c for c in self._ctrl_defs}
        cfg       = {"game_region": self._game_region}
        name      = self._scripts[idx]["name"]

        # ── Scope buttons to this script ──────────────────────────────────
        # Only pass buttons whose names are actually referenced in the script
        # code.  This prevents scripts from accidentally scanning/clicking
        # buttons that belong to a different script when buttons.scan() is
        # called.  If the script references "buttons" generically (bulk scan),
        # all buttons are still included so existing scripts don't break.
        if "buttons.scan" in code or "buttons.ocr" in code or "buttons.pixel" in code:
            # Bulk scan used — pass everything (existing behaviour)
            btn_dict = all_btns
        else:
            # Selective: only buttons whose name token appears in the code
            import re as _re
            btn_dict = {n: b for n, b in all_btns.items()
                        if _re.search(rf'\b{_re.escape(n)}\b', code)}
            # Always include buttons that have no name match so nothing silently
            # vanishes — fall back to all if a selective set is empty
            if not btn_dict:
                btn_dict = all_btns

        eng = ScriptEngine(code, btn_dict, ctrl_dict, cfg,
                           script_name=name, macro_getter=self.get_macro_events)
        prefix = f"[{name}] "
        eng.log_sig.connect(lambda msg, p=prefix: self._log_msg(p + msg))
        eng.status_sig.connect(lambda s, i=idx: self._on_engine_status(i, s))
        eng.finished.connect(lambda i=idx: self._on_engine_finished(i))
        return eng

    def _on_engine_status(self, idx, status):
        self._refresh_script_list_item(idx)
        if idx == self._active_script_idx:
            self._update_active_run_buttons()
        # Update the global status if anything is running
        any_running = any(self._is_running(i) for i in range(len(self._scripts)))
        if any_running:
            self._rec_status.setText("Running")
        elif "Stop" in status or "stop" in status:
            self._rec_status.setText("Stopped")

    def _on_engine_finished(self, idx):
        self._script_engines.pop(idx, None)
        self._refresh_script_list_item(idx)
        if idx == self._active_script_idx:
            self._update_active_run_buttons()
        any_running = any(self._is_running(i) for i in range(len(self._scripts)))
        if not any_running:
            self._rec_status.setText("Idle")

    # ── script list ──────────────────────────────────────────────────────────

    def _refresh_script_list(self):
        """Rebuild the list widget from self._scripts."""
        prev_row = self._script_list.currentRow()
        self._script_list.blockSignals(True)
        self._script_list.clear()
        for i, s in enumerate(self._scripts):
            self._script_list.addItem(self._script_list_text(i))
        self._script_list.blockSignals(False)
        # restore selection
        row = min(prev_row, len(self._scripts)-1)
        if row >= 0:
            self._script_list.setCurrentRow(row)
        elif self._scripts:
            self._script_list.setCurrentRow(0)

    def _script_list_text(self, idx):
        name = self._scripts[idx]["name"]
        if self._is_running(idx):
            return f"▶ {name}"
        return f"  {name}"

    def _refresh_script_list_item(self, idx):
        item = self._script_list.item(idx)
        if item:
            item.setText(self._script_list_text(idx))
            color = T['green'] if self._is_running(idx) else T['fg2']
            item.setForeground(QColor(color))

    # ── selection ────────────────────────────────────────────────────────────

    def _on_script_selected(self, row):
        # Save current editor content before switching
        if 0 <= self._active_script_idx < len(self._scripts):
            self._scripts[self._active_script_idx]["code"] = self._script_edit.toPlainText()
        self._active_script_idx = row
        if row < 0 or row >= len(self._scripts):
            self._script_edit.setPlainText("")
            self._script_name_edit.setText("")
            self._script_status_bar.setText("")
            return
        s = self._scripts[row]
        self._script_edit.blockSignals(True)
        self._script_edit.setPlainText(s.get("code", ""))
        self._script_edit.blockSignals(False)
        self._script_name_edit.setText(s["name"])
        self._update_active_run_buttons()
        self._update_status_bar(row)

    def _update_active_run_buttons(self):
        idx = self._active_script_idx
        running = self._is_running(idx)
        self._btn_run_active.setEnabled(not running and idx >= 0)
        self._btn_stop_active.setEnabled(running)

    def _update_status_bar(self, idx):
        if idx < 0 or idx >= len(self._scripts):
            self._script_status_bar.setText("")
            return
        if self._is_running(idx):
            self._script_status_bar.setText(
                f"● RUNNING  ·  {self._scripts[idx]['name']}")
            self._script_status_bar.setStyleSheet(
                f"background:{T['card']};color:{T['green']};font-size:10px;"
                f"font-weight:bold;padding:0 8px;border-top:1px solid {T['border']};")
        else:
            lines = len(self._scripts[idx].get("code","").splitlines())
            self._script_status_bar.setText(
                f"  {self._scripts[idx]['name']}  ·  {lines} lines")
            self._script_status_bar.setStyleSheet(
                f"background:{T['card']};color:{T['dim']};font-size:10px;"
                f"padding:0 8px;border-top:1px solid {T['border']};")

    # ── editor changes ────────────────────────────────────────────────────────

    def _on_script_text_changed(self):
        idx = self._active_script_idx
        if 0 <= idx < len(self._scripts):
            self._scripts[idx]["code"] = self._script_edit.toPlainText()
            self._update_status_bar(idx)

    def _on_script_name_edited(self, text):
        idx = self._active_script_idx
        if 0 <= idx < len(self._scripts):
            self._scripts[idx]["name"] = text or f"script_{idx+1}"
            self._refresh_script_list_item(idx)
            self._save_config()

    # ── CRUD ─────────────────────────────────────────────────────────────────

    def _new_script(self):
        n = len(self._scripts) + 1
        self._scripts.append({"name": f"script_{n}", "code":
            "# New script\n"
            "while True:\n"
            "    buttons.scan()\n"
            "    sleep(0.5)\n"
        })
        self._refresh_script_list()
        self._script_list.setCurrentRow(len(self._scripts) - 1)
        self._save_config()

    def _delete_script(self):
        idx = self._active_script_idx
        if idx < 0 or idx >= len(self._scripts): return
        name = self._scripts[idx]["name"]
        if QMessageBox.question(self, "Delete Script",
                f"Delete '{name}'?",
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        # Stop if running
        if self._is_running(idx):
            self._script_engines[idx].stop()
        # Remove engine refs with idx >= idx shifted
        new_engines = {}
        for k, v in self._script_engines.items():
            if k < idx: new_engines[k] = v
            elif k > idx: new_engines[k-1] = v
        self._script_engines = new_engines
        self._scripts.pop(idx)
        self._active_script_idx = -1
        self._refresh_script_list()
        self._save_config()

    # ── run / stop individual ────────────────────────────────────────────────

    def _run_active_script(self):
        idx = self._active_script_idx
        if idx < 0 or idx >= len(self._scripts): return
        # Save editor content first
        self._scripts[idx]["code"] = self._script_edit.toPlainText()
        code = self._scripts[idx]["code"].strip()
        if not code: return
        # If there's already a running engine for THIS slot, do nothing
        if self._is_running(idx): return
        # Clean up any dead engine object that wasn't removed yet
        self._script_engines.pop(idx, None)
        eng = self._make_engine(idx, code)
        self._script_engines[idx] = eng
        eng.start()
        self._refresh_script_list_item(idx)
        self._update_active_run_buttons()
        self._update_status_bar(idx)
        self._log_msg(f"▶ Started: {self._scripts[idx]['name']}")

    def _stop_active_script(self):
        idx = self._active_script_idx
        if self._is_running(idx):
            self._script_engines[idx].stop()
            self._log_msg(f"⏹ Stopping: {self._scripts[idx]['name']}")

    # ── run / stop all ───────────────────────────────────────────────────────

    def _run_all_scripts(self):
        running_count = sum(1 for i in range(len(self._scripts)) if self._is_running(i))
        pending = [s for i, s in enumerate(self._scripts)
                   if not self._is_running(i) and s.get("code", "").strip()]
        if not pending: return
        # Confirm before starting multiple scripts at once
        names = "\n".join(f"  • {s['name']}" for s in pending)
        reply = QMessageBox.question(
            self, "Run All Scripts",
            f"Start {len(pending)} script(s)?\n{names}",
            QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes: return
        # Save current editor first
        if self._active_script_idx >= 0 and self._active_script_idx < len(self._scripts):
            self._scripts[self._active_script_idx]["code"] = self._script_edit.toPlainText()
        for idx, s in enumerate(self._scripts):
            if self._is_running(idx): continue
            code = s.get("code", "").strip()
            if not code: continue
            self._script_engines.pop(idx, None)     # clean up any dead engine
            eng = self._make_engine(idx, code)
            self._script_engines[idx] = eng
            eng.start()
            self._refresh_script_list_item(idx)
            self._log_msg(f"▶ Started: {s['name']}")
        self._update_active_run_buttons()

    def _stop_all_scripts(self):
        for idx, eng in list(self._script_engines.items()):
            if eng and eng.isRunning():
                eng.stop()
                self._log_msg(f"⏹ Stopping: {self._scripts[idx]['name']}")
        self._update_active_run_buttons()

    # ─────────────────────────────────────────────────────────────────────────
    # Macro library helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _refresh_macro_list(self):
        self._macro_list.blockSignals(True)
        prev = self._macro_list.currentRow()
        self._macro_list.clear()
        for m in self._macros:
            n   = len(m.get("events", []))
            dur = m["events"][-1]["t"] if m.get("events") else 0
            self._macro_list.addItem(f'  {m["name"]}   ({n} events, {dur:.1f}s)')
        self._macro_list.blockSignals(False)
        row = min(prev, len(self._macros) - 1)
        if row >= 0:
            self._macro_list.setCurrentRow(row)

    def _on_macro_selected(self, row):
        self._active_macro_idx = row
        if 0 <= row < len(self._macros):
            m   = self._macros[row]
            n   = len(m.get("events", []))
            dur = m["events"][-1]["t"] if m.get("events") else 0
            self._lbl_macro_sel.setText(f'Selected: {m["name"]}  ({n} events, {dur:.1f}s)')
            self._lbl_macro_sel.setStyleSheet(f"color:{T['accent']};font-size:10px;")
        else:
            self._lbl_macro_sel.setText("No macro selected")
            self._lbl_macro_sel.setStyleSheet(f"color:{T['fg2']};font-size:10px;")

    def _new_macro(self):
        name, ok = QInputDialog.getText(self, "New Macro", "Macro name:")
        if not ok or not name.strip(): return
        name = name.strip()
        if any(m["name"] == name for m in self._macros):
            QMessageBox.warning(self, "Duplicate", f"Macro '{name}' already exists."); return
        self._macros.append({"name": name, "events": []})
        self._refresh_macro_list()
        self._macro_list.setCurrentRow(len(self._macros) - 1)
        self._save_config()
        self._log_msg(f"+ New macro: {name}")

    def _rename_macro(self):
        row = self._active_macro_idx
        if row < 0 or row >= len(self._macros): return
        old = self._macros[row]["name"]
        name, ok = QInputDialog.getText(self, "Rename Macro", "New name:", text=old)
        if not ok or not name.strip(): return
        name = name.strip()
        if name != old and any(m["name"] == name for m in self._macros):
            QMessageBox.warning(self, "Duplicate", f"'{name}' already exists."); return
        self._macros[row]["name"] = name
        self._refresh_macro_list()
        self._save_config()

    def _delete_macro(self):
        row = self._active_macro_idx
        if row < 0 or row >= len(self._macros): return
        name = self._macros[row]["name"]
        if QMessageBox.question(self, "Delete Macro", f"Delete '{name}'?",
                                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        self._macros.pop(row)
        self._active_macro_idx = -1
        self._refresh_macro_list()
        self._save_config()
        self._log_msg(f"Deleted macro: {name}")

    def _save_to_selected_macro(self):
        if not self._macro_events:
            self._log_msg("Nothing recorded. Start/stop recording first."); return
        row = self._active_macro_idx
        if row < 0 or row >= len(self._macros):
            name, ok = QInputDialog.getText(self, "Save Macro", "Name for this macro:")
            if not ok or not name.strip(): return
            name = name.strip()
            self._macros.append({"name": name, "events": []})
            self._refresh_macro_list()
            row = len(self._macros) - 1
            self._macro_list.setCurrentRow(row)
            self._active_macro_idx = row
        self._macros[row]["events"] = list(self._macro_events)
        n   = len(self._macro_events)
        dur = self._macro_events[-1]["t"] if self._macro_events else 0
        self._refresh_macro_list()
        self._save_config()
        self._log_msg(f"Saved to '{self._macros[row]['name']}': {n} events, {dur:.1f}s")

    def get_macro_events(self, name: str):
        """Return events list for macro by name. Used by ScriptEngine play_macro()."""
        for m in self._macros:
            if m["name"] == name:
                return m.get("events", [])
        return None

    # ─────────────────────────────────────────────────────────────────────────
    # Macro recorder / player
    # ─────────────────────────────────────────────────────────────────────────

    def _start_record(self):
        rec_kb    = self._cb_rec_keyboard.isChecked()
        rec_mouse = self._cb_rec_mouse.isChecked()

        if not rec_kb and not rec_mouse:
            self._log_msg("⚠ Select at least one input to record (Keyboard / Mouse).")
            return

        if rec_kb and not KEYBOARD_OK and not INTERCEPTION_OK:
            self._log_msg("⚠ keyboard lib not installed — keyboard events won't be recorded. "
                          "pip install keyboard + run as Admin")

        mode_str = ("Keyboard + Mouse" if rec_kb and rec_mouse
                    else "Keyboard only" if rec_kb
                    else "Mouse only")
        self._macro_recorder.start_recording(record_mouse=rec_mouse, record_keyboard=rec_kb)
        self._rec_status.setText(f"RECORDING  [{mode_str}]")
        self._rec_status.setStyleSheet(f"color:{T['red']};font-size:11px;font-weight:bold;")
        self._btn_rec_start.setEnabled(False)
        self._btn_rec_stop.setEnabled(True)
        self._cb_rec_keyboard.setEnabled(False)
        self._cb_rec_mouse.setEnabled(False)
        self._rec_timer = QTimer()
        self._rec_timer.timeout.connect(self._update_rec_count)
        self._rec_timer.start(400)
        self._log_msg(f"Recording started — {mode_str}")

    def _update_rec_count(self):
        n = self._macro_recorder.event_count()
        self._rec_info.setText(f"Recording... {n} events captured")

    def _stop_record(self):
        self._macro_recorder.stop_recording()
        if hasattr(self, "_rec_timer"): self._rec_timer.stop()
        self._macro_events = list(self._macro_recorder.events)
        n     = len(self._macro_events)
        dur   = self._macro_events[-1]["t"] if self._macro_events else 0
        kb_n  = sum(1 for e in self._macro_events if e["type"] in ("key_down", "key_up"))
        clk_n = sum(1 for e in self._macro_events if e["type"] in ("mouse_down", "mouse_up"))
        mov_n = sum(1 for e in self._macro_events if e["type"] == "mouse_move")
        self._rec_info.setText(
            f"{n} events  ({kb_n} key, {clk_n} clicks, {mov_n} moves) — {dur:.1f}s"
        )
        self._rec_status.setText("Recorded ✓")
        self._rec_status.setStyleSheet(f"color:{T['green']};font-size:11px;font-weight:bold;")
        self._btn_rec_start.setEnabled(True)
        self._btn_rec_stop.setEnabled(False)
        self._cb_rec_keyboard.setEnabled(True)
        self._cb_rec_mouse.setEnabled(True)
        self._log_msg(f"Recorded: {n} events  ({kb_n} kb, {clk_n} clicks, {mov_n} moves) — {dur:.1f}s")

    def _play_macro(self):
        row = self._active_macro_idx
        if row < 0 or row >= len(self._macros):
            self._log_msg("Select a macro in the library first."); return
        events = self._macros[row].get("events", [])
        if not events:
            self._log_msg("Selected macro has no events. Record and save first."); return
        name = self._macros[row]["name"]
        loop = self._cb_loop.isChecked()
        self._macro_player = MacroPlayer(events, loop=loop)
        self._macro_player.log_sig.connect(self._log_msg)
        self._macro_player.finished.connect(self._on_macro_finished)
        self._macro_player.start()
        self._play_status.setText(f"Playing: {name}")
        self._play_status.setStyleSheet(f"color:{T['green']};font-size:11px;font-weight:bold;")
        self._btn_play_start.setEnabled(False)
        self._btn_play_stop.setEnabled(True)
        self._log_msg(f"Playing macro: {name} ({len(events)} events, loop={loop})")

    def _stop_macro(self):
        if self._macro_player:
            self._macro_player.stop_playback()

    def _on_macro_finished(self):
        self._play_status.setText("Idle")
        self._play_status.setStyleSheet(f"color:{T['fg2']};font-size:11px;font-weight:bold;")
        self._btn_play_start.setEnabled(True)
        self._btn_play_stop.setEnabled(False)

    # ─────────────────────────────────────────────────────────────────────────
    # Hotkey management
    # ─────────────────────────────────────────────────────────────────────────

    def _set_hotkey(self, action_id: str, edit, status_lbl):
        combo = edit.text().strip()
        if not combo:
            self._clear_hotkey(action_id, edit, status_lbl); return
        if not KEYBOARD_OK:
            status_lbl.setText("no kb lib")
            status_lbl.setStyleSheet(f"color:{T['red']};font-size:10px;")
            self._log_msg("keyboard lib not installed — hotkeys unavailable. pip install keyboard + run as Admin")
            return
        old = self._hotkey_handles.pop(action_id, None)
        if old:
            try: _kb.remove_hotkey(old)
            except Exception: pass
        try:
            handle = _kb.add_hotkey(combo, lambda a=action_id: self._fire_hotkey(a), suppress=False)
            self._hotkey_handles[action_id] = handle
            self._hotkeys[action_id] = combo
            status_lbl.setText(f"[{combo}]")
            status_lbl.setStyleSheet(f"color:{T['green']};font-size:10px;")
            self._log_msg(f"Hotkey set: [{combo}] -> {action_id}")
            self._save_config()
        except Exception as ex:
            status_lbl.setText("error")
            status_lbl.setStyleSheet(f"color:{T['red']};font-size:10px;")
            self._log_msg(f"Hotkey error for '{combo}': {ex}")

    def _clear_hotkey(self, action_id: str, edit, status_lbl):
        old = self._hotkey_handles.pop(action_id, None)
        if old:
            try: _kb.remove_hotkey(old)
            except Exception: pass
        self._hotkeys[action_id] = ""
        edit.clear()
        status_lbl.setText("")
        self._log_msg(f"Hotkey cleared: {action_id}")
        self._save_config()

    def _fire_hotkey(self, action_id: str):
        QTimer.singleShot(0, lambda a=action_id: self._dispatch_hotkey(a))

    def _dispatch_hotkey(self, action_id: str):
        if   action_id == "run_active_script":  self._run_active_script()
        elif action_id == "stop_active_script": self._stop_active_script()
        elif action_id == "stop_all_scripts":   self._stop_all_scripts()
        elif action_id == "rec_start":          self._start_record()
        elif action_id == "rec_stop":           self._stop_record()
        elif action_id == "play_macro":         self._play_macro()
        elif action_id == "stop_macro":         self._stop_macro()

    def _restore_hotkeys(self):
        if not KEYBOARD_OK: return
        for action_id, combo in self._hotkeys.items():
            if not combo: continue
            edit = self._hk_edits.get(action_id)
            old  = self._hotkey_handles.pop(action_id, None)
            if old:
                try: _kb.remove_hotkey(old)
                except Exception: pass
            try:
                handle = _kb.add_hotkey(combo, lambda a=action_id: self._fire_hotkey(a), suppress=False)
                self._hotkey_handles[action_id] = handle
                if edit: edit.setText(combo)
            except Exception as ex:
                self._log_msg(f"Could not restore hotkey [{combo}] for {action_id}: {ex}")

    # ─────────────────────────────────────────────────────────────────────────
    # Config load/save  (internal helpers used by Config Manager)
    # ─────────────────────────────────────────────────────────────────────────

    _CONFIGS_DIR = "configs"

    def _cfg_dir(self) -> str:
        os.makedirs(self._CONFIGS_DIR, exist_ok=True)
        return self._CONFIGS_DIR

    def _cfg_path(self, name: str) -> str:
        return os.path.join(self._cfg_dir(), f"{name}.json")

    def _cfg_refresh_list(self):
        """Repopulate the list widget from the configs/ folder."""
        if not hasattr(self, "_cfg_list"): return
        self._cfg_list.clear()
        folder = self._cfg_dir()
        names = sorted(f[:-5] for f in os.listdir(folder) if f.endswith(".json"))
        for n in names:
            item = QListWidgetItem(n)
            if n == self._active_config_name:
                item.setForeground(QColor(T["accent"]))
                item.setText(f"{n}  ✔")
            self._cfg_list.addItem(item)

    def _cfg_current_name(self) -> str:
        item = self._cfg_list.currentItem()
        if not item: return ""
        return item.text().replace("  ✔", "").strip()

    def _cfg_collect(self) -> dict:
        if self._active_script_idx >= 0 and self._active_script_idx < len(self._scripts):
            self._scripts[self._active_script_idx]["code"] = self._script_edit.toPlainText()
        return {
            "btn_defs":    self._btn_defs,
            "ctrl_defs":   self._ctrl_defs,
            "game_region": self._game_region,
            "scripts":     self._scripts,
            "macros":      self._macros,
            "hotkeys":     self._hotkeys,
        }

    def _cfg_write(self, path: str, data: dict):
        tmp_path = path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        except Exception:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

    def _cfg_apply(self, data: dict):
        self._btn_defs    = data.get("btn_defs", [])
        self._ctrl_defs   = data.get("ctrl_defs", [])
        self._game_region = data.get("game_region")
        saved_scripts = data.get("scripts")
        if saved_scripts:
            self._scripts = saved_scripts
        elif data.get("script"):
            self._scripts = [{"name": "script_1", "code": data["script"]}]
        else:
            self._scripts = []
        self._macros = data.get("macros", [])
        saved_hk = data.get("hotkeys", {})
        for k in self._hotkeys:
            if k in saved_hk:
                self._hotkeys[k] = saved_hk[k]
        if self._game_region:
            r = self._game_region
            self._lbl_game_reg.setText(f"{r[0]},{r[1]}  {r[2]}x{r[3]}")
            self._lbl_game_reg.setStyleSheet(f"color:{T['green']};font-size:10px;")
        self._refresh_btn_list()
        self._refresh_ctrl_list()
        self._refresh_script_list()
        self._refresh_macro_list()
        self._restore_hotkeys()
        if self._scripts:
            self._script_list.setCurrentRow(0)
            self._on_script_selected(0)

    # ─────────────────────────────────────────────────────────────────────────
    # Config Manager button handlers
    # ─────────────────────────────────────────────────────────────────────────

    def _cfg_new(self):
        name, ok = QInputDialog.getText(self, "New Config", "Config name:")
        if not ok or not name.strip(): return
        name = name.strip()
        path = self._cfg_path(name)
        if os.path.exists(path):
            QMessageBox.warning(self, "Exists", f'"{name}" already exists. Use Save As or rename.')
            return
        try:
            self._cfg_write(path, self._cfg_collect())
            self._active_config_name = name
            self._lbl_cfg.setText(f"Config: {name}")
            self._cfg_refresh_list()
            self._write_default_cfg(name)
            self._log_msg(f"Created config: {name}")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _cfg_save(self):
        if not self._active_config_name:
            self._cfg_save_as(); return
        try:
            self._cfg_write(self._cfg_path(self._active_config_name), self._cfg_collect())
            self._log_msg(f"Saved: {self._active_config_name}")
            self._cfg_refresh_list()
        except Exception as e:
            QMessageBox.critical(self, "Save Error", str(e))

    def _cfg_save_as(self):
        name, ok = QInputDialog.getText(self, "Save As", "Save config as:", text=self._active_config_name)
        if not ok or not name.strip(): return
        name = name.strip()
        path = self._cfg_path(name)
        if os.path.exists(path):
            if QMessageBox.question(self, "Overwrite?", f'"{name}" already exists. Overwrite?',
                    QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
                return
        try:
            self._cfg_write(path, self._cfg_collect())
            self._active_config_name = name
            self._lbl_cfg.setText(f"Config: {name}")
            self._cfg_refresh_list()
            self._write_default_cfg(name)
            self._log_msg(f"Saved as: {name}")
        except Exception as e:
            QMessageBox.critical(self, "Save Error", str(e))

    def _cfg_load_selected(self):
        name = self._cfg_current_name()
        if not name: return
        path = self._cfg_path(name)
        if not os.path.exists(path):
            QMessageBox.warning(self, "Not Found", f"Config file not found:\n{path}")
            self._cfg_refresh_list(); return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            self._cfg_apply(data)
            self._active_config_name = name
            self._lbl_cfg.setText(f"Config: {name}")
            self._cfg_refresh_list()
            self._write_default_cfg(name)
            self._log_msg(f"Loaded: {name}")
        except Exception as e:
            QMessageBox.critical(self, "Load Error", str(e))

    def _cfg_rename(self):
        name = self._cfg_current_name()
        if not name: self._log_msg("Select a config to rename."); return
        new_name, ok = QInputDialog.getText(self, "Rename Config", "New name:", text=name)
        if not ok or not new_name.strip() or new_name.strip() == name: return
        new_name = new_name.strip()
        new_path = self._cfg_path(new_name)
        if os.path.exists(new_path):
            QMessageBox.warning(self, "Exists", f'"{new_name}" already exists.'); return
        try:
            os.rename(self._cfg_path(name), new_path)
            if self._active_config_name == name:
                self._active_config_name = new_name
                self._lbl_cfg.setText(f"Config: {new_name}")
            self._cfg_refresh_list()
            self._log_msg(f"Renamed: {name} > {new_name}")
        except Exception as e:
            QMessageBox.critical(self, "Rename Error", str(e))

    def _cfg_delete(self):
        name = self._cfg_current_name()
        if not name: self._log_msg("Select a config to delete."); return
        if QMessageBox.question(self, "Delete Config", f'Delete "{name}"?  This cannot be undone.',
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            os.remove(self._cfg_path(name))
            if self._active_config_name == name:
                self._active_config_name = ""
                self._lbl_cfg.setText("No config loaded")
            self._cfg_refresh_list()
            self._log_msg(f"Deleted: {name}")
        except Exception as e:
            QMessageBox.critical(self, "Delete Error", str(e))

    def _cfg_export(self):
        name = self._active_config_name or "config"
        path, _ = QFileDialog.getSaveFileName(self, "Export Config", f"{name}.json", "JSON files (*.json)")
        if not path: return
        try:
            self._cfg_write(path, self._cfg_collect())
            self._log_msg(f"Exported to: {os.path.basename(path)}")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", str(e))

    def _cfg_import(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import Config", "", "JSON files (*.json)")
        if not path: return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            QMessageBox.critical(self, "Import Error", f"Could not read file:\n{e}"); return
        suggested = os.path.splitext(os.path.basename(path))[0]
        name, ok = QInputDialog.getText(self, "Import Config", "Save imported config as:", text=suggested)
        if not ok or not name.strip(): return
        name = name.strip()
        dest = self._cfg_path(name)
        if os.path.exists(dest):
            if QMessageBox.question(self, "Overwrite?", f'"{name}" already exists. Overwrite?',
                    QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
                return
        try:
            self._cfg_write(dest, data)
            self._cfg_apply(data)
            self._active_config_name = name
            self._lbl_cfg.setText(f"Config: {name}")
            self._cfg_refresh_list()
            self._log_msg(f"Imported as: {name}")
        except Exception as e:
            QMessageBox.critical(self, "Import Error", str(e))

    # ─────────────────────────────────────────────────────────────────────────
    # Legacy shims — all internal _save_config calls still work unchanged
    # ─────────────────────────────────────────────────────────────────────────

    def _write_default_cfg(self, name: str):
        """Persist the active config name to configs/default.cfg."""
        try:
            os.makedirs(self._CONFIGS_DIR, exist_ok=True)
            with open(DEFAULT_CFG, "w", encoding="utf-8") as f:
                f.write(name)
        except Exception as e:
            print(f"default.cfg write error: {e}")

    def _read_default_cfg(self) -> str:
        """Return the last-used config name from configs/default.cfg, or ''."""
        try:
            if os.path.exists(DEFAULT_CFG):
                with open(DEFAULT_CFG, encoding="utf-8") as f:
                    return f.read().strip()
        except Exception as e:
            print(f"default.cfg read error: {e}")
        return ""

    def _save_config(self):
        """Auto-save to active config, or fall back to CONFIG_FILE."""
        try:
            if self._active_config_name:
                self._cfg_write(self._cfg_path(self._active_config_name), self._cfg_collect())
                self._write_default_cfg(self._active_config_name)
            else:
                self._cfg_write(CONFIG_FILE, self._cfg_collect())
        except Exception as e:
            print(f"Config save error: {e}")

    def _load_config(self):
        """Startup: read configs/default.cfg for the last-used config name and load it.
        If default.cfg doesn't exist it is created empty. If it's empty, nothing is loaded.
        """
        os.makedirs(self._CONFIGS_DIR, exist_ok=True)
        if not os.path.exists(DEFAULT_CFG):
            open(DEFAULT_CFG, "w", encoding="utf-8").close()   # create empty
        last = self._read_default_cfg()
        if last:
            path = self._cfg_path(last)
            if os.path.exists(path):
                try:
                    with open(path, encoding="utf-8") as f:
                        data = json.load(f)
                    self._cfg_apply(data)
                    self._active_config_name = last
                    self._lbl_cfg.setText(f"Config: {last}")
                    self._log_msg(f"Auto-loaded last config: {last}")
                except Exception as e:
                    print(f"Auto-load config error ({last}): {e}")
            else:
                self._log_msg(f"⚠ Last config '{last}' not found in configs/")
        self._cfg_refresh_list()

    def closeEvent(self, e):
        self._save_config()
        for eng in self._script_engines.values():
            try: eng.stop()
            except Exception: pass
        if self._macro_player:
            self._macro_player.stop_playback()
        if KEYBOARD_OK:
            for handle in list(self._hotkey_handles.values()):
                try: _kb.remove_hotkey(handle)
                except Exception: pass
        try:
            keys_cleanup()
        except Exception:
            pass
        e.accept()


# ═══════════════════════════════════════════════════════════════════════════════
#  Entry Point
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())