"""
core/dialogs.py — Overlays, region highlighters, pixel pickers, button definition dialogs, and editor tools
"""

import os
import io
import re
import math
import copy
import base64
import json
import traceback
from typing import Optional, List, Dict, Any, Tuple

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QSpinBox, QDoubleSpinBox, QTextEdit,
    QGroupBox, QSplitter, QDialog, QDialogButtonBox,
    QListWidget, QListWidgetItem, QCheckBox, QMessageBox,
    QTabWidget, QComboBox, QScrollArea, QFrame, QSizePolicy,
    QLineEdit, QSlider, QRadioButton, QButtonGroup, QColorDialog,
    QInputDialog, QFormLayout, QGridLayout, QStackedWidget
)
from PySide6.QtCore import Qt, Signal, QObject, QThread, QRect, QTimer, QPoint
from PySide6.QtGui import (
    QPixmap, QImage, QPainter, QPen, QColor,
    QFont, QBrush, QSyntaxHighlighter, QTextCharFormat, QCursor
)

try:
    from PIL import Image, ImageDraw, ImageGrab, ImageEnhance, ImageChops
    PIL_OK = True
except ImportError:
    PIL_OK = False

try:
    import pytesseract
    OCR_OK = PIL_OK
except ImportError:
    OCR_OK = False

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

from core.theme import (
    T, _ss, _center_on_screen, _vdesk,
    _mkbtn, _sep, _sep_v, _section_lbl, _section_lbl_small
)
from core.models import DEFAULT_BTN, DEFAULT_CTRL
from core.vision import (
    capture, capture_sct, OCR_CHANNELS, OCR_DEFAULT_PASSES,
    _apply_ocr_channel, enhance_ocr, ocr_find_text, ocr_read_number,
    pixel_match, color_match, image_match, _detect_highlight, detect_button
)

_REGION_EDIT_LOCKED = True

class RegionSelector(QWidget):
    """
    Full-screen translucent drag-to-select overlay.

    Never uses exec() — completely event-driven.
    Pass a callback to open():
        RegionSelector.open(callback)
    The callback receives (x, y, w, h) when the user finishes drawing.
    The overlay closes itself; the parent never hides or changes state.

    Also works as a standalone widget:
        sel = RegionSelector()
        sel.done.connect(my_slot)   # slot(x, y, w, h)
        sel.show()
    """
    done = Signal(int, int, int, int)   # emitted on successful selection

    # Singleton — only one overlay can exist at a time
    _instance = None

    @staticmethod
    def open(callback):
        """
        Show the overlay.  callback(x, y, w, h) is called on success.
        Safe to call from any context — no exec(), no nesting.
        """
        if RegionSelector._instance is not None:
            try: RegionSelector._instance.close()
            except Exception: pass
        sel = RegionSelector()
        sel.done.connect(callback)
        sel.show()
        sel.raise_()
        sel.activateWindow()
        RegionSelector._instance = sel

    def __init__(self):
        super().__init__(None)   # no parent
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating, False)  # we DO want focus
        self.setCursor(Qt.CrossCursor)
        vx, vy, vw, vh = _vdesk()
        self.setGeometry(vx, vy, vw, vh)
        self._start = None
        self._curr  = None

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._start = e.globalPosition().toPoint()
            self._curr  = self._start
            self.update()

    def mouseMoveEvent(self, e):
        if self._start:
            self._curr = e.globalPosition().toPoint()
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton or not self._start:
            return
        p2 = e.globalPosition().toPoint()
        x  = min(self._start.x(), p2.x())
        y  = min(self._start.y(), p2.y())
        w  = abs(self._start.x() - p2.x())
        h  = abs(self._start.y() - p2.y())
        RegionSelector._instance = None
        self.close()
        if w > 4 and h > 4:
            self.done.emit(x, y, w, h)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            RegionSelector._instance = None
            self.close()

    def closeEvent(self, e):
        RegionSelector._instance = None
        e.accept()

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 100))
        if self._start and self._curr:
            lp1 = self.mapFromGlobal(self._start)
            lp2 = self.mapFromGlobal(self._curr)
            rect = QRect(lp1, lp2).normalized()
            p.setPen(QPen(QColor(T['accent']), 2))
            p.setBrush(QColor(0, 212, 255, 40))
            p.drawRect(rect)
            pen = QPen(QColor(255, 255, 255, 60), 1, Qt.DashLine)
            p.setPen(pen)
            p.drawLine(lp2.x(), 0, lp2.x(), self.height())
            p.drawLine(0, lp2.y(), self.width(), lp2.y())
            p.setPen(QPen(QColor(T['accent']), 1))
            font = p.font(); font.setBold(True); font.setPointSize(9)
            p.setFont(font)
            lbl = f" {rect.width()} × {rect.height()} "
            tx = rect.x() + rect.width() // 2 - 40
            ty = rect.y() - 6 if rect.y() > 20 else rect.y() + rect.height() + 16
            p.fillRect(tx - 4, ty - 13, len(lbl) * 7 + 8, 18, QColor(0, 0, 0, 160))
            p.drawText(tx, ty, lbl)


class RegionHighlighter(QWidget):
    """Persistent coloured border overlay with optional drag + resize.

    Read-only mode (editable=False, default)
    -----------------------------------------
    - WindowTransparentForInput — never steals mouse/keyboard.
    - Stays on screen until .close() is called.
    - Call update_geometry(x,y,w,h) to move/resize it.

    Editable mode (editable=True)
    ------------------------------
    - Drag the inside to move the region.
    - Drag any of the 8 corner/edge handles to resize.
    - Emits region_changed(x, y, w, h) whenever the region changes.
    - A small toolbar (RegionEditBar) is shown alongside it with live
      x/y/w/h spinboxes and a Done button.
    """

    region_changed = Signal(int, int, int, int)   # x, y, w, h

    _HANDLE_SIZE = 10   # px, square hit area for resize handles

    def __init__(self, x, y, w, h, label="", color=None, editable=False):
        super().__init__(None)
        self._color    = QColor(color) if isinstance(color, str) else (color or QColor(0, 212, 255))
        self._label    = label
        self._rx       = int(x)
        self._ry       = int(y)
        self._rw       = max(10, int(w))
        self._rh       = max(10, int(h))
        self._editable = editable

        flags = (Qt.FramelessWindowHint |
                 Qt.WindowStaysOnTopHint |
                 Qt.Tool)
        if not editable:
            flags |= Qt.WindowTransparentForInput
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating, not editable)
        if editable:
            self.setCursor(Qt.SizeAllCursor)

        # drag/resize state
        self._drag_start    = None   # QPoint where drag began (global)
        self._orig_rect     = None   # (rx,ry,rw,rh) at drag start
        self._resize_edge   = None   # one of 'n','s','e','w','nw','ne','sw','se', or None=move

        self._apply_geo()

    # ── geometry ──────────────────────────────────────────────────────────────

    _PAD = 3

    def _apply_geo(self):
        P = self._PAD
        self.setGeometry(self._rx - P, self._ry - P,
                         self._rw + P*2, self._rh + P*2)

    def update_geometry(self, x, y, w, h, emit=False):
        self._rx, self._ry = int(x), int(y)
        self._rw, self._rh = max(10, int(w)), max(10, int(h))
        self._apply_geo()
        self.update()
        if emit:
            self.region_changed.emit(self._rx, self._ry, self._rw, self._rh)

    @property
    def region(self):
        return (self._rx, self._ry, self._rw, self._rh)

    # ── painting ──────────────────────────────────────────────────────────────

    def paintEvent(self, _):
        p    = QPainter(self)
        c    = self._color
        P    = self._PAD
        W, H = self.width(), self.height()

        # fill
        p.setPen(QPen(c, 2, Qt.DashLine if not self._editable else Qt.SolidLine))
        p.setBrush(QColor(c.red(), c.green(), c.blue(), 18))
        p.drawRect(P - 1, P - 1, W - P*2 + 1, H - P*2 + 1)

        # label
        if self._label:
            font = p.font(); font.setBold(True); font.setPointSize(8)
            p.setFont(font)
            tw = len(self._label) * 7 + 10
            p.fillRect(P, P, min(tw, W - P*2), 19,
                       QColor(c.red(), c.green(), c.blue(), 200))
            p.setPen(QColor(255, 255, 255))
            p.drawText(P + 4, P + 13, self._label)

        # resize handles (editable only)
        if self._editable:
            p.setPen(QPen(c, 1))
            p.setBrush(QColor(255, 255, 255, 220))
            HS = self._HANDLE_SIZE
            for hx, hy in self._handle_positions().values():
                p.drawRect(hx - HS//2, hy - HS//2, HS, HS)

    # ── handle positions (widget-local coords) ───────────────────────────────

    def _handle_positions(self):
        """Return (x,y) widget-local centre of each resize handle."""
        P = self._PAD
        W, H = self.width(), self.height()
        mx, my = P + (W - P*2)//2, P + (H - P*2)//2
        return {
            'nw': (P, P),         'n': (mx, P),         'ne': (W-P, P),
            'w':  (P, my),                               'e':  (W-P, my),
            'sw': (P, H-P),       's': (mx, H-P),        'se': (W-P, H-P),
        }

    def _edge_at(self, lx, ly):
        """Return edge name if (lx,ly) widget-local is on a handle, else None."""
        HS = self._HANDLE_SIZE + 2
        for edge, (hx, hy) in self._handle_positions().items():
            if abs(lx - hx) <= HS//2 and abs(ly - hy) <= HS//2:
                return edge
        return None

    # ── mouse events (editable only) ─────────────────────────────────────────

    def mousePressEvent(self, e):
        if not self._editable or e.button() != Qt.LeftButton:
            return
        self._drag_start  = e.globalPosition().toPoint()
        self._orig_rect   = (self._rx, self._ry, self._rw, self._rh)
        self._resize_edge = self._edge_at(e.position().toPoint().x(), e.position().toPoint().y())
        if self._resize_edge:
            self.setCursor(Qt.SizeFDiagCursor)
        else:
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        if not self._editable or self._drag_start is None:
            return
        dx = e.globalPosition().toPoint().x() - self._drag_start.x()
        dy = e.globalPosition().toPoint().y() - self._drag_start.y()
        ox, oy, ow, oh = self._orig_rect

        if self._resize_edge is None:
            # move
            self._rx, self._ry = ox + dx, oy + dy
        else:
            # resize — update the appropriate edges
            edge = self._resize_edge
            nx, ny, nw, nh = ox, oy, ow, oh
            if 'e' in edge: nw = max(20, ow + dx)
            if 's' in edge: nh = max(20, oh + dy)
            if 'w' in edge: nx, nw = ox + dx, max(20, ow - dx)
            if 'n' in edge: ny, nh = oy + dy, max(20, oh - dy)
            self._rx, self._ry, self._rw, self._rh = nx, ny, nw, nh

        self._apply_geo()
        self.update()
        self.region_changed.emit(self._rx, self._ry, self._rw, self._rh)

    def mouseReleaseEvent(self, e):
        if not self._editable: return
        self._drag_start = None
        self.setCursor(Qt.SizeAllCursor)

    def mouseDoubleClickEvent(self, e):
        """Double-click inside (not on handle) to finish editing."""
        if self._editable and self._edge_at(e.position().toPoint().x(), e.position().toPoint().y()) is None:
            self.region_changed.emit(self._rx, self._ry, self._rw, self._rh)

    def set_editable(self, editable: bool):
        """Switch between read-only (click-through) and editable (drag/resize) at runtime.

        When a RegionEditBar is associated via .bar, it is shown/hidden automatically.
        """
        if self._editable == editable:
            return
        self._editable = editable

        # Rebuild window flags — Qt requires hide/show to apply flag changes
        flags = (Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        if not editable:
            flags |= Qt.WindowTransparentForInput
        was_visible = self.isVisible()
        self.hide()
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_ShowWithoutActivating, not editable)
        if editable:
            self.setCursor(Qt.SizeAllCursor)
        else:
            self.unsetCursor()
            self._drag_start = None   # cancel any in-progress drag
        if was_visible:
            self.show()
        self.update()
        # Bar visibility is not controlled here — it's always shown when a region is open.
# Alias for backward compatibility
PersistentHighlight = RegionHighlighter

# ── Floating edit toolbar shown alongside an editable RegionHighlighter ───────

class RegionEditBar(QWidget):
    """Small always-on-top toolbar with x/y/w/h spinboxes + Done button.

    Stays positioned just above the overlay.  Spinboxes are two-way: dragging
    the overlay updates them; editing them updates the overlay.

    Contains a 🔒/🔓 toggle that switches the linked overlay between
    read-only (click-through) and editable (drag/resize) mode.
    The bar itself is always visible whenever the region overlay is shown.
    """

    done_sig    = Signal()
    region_sig  = Signal(int, int, int, int)

    def __init__(self, x, y, w, h, label="", parent=None):
        super().__init__(parent, Qt.Tool | Qt.WindowStaysOnTopHint |
                         Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating, False)
        self.setStyleSheet(
            f"background:{T['panel']};border:1px solid {T['accent']};"
            f"border-radius:6px;color:{T['fg']};")

        lay = QHBoxLayout(self); lay.setContentsMargins(6,4,6,4); lay.setSpacing(6)

        title = QLabel(f"Region: {label}" if label else "Region")
        title.setStyleSheet(f"color:{T['accent']};font-weight:bold;font-size:11px;")
        lay.addWidget(title)

        _sp_style = (
            f"QSpinBox{{background:{T['card']};color:{T['fg']};"
            f"border:1px solid {T['border2']};border-radius:3px;"
            f"padding:1px 4px;font-size:11px;min-width:58px;}}")

        def _sp(label_text, val, lo=-9999, hi=99999):
            lbl = QLabel(label_text)
            lbl.setStyleSheet(f"color:{T['fg2']};font-size:10px;")
            sp  = QSpinBox(); sp.setRange(lo, hi); sp.setValue(val)
            sp.setStyleSheet(_sp_style); sp.setButtonSymbols(QSpinBox.NoButtons)
            lay.addWidget(lbl); lay.addWidget(sp)
            return sp

        self._sp_x = _sp("X", x)
        self._sp_y = _sp("Y", y)
        self._sp_w = _sp("W", w, 1)
        self._sp_h = _sp("H", h, 1)

        # ── Lock / Unlock toggle ──────────────────────────────────────────────
        self._lock_btn = QPushButton("🔒")
        self._lock_btn.setCheckable(True)
        self._lock_btn.setChecked(True)   # start locked
        self._lock_btn.setFixedSize(30, 26)
        self._lock_btn.setToolTip(
            "🔒 Locked — overlay is click-through, safe to work behind it\n"
            "🔓 Unlocked — drag to move, handles to resize")
        self._lock_btn.setStyleSheet(
            f"QPushButton{{border:1px solid;border-radius:4px;font-size:13px;padding:0;}}"
            f"QPushButton:checked{{background:{T['red2']};border-color:{T['red']};}}"
            f"QPushButton:!checked{{background:{T['green2']};border-color:{T['green']};}}"
            f"QPushButton:hover{{opacity:0.85;}}")
        self._lock_btn.clicked.connect(self._on_lock_toggled)
        lay.addWidget(self._lock_btn)

        # Spinboxes are disabled while locked
        self._set_spinboxes_enabled(False)

        done_btn = QPushButton("✓ Done")
        done_btn.setFixedHeight(26)
        done_btn.setStyleSheet(
            f"QPushButton{{background:{T['green2']};color:white;border:none;"
            f"border-radius:4px;padding:0 10px;font-size:11px;font-weight:bold;}}"
            f"QPushButton:hover{{background:{T['green']};}}")
        done_btn.clicked.connect(self.done_sig)
        lay.addWidget(done_btn)

        cancel_btn = QPushButton("✕")
        cancel_btn.setFixedSize(26, 26)
        cancel_btn.setStyleSheet(
            f"QPushButton{{background:{T['red2']};color:white;border:none;"
            f"border-radius:4px;font-size:12px;}}"
            f"QPushButton:hover{{background:{T['red']};}}")
        cancel_btn.clicked.connect(self._on_cancel)
        lay.addWidget(cancel_btn)
        self._cancelled = False

        # wire spinboxes → emit region_sig
        for sp in (self._sp_x, self._sp_y, self._sp_w, self._sp_h):
            sp.valueChanged.connect(self._on_spin_changed)

        # The linked RegionHighlighter overlay — set after construction
        self._overlay = None

        self.adjustSize()
        self._reposition(x, y)

    def _set_spinboxes_enabled(self, enabled: bool):
        for sp in (self._sp_x, self._sp_y, self._sp_w, self._sp_h):
            sp.setEnabled(enabled)

    def _on_lock_toggled(self):
        locked = self._lock_btn.isChecked()
        self._lock_btn.setText("🔒" if locked else "🔓")
        self._set_spinboxes_enabled(not locked)
        global _REGION_EDIT_LOCKED
        _REGION_EDIT_LOCKED = locked
        if self._overlay is not None:
            self._overlay.set_editable(not locked)

    def _on_spin_changed(self):
        self.region_sig.emit(
            self._sp_x.value(), self._sp_y.value(),
            self._sp_w.value(), self._sp_h.value())

    def _on_cancel(self):
        self._cancelled = True
        self.done_sig.emit()

    def update_values(self, x, y, w, h):
        """Called when the overlay moves — update spinboxes without re-emitting."""
        for sp, v in zip((self._sp_x, self._sp_y, self._sp_w, self._sp_h),
                         (x, y, w, h)):
            sp.blockSignals(True)
            sp.setValue(int(v))
            sp.blockSignals(False)
        self._reposition(x, y)

    def _reposition(self, rx, ry):
        """Float the bar just above the region, clamped to screen."""
        self.adjustSize()
        bw, bh = self.width(), self.height()
        screen  = QApplication.primaryScreen().availableGeometry()
        bx = max(screen.left(), min(rx, screen.right()  - bw))
        by = max(screen.top(),  ry - bh - 8)
        self.move(bx, by)

    @property
    def cancelled(self): return self._cancelled


class PixelPickerDlg(QDialog):
    """Zoom-adjustable pixel picker.

    The region image is shown at a user-controlled zoom (1×–8×).
    A slider + +/– buttons let the user change zoom any time.
    The dialog is capped at 85 % of the monitor's usable area; if the
    zoomed image is larger than that, a scroll area takes over.
    """

    _ZOOM_MIN = 1
    _ZOOM_MAX = 8
    _ZOOM_DEFAULT = 2          # sensible starting zoom (not the old hard-coded ×4)

    def __init__(self, pil_img, existing=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Pick Pixel Colors")
        self.setStyleSheet(f"background:{T['bg']};color:{T['fg']};")
        self.pil_img  = pil_img
        self.points: list = []
        self.colors: list  = []
        self._scale   = self._ZOOM_DEFAULT

        # ── Hard size limit: 85 % of the primary screen's available geometry ──
        screen  = QApplication.primaryScreen().availableGeometry()
        self._max_w = int(screen.width()  * 0.85)
        self._max_h = int(screen.height() * 0.85)

        lay = QVBoxLayout(self)
        lay.setSpacing(6)

        # ── Info label ────────────────────────────────────────────────────
        self._info = QLabel("Click pixels to sample colors. Click OK when done.")
        self._info.setStyleSheet(f"color:{T['accent']};font-weight:bold;padding:4px;")
        lay.addWidget(self._info)

        # ── Required-count row ────────────────────────────────────────────
        row = QHBoxLayout()
        row.addWidget(QLabel("Pixels required to match:"))
        self._sp_req = QSpinBox(); self._sp_req.setRange(1, 50)
        self._sp_req.setValue(existing.get("count_required", 4) if existing else 4)
        row.addWidget(self._sp_req)
        row.addStretch()
        lay.addLayout(row)

        # ── Zoom controls ─────────────────────────────────────────────────
        zoom_row = QHBoxLayout(); zoom_row.setSpacing(6)
        _zl = QLabel("Zoom:")
        _zl.setStyleSheet(f"color:{T['fg2']};font-size:11px;")
        zoom_row.addWidget(_zl)

        btn_zm = QPushButton("−"); btn_zm.setFixedSize(26, 26)
        btn_zm.setStyleSheet(
            f"QPushButton{{background:{T['dim2']};color:{T['fg']};border:none;"
            f"border-radius:4px;font-size:14px;font-weight:bold;}}"
            f"QPushButton:hover{{background:{T['accent']};}}")
        zoom_row.addWidget(btn_zm)

        self._zoom_slider = QSlider(Qt.Horizontal)
        self._zoom_slider.setRange(self._ZOOM_MIN, self._ZOOM_MAX)
        self._zoom_slider.setValue(self._scale)
        self._zoom_slider.setFixedWidth(160)
        self._zoom_slider.setStyleSheet(
            f"QSlider::groove:horizontal{{height:4px;background:{T['border2']};border-radius:2px;}}"
            f"QSlider::handle:horizontal{{width:14px;height:14px;margin:-5px 0;"
            f"background:{T['accent']};border-radius:7px;}}"
            f"QSlider::sub-page:horizontal{{background:{T['accent']};border-radius:2px;}}")
        zoom_row.addWidget(self._zoom_slider)

        btn_zp = QPushButton("+"); btn_zp.setFixedSize(26, 26)
        btn_zp.setStyleSheet(btn_zm.styleSheet())
        zoom_row.addWidget(btn_zp)

        self._zoom_lbl = QLabel(f"{self._scale}×")
        self._zoom_lbl.setStyleSheet(
            f"color:{T['accent']};font-size:12px;font-weight:bold;min-width:28px;")
        zoom_row.addWidget(self._zoom_lbl)

        undo_btn = QPushButton("↩ Undo"); undo_btn.setFixedHeight(26)
        undo_btn.setStyleSheet(
            f"QPushButton{{background:{T['dim2']};color:{T['fg']};border:none;"
            f"border-radius:4px;font-size:10px;padding:0 8px;}}"
            f"QPushButton:hover{{background:{T['accent2']};}}")
        zoom_row.addWidget(undo_btn)

        clr_btn = QPushButton("Clear all"); clr_btn.setFixedHeight(26)
        clr_btn.setStyleSheet(
            f"QPushButton{{background:{T['red2']};color:white;border:none;"
            f"border-radius:4px;font-size:10px;padding:0 8px;}}"
            f"QPushButton:hover{{background:{T['red']};}}")
        zoom_row.addWidget(clr_btn)
        zoom_row.addStretch()
        lay.addLayout(zoom_row)

        # ── Scroll area → image label ─────────────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(False)
        self._scroll.setStyleSheet(
            f"QScrollArea{{border:1px solid {T['border2']};background:{T['card']};}}"
            f"QScrollBar:vertical{{background:{T['bg']};width:10px;border-radius:5px;}}"
            f"QScrollBar::handle:vertical{{background:{T['dim2']};border-radius:5px;min-height:20px;}}"
            f"QScrollBar:horizontal{{background:{T['bg']};height:10px;border-radius:5px;}}"
            f"QScrollBar::handle:horizontal{{background:{T['dim2']};border-radius:5px;min-width:20px;}}")
        self._lbl = QLabel()
        self._lbl.setCursor(Qt.CrossCursor)
        self._lbl.mousePressEvent = self._on_click
        self._scroll.setWidget(self._lbl)
        lay.addWidget(self._scroll, 1)

        # ── OK / Cancel ───────────────────────────────────────────────────
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

        # ── Wire signals ──────────────────────────────────────────────────
        self._zoom_slider.valueChanged.connect(self._on_zoom_changed)
        btn_zm.clicked.connect(lambda: self._zoom_step(-1))
        btn_zp.clicked.connect(lambda: self._zoom_step(+1))
        undo_btn.clicked.connect(self._undo_last)
        clr_btn.clicked.connect(self._clear_all)

        # ── Load existing points ──────────────────────────────────────────
        if existing:
            self.points = list(existing.get("points", []))
            self.colors = list(existing.get("colors", []))

        self._apply_zoom()        # sets pixmap + resizes dialog

    # ── Zoom helpers ──────────────────────────────────────────────────────

    def _zoom_step(self, delta: int):
        v = max(self._ZOOM_MIN, min(self._ZOOM_MAX, self._zoom_slider.value() + delta))
        self._zoom_slider.setValue(v)          # triggers _on_zoom_changed

    def _on_zoom_changed(self, value: int):
        self._scale = value
        self._zoom_lbl.setText(f"{value}×")
        self._apply_zoom()

    def _apply_zoom(self):
        """Re-render the zoomed pixmap and resize the scroll area + dialog."""
        w, h = self.pil_img.size
        zw, zh = w * self._scale, h * self._scale
        self._disp = self.pil_img.resize((zw, zh), Image.NEAREST)
        self._refresh()

        # Size the scroll area: show image at full zoom up to the hard limit.
        # Leave ~120 px vertical for controls; 40 px horizontal for scrollbar.
        avail_w = self._max_w - 40
        avail_h = self._max_h - 120
        scroll_w = min(zw + 4, avail_w)
        scroll_h = min(zh + 4, avail_h)
        self._scroll.setFixedSize(scroll_w, scroll_h)

        # Resize dialog to fit everything, but never exceed the hard limit.
        self.adjustSize()
        new_w = min(self.sizeHint().width(),  self._max_w)
        new_h = min(self.sizeHint().height(), self._max_h)
        self.resize(new_w, new_h)

    def _refresh(self):
        """Redraw the QLabel pixmap with current zoom + dot overlays."""
        img = self._disp.copy().convert("RGBA")
        if self.points and PIL_OK:
            d = ImageDraw.Draw(img)
            for i, (x, y) in enumerate(self.points):
                sx, sy = x * self._scale, y * self._scale
                col = (0, 230, 118, 255)
                r = max(4, self._scale + 2)
                d.ellipse([sx - r, sy - r, sx + r, sy + r], outline=col, width=2)
                d.text((sx + r + 2, sy - 8), str(i + 1), fill=col)
        img  = img.convert("RGB")
        data = img.tobytes("raw", "RGB")
        qi   = QImage(data, img.width, img.height, img.width * 3,
                      QImage.Format_RGB888).copy()
        pix  = QPixmap.fromImage(qi)
        self._lbl.setPixmap(pix)
        self._lbl.resize(pix.size())

    # ── Click handling ────────────────────────────────────────────────────

    def _on_click(self, e):
        x = e.position().toPoint().x() // self._scale
        y = e.position().toPoint().y() // self._scale
        x = max(0, min(self.pil_img.width  - 1, x))
        y = max(0, min(self.pil_img.height - 1, y))
        col = self.pil_img.getpixel((x, y))
        self.points.append([x, y])
        self.colors.append(list(col[:3]))
        self._info.setText(
            f"{len(self.points)} pixel(s) selected  "
            f"— last: ({x}, {y})  RGB {col[:3]}")
        self._refresh()

    # ── Undo / Clear ──────────────────────────────────────────────────────

    def _undo_last(self):
        if self.points:
            self.points.pop(); self.colors.pop()
            self._info.setText(f"{len(self.points)} pixel(s) selected")
            self._refresh()

    def _clear_all(self):
        self.points.clear(); self.colors.clear()
        self._info.setText("All pixels cleared.")
        self._refresh()

    def get_data(self):
        return {"points": self.points, "colors": self.colors,
                "count_required": self._sp_req.value()}


# ═══════════════════════════════════════════════════════════════════════════════
#  Screen Color Picker  (click anywhere on the desktop to sample a color)
# ═══════════════════════════════════════════════════════════════════════════════

class ScreenColorPicker(QDialog):
    """Full-screen translucent overlay — user clicks anywhere to sample that pixel's color.

    Usage:
        picker = ScreenColorPicker(parent)
        if picker.exec() == QDialog.Accepted:
            r, g, b = picker.picked_color   # [R, G, B]
    """

    def __init__(self, parent=None):
        super().__init__(parent,
                         Qt.FramelessWindowHint |
                         Qt.WindowStaysOnTopHint |
                         Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.CrossCursor)
        self.picked_color = [128, 128, 128]
        self._mag_pos     = None      # current cursor position for magnifier
        self._screenshot  = None      # PIL screenshot taken on open

        screen = QApplication.primaryScreen()
        geo    = screen.geometry()
        self.setGeometry(geo)

        # Take a screenshot immediately so we can sample pixels and draw magnifier
        try:
            raw = screen.grabWindow(0)
            buf = QBuffer()
            buf.open(QIODevice.OpenModeFlag.ReadWrite)
            raw.save(buf, "PNG")
            buf.seek(0)
            self._screenshot = Image.open(io.BytesIO(buf.data())).convert("RGB")
        except Exception:
            pass

    def paintEvent(self, e):
        p = QPainter(self)
        # Dim the whole screen
        p.fillRect(self.rect(), QColor(0, 0, 0, 80))

        if self._screenshot and self._mag_pos:
            mx, my = self._mag_pos
            MAG    = 8       # zoom factor inside magnifier
            RAD    = 48      # pixels to each side in source image
            SIZE   = 180     # magnifier widget size (px)
            CROSS  = SIZE // 2

            # Crop source patch
            img_w, img_h = self._screenshot.size
            x0, y0 = max(0, mx - RAD), max(0, my - RAD)
            x1, y1 = min(img_w, mx + RAD + 1), min(img_h, my + RAD + 1)
            patch  = self._screenshot.crop((x0, y0, x1, y1))
            pw, ph = patch.size
            zoomed = patch.resize((pw * MAG, ph * MAG), Image.NEAREST)
            zw, zh = zoomed.size

            # Convert to QPixmap
            data = zoomed.tobytes("raw", "RGB")
            qi   = QImage(data, zw, zh, zw * 3, QImage.Format_RGB888).copy()
            src_pix = QPixmap.fromImage(qi)

            # Draw circular clip
            clip_path = QPainterPath()
            clip_path.addEllipse(mx - SIZE // 2, my - SIZE // 2 - 20, SIZE, SIZE)
            p.setClipPath(clip_path)

            # Center the magnifier on cursor
            draw_x = mx - SIZE // 2
            draw_y = my - SIZE // 2 - 20
            src_cx = (mx - x0) * MAG - SIZE // 2
            src_cy = (my - y0) * MAG - SIZE // 2
            p.drawPixmap(draw_x, draw_y,
                         src_pix,
                         max(0, src_cx), max(0, src_cy), SIZE, SIZE)
            p.setClipping(False)

            # Border + crosshair
            p.setPen(QPen(QColor(255, 255, 255, 200), 2))
            p.drawEllipse(mx - SIZE // 2, my - SIZE // 2 - 20, SIZE, SIZE)
            p.setPen(QPen(QColor(255, 50, 50, 230), 1))
            p.drawLine(mx, my - SIZE // 2 - 20, mx, my + SIZE // 2 - 20)
            p.drawLine(mx - SIZE // 2, my - 20, mx + SIZE // 2, my - 20)

            # Color swatch + hex label
            if 0 <= mx < img_w and 0 <= my < img_h:
                r, g, b = self._screenshot.getpixel((mx, my))[:3]
                swatch_rect = QRect(mx - 36, my + SIZE // 2 - 10, 72, 22)
                p.fillRect(swatch_rect, QColor(r, g, b))
                p.setPen(QColor(255, 255, 255) if (r*0.299+g*0.587+b*0.114) < 128
                         else QColor(0, 0, 0))
                p.setFont(QFont("Courier", 9, QFont.Bold))
                p.drawText(swatch_rect, Qt.AlignCenter, f"#{r:02X}{g:02X}{b:02X}")

        # ESC hint
        p.setClipping(False)
        p.setPen(QColor(255, 255, 255, 160))
        p.setFont(QFont("Arial", 10))
        p.drawText(self.rect().adjusted(0, 0, -10, -10),
                   Qt.AlignBottom | Qt.AlignRight, "Click to pick   ESC to cancel")

    def mouseMoveEvent(self, e):
        self._mag_pos = (int(e.globalPosition().x()), int(e.globalPosition().y()))
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            gx = int(e.globalPosition().x())
            gy = int(e.globalPosition().y())
            if self._screenshot:
                iw, ih = self._screenshot.size
                gx = max(0, min(iw - 1, gx))
                gy = max(0, min(ih - 1, gy))
                self.picked_color = list(self._screenshot.getpixel((gx, gy))[:3])
            self.accept()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.reject()


# ═══════════════════════════════════════════════════════════════════════════════
#  Color Picker Dialog  (pick points on the region image, each with its own
#  target color + threshold; then group them for scripting)
# ═══════════════════════════════════════════════════════════════════════════════

class ColorPickerDlg(QDialog):
    """Multi-color pixel picker.

    Each point you click gets:
      • a pixel ID (0-based)
      • its sampled color shown as a swatch (editable — click swatch to repick
        from anywhere on screen or use the standard color wheel)
      • an individual threshold spinbox

    Below the point list you define *groups*:  give a group a name, assign
    pixel IDs, choose a target color + threshold + min_match count.
    The groups map directly to the script API:
        btn.color.scan()                       → runs all groups, returns dict
        btn.color.scan(pixel_ids=[0,1], color='#FF8C00', threshold=40)
    """

    _ZOOM_MIN     = 1
    _ZOOM_MAX     = 8
    _ZOOM_DEFAULT = 2

    def __init__(self, pil_img, existing=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Color Point Picker")
        self.setStyleSheet(f"background:{T['bg']};color:{T['fg']};")
        self.pil_img  = pil_img
        self._scale   = self._ZOOM_DEFAULT

        # points: [[x,y], ...]   rules: [{"color":[R,G,B],"threshold":40,"label":""}, ...]
        self._points: list = []
        self._rules:  list = []
        self._groups: list = []
        self._pt_checkboxes: dict = {}   # idx → QCheckBox

        screen      = QApplication.primaryScreen().availableGeometry()
        self._max_w = int(screen.width()  * 0.90)
        self._max_h = int(screen.height() * 0.90)

        if existing:
            self._points = [list(p) for p in existing.get("points", [])]
            self._rules  = [dict(r) for r in existing.get("rules",  [])]
            self._groups = [dict(g) for g in existing.get("groups", [])]
            # back-fill rules if fewer than points
            while len(self._rules) < len(self._points):
                self._rules.append({"color": [128,128,128], "threshold": 40, "label": ""})

        self._build_ui()
        self._apply_zoom()

    # ─────────────────────────────────────────────────────────────────────────
    # UI build
    # ─────────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setSpacing(8)

        # ── LEFT: image + zoom ────────────────────────────────────────────
        left = QVBoxLayout(); left.setSpacing(6)

        # zoom row
        zrow = QHBoxLayout(); zrow.setSpacing(6)
        _cb_style = (f"QCheckBox{{color:{T['fg2']};font-size:11px;}}")
        zm_lbl = QLabel("Zoom:"); zm_lbl.setStyleSheet(f"color:{T['fg2']};font-size:11px;")
        zrow.addWidget(zm_lbl)
        self._zm_btn = QPushButton("−"); self._zm_btn.setFixedSize(24, 24)
        self._zp_btn = QPushButton("+"); self._zp_btn.setFixedSize(24, 24)
        for b in (self._zm_btn, self._zp_btn):
            b.setStyleSheet(
                f"QPushButton{{background:{T['dim2']};color:{T['fg']};border:none;"
                f"border-radius:4px;font-size:13px;font-weight:bold;}}"
                f"QPushButton:hover{{background:{T['accent']};}}")
        self._zoom_slider = QSlider(Qt.Horizontal); self._zoom_slider.setRange(1, 8)
        self._zoom_slider.setValue(self._scale); self._zoom_slider.setFixedWidth(120)
        self._zoom_slider.setStyleSheet(
            f"QSlider::groove:horizontal{{height:4px;background:{T['border2']};border-radius:2px;}}"
            f"QSlider::handle:horizontal{{width:13px;height:13px;margin:-5px 0;"
            f"background:{T['accent']};border-radius:7px;}}"
            f"QSlider::sub-page:horizontal{{background:{T['accent']};border-radius:2px;}}")
        self._zoom_lbl = QLabel(f"{self._scale}×")
        self._zoom_lbl.setStyleSheet(f"color:{T['accent']};font-size:12px;font-weight:bold;min-width:26px;")
        zrow.addWidget(self._zm_btn); zrow.addWidget(self._zoom_slider)
        zrow.addWidget(self._zp_btn); zrow.addWidget(self._zoom_lbl); zrow.addStretch()
        left.addLayout(zrow)

        hint = QLabel("Click image to place a point  ·  Right-click to remove last")
        hint.setStyleSheet(f"color:{T['fg2']};font-size:10px;")
        left.addWidget(hint)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(False)
        self._scroll.setStyleSheet(
            f"QScrollArea{{border:1px solid {T['border2']};background:{T['card']};}}"
            f"QScrollBar:vertical{{background:{T['bg']};width:10px;border-radius:5px;}}"
            f"QScrollBar::handle:vertical{{background:{T['dim2']};border-radius:5px;min-height:20px;}}"
            f"QScrollBar:horizontal{{background:{T['bg']};height:10px;border-radius:5px;}}"
            f"QScrollBar::handle:horizontal{{background:{T['dim2']};border-radius:5px;min-width:20px;}}")
        self._img_lbl = QLabel(); self._img_lbl.setCursor(Qt.CrossCursor)
        self._img_lbl.mousePressEvent = self._on_img_click
        self._scroll.setWidget(self._img_lbl)
        left.addWidget(self._scroll, 1)
        root.addLayout(left, 3)

        # ── RIGHT: point list + group editor ─────────────────────────────
        right = QVBoxLayout(); right.setSpacing(8)

        # Point list
        pts_lbl = QLabel("Points"); pts_lbl.setStyleSheet(
            f"color:{T['fg']};font-size:12px;font-weight:bold;")
        pts_hdr = QHBoxLayout()
        pts_hdr.addWidget(pts_lbl)
        sel_all_btn = QPushButton("☑ All"); sel_all_btn.setFixedHeight(20)
        sel_all_btn.setStyleSheet(
            f"QPushButton{{background:{T['dim2']};color:{T['fg2']};border:none;"
            f"border-radius:3px;font-size:9px;padding:0 6px;}}"
            f"QPushButton:hover{{background:{T['accent2']};color:white;}}")
        sel_all_btn.clicked.connect(lambda: self._select_all_points(True))
        sel_none_btn = QPushButton("☐ None"); sel_none_btn.setFixedHeight(20)
        sel_none_btn.setStyleSheet(sel_all_btn.styleSheet())
        sel_none_btn.clicked.connect(lambda: self._select_all_points(False))
        pts_hdr.addStretch()
        pts_hdr.addWidget(sel_all_btn)
        pts_hdr.addWidget(sel_none_btn)
        right.addLayout(pts_hdr)

        self._pt_list = QScrollArea()
        self._pt_list.setWidgetResizable(True)
        self._pt_list.setFixedHeight(220)
        self._pt_list.setStyleSheet(
            f"QScrollArea{{border:1px solid {T['border2']};background:{T['card']};}}")
        self._pt_container = QWidget()
        self._pt_layout    = QVBoxLayout(self._pt_container); self._pt_layout.setSpacing(4)
        self._pt_layout.addStretch()
        self._pt_list.setWidget(self._pt_container)
        right.addWidget(self._pt_list)

        # Group editor
        grp_lbl = QLabel("Groups  (for scripting)"); grp_lbl.setStyleSheet(
            f"color:{T['fg']};font-size:12px;font-weight:bold;")
        right.addWidget(grp_lbl)

        grp_hint = QLabel(
            "btn.color.scan()  →  checks all groups\n"
            "btn.color.scan(pixel_ids=[0,1], color='#FF8C00', threshold=40)\n"
            "btn.color.scan(color='#FFFFFF', threshold=40, min_match=10)")
        grp_hint.setStyleSheet(
            f"color:{T['fg2']};font-size:9px;font-family:monospace;"
            f"background:{T['card']};padding:4px;border-radius:4px;")
        right.addWidget(grp_hint)

        self._grp_list = QScrollArea()
        self._grp_list.setWidgetResizable(True)
        self._grp_list.setStyleSheet(
            f"QScrollArea{{border:1px solid {T['border2']};background:{T['card']};}}")
        self._grp_container = QWidget()
        self._grp_layout    = QVBoxLayout(self._grp_container); self._grp_layout.setSpacing(6)
        self._grp_layout.addStretch()
        self._grp_list.setWidget(self._grp_container)
        right.addWidget(self._grp_list, 1)

        grp_btns = QHBoxLayout(); grp_btns.setSpacing(4)
        add_sel_btn = QPushButton("＋ Group from Selected")
        add_sel_btn.setFixedHeight(28)
        add_sel_btn.setStyleSheet(
            f"QPushButton{{background:{T['accent']};color:white;border:none;"
            f"border-radius:4px;font-size:11px;font-weight:bold;}}"
            f"QPushButton:hover{{background:{T['accent2']};}}")
        add_sel_btn.setToolTip("Tick checkboxes next to points, then click to create a group")
        add_sel_btn.clicked.connect(self._add_group_from_selection)

        add_empty_btn = QPushButton("＋ Empty")
        add_empty_btn.setFixedHeight(28)
        add_empty_btn.setStyleSheet(
            f"QPushButton{{background:{T['dim2']};color:{T['fg']};border:none;"
            f"border-radius:4px;font-size:10px;}}"
            f"QPushButton:hover{{background:{T['dim']};}}")
        add_empty_btn.setToolTip("Add an empty group and fill manually")
        add_empty_btn.clicked.connect(self._add_group_row)

        grp_btns.addWidget(add_sel_btn, 2)
        grp_btns.addWidget(add_empty_btn, 1)
        right.addLayout(grp_btns)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept); btns.rejected.connect(self.reject)
        right.addWidget(btns)
        root.addLayout(right, 2)

        # Wire zoom
        self._zoom_slider.valueChanged.connect(self._on_zoom)
        self._zm_btn.clicked.connect(lambda: self._zoom_step(-1))
        self._zp_btn.clicked.connect(lambda: self._zoom_step(+1))

        # Populate from existing data
        for i in range(len(self._points)):
            self._add_point_row(i)
        for g in self._groups:
            self._add_group_row(g)

    # ─────────────────────────────────────────────────────────────────────────
    # Zoom
    # ─────────────────────────────────────────────────────────────────────────

    def _zoom_step(self, d):
        self._zoom_slider.setValue(
            max(1, min(8, self._zoom_slider.value() + d)))

    def _on_zoom(self, v):
        self._scale = v
        self._zoom_lbl.setText(f"{v}×")
        self._apply_zoom()

    def _apply_zoom(self):
        w, h = self.pil_img.size
        zw, zh = w * self._scale, h * self._scale
        avail_w = self._max_w * 6 // 10 - 40     # left column is ~60 % of max_w
        avail_h = self._max_h - 100
        self._scroll.setFixedSize(min(zw + 4, avail_w), min(zh + 4, avail_h))
        self._refresh_image()
        self.adjustSize()
        self.resize(min(self.sizeHint().width(), self._max_w),
                    min(self.sizeHint().height(), self._max_h))

    # ─────────────────────────────────────────────────────────────────────────
    # Image rendering
    # ─────────────────────────────────────────────────────────────────────────

    def _refresh_image(self):
        w, h   = self.pil_img.size
        zoomed = self.pil_img.resize((w * self._scale, h * self._scale), Image.NEAREST)
        img    = zoomed.convert("RGBA")
        if PIL_OK and self._points:
            d = ImageDraw.Draw(img)
            for i, (x, y) in enumerate(self._points):
                sx, sy = x * self._scale, y * self._scale
                rule   = self._rules[i] if i < len(self._rules) else {}
                # Use fixed high-visibility colors (same as PixelPickerDlg)
                # so dots are always visible regardless of the target color.
                # Show a small swatch of the actual target color as a filled
                # inner dot so the user can still see what color was picked.
                DOT_COL  = (0, 230, 118, 255)    # bright green outline (same as pixel picker)
                FILL_COL = (0, 0, 0, 120)         # semi-transparent dark fill
                rc, gc, bc = rule.get("color", [128, 128, 128])
                SWATCH   = (int(rc), int(gc), int(bc), 200)
                r        = max(5, self._scale + 3)
                rs       = max(2, r - 3)           # inner swatch radius
                d.ellipse([sx-r, sy-r, sx+r, sy+r], outline=DOT_COL, fill=FILL_COL, width=2)
                d.ellipse([sx-rs, sy-rs, sx+rs, sy+rs], fill=SWATCH)
                d.text((sx + r + 3, sy - 8), str(i), fill=DOT_COL)
        img  = img.convert("RGB")
        data = img.tobytes("raw", "RGB")
        qi   = QImage(data, img.width, img.height, img.width * 3,
                      QImage.Format_RGB888).copy()
        pix  = QPixmap.fromImage(qi)
        self._img_lbl.setPixmap(pix)
        self._img_lbl.resize(pix.size())

    # ─────────────────────────────────────────────────────────────────────────
    # Image click — place / remove points
    # ─────────────────────────────────────────────────────────────────────────

    def _on_img_click(self, e):
        x = max(0, min(self.pil_img.width  - 1, e.position().toPoint().x() // self._scale))
        y = max(0, min(self.pil_img.height - 1, e.position().toPoint().y() // self._scale))
        if e.button() == Qt.RightButton:
            if self._points:
                self._points.pop(); self._rules.pop()
                self._remove_last_point_row()
                self._refresh_image()
            return
        # Sample actual pixel color as default target
        col = list(self.pil_img.getpixel((x, y))[:3])
        self._points.append([x, y])
        self._rules.append({"color": col, "threshold": 40, "label": ""})
        self._add_point_row(len(self._points) - 1)
        self._refresh_image()

    # ─────────────────────────────────────────────────────────────────────────
    # Point list rows
    # ─────────────────────────────────────────────────────────────────────────

    def _add_point_row(self, idx: int):
        """Insert a row for point[idx] — includes a selection checkbox."""
        rule = self._rules[idx] if idx < len(self._rules) else {}
        rc, gc, bc = rule.get("color", [128, 128, 128])
        thr        = rule.get("threshold", 40)

        row_w = QWidget()
        row_w.setObjectName(f"pt_row_{idx}")
        row_h = QHBoxLayout(row_w); row_h.setContentsMargins(2,2,2,2); row_h.setSpacing(5)

        # ── selection checkbox ────────────────────────────────────────────
        chk = QCheckBox()
        chk.setFixedSize(16, 16)
        chk.setStyleSheet(
            f"QCheckBox::indicator{{width:14px;height:14px;border-radius:3px;"
            f"border:1px solid {T['border2']};background:{T['bg']};}}"
            f"QCheckBox::indicator:checked{{background:{T['accent']};border-color:{T['accent']};}}")
        chk._pt_idx = idx
        self._pt_checkboxes[idx] = chk
        row_h.addWidget(chk)

        id_lbl = QLabel(f"#{idx}")
        id_lbl.setStyleSheet(f"color:{T['fg2']};font-size:10px;min-width:22px;")
        row_h.addWidget(id_lbl)

        # Color swatch
        swatch = QPushButton()
        swatch.setFixedSize(32, 22)
        swatch.setStyleSheet(
            f"background:rgb({rc},{gc},{bc});"
            f"border:1px solid {T['border2']};border-radius:3px;")
        swatch.setToolTip("Click: color wheel   Right-click: pick from screen")
        swatch._color = [rc, gc, bc]
        swatch._idx   = idx

        def _swatch_press(pos, sw=swatch):
            if pos.button() == Qt.RightButton:
                picker = ScreenColorPicker(self)
                if picker.exec() == QDialog.Accepted:
                    r2, g2, b2 = picker.picked_color
                    sw._color = [r2, g2, b2]
                    sw.setStyleSheet(
                        f"background:rgb({r2},{g2},{b2});"
                        f"border:1px solid {T['border2']};border-radius:3px;")
                    self._rules[sw._idx]["color"] = [r2, g2, b2]
                    self._refresh_image()
            else:
                r2, g2, b2 = sw._color
                chosen = QColorDialog.getColor(QColor(r2, g2, b2), self, "Pick color")
                if chosen.isValid():
                    sw._color = [chosen.red(), chosen.green(), chosen.blue()]
                    sw.setStyleSheet(
                        f"background:rgb({chosen.red()},{chosen.green()},{chosen.blue()});"
                        f"border:1px solid {T['border2']};border-radius:3px;")
                    self._rules[sw._idx]["color"] = sw._color[:]
                    self._refresh_image()

        swatch.mousePressEvent = _swatch_press
        row_h.addWidget(swatch)

        hex_lbl = QLabel(f"#{rc:02X}{gc:02X}{bc:02X}")
        hex_lbl.setStyleSheet(
            f"color:{T['fg2']};font-size:9px;font-family:monospace;min-width:52px;")
        row_h.addWidget(hex_lbl)

        thr_sp = QSpinBox(); thr_sp.setRange(0, 441); thr_sp.setValue(thr)
        thr_sp.setFixedWidth(50); thr_sp.setToolTip("±threshold (0-441)")
        thr_sp.setStyleSheet(
            f"QSpinBox{{background:{T['card']};color:{T['fg']};border:1px solid {T['border2']};"
            f"border-radius:3px;padding:1px 3px;font-size:10px;}}")
        thr_sp._idx = idx
        thr_sp.valueChanged.connect(lambda v, sp=thr_sp: self._rules[sp._idx].__setitem__("threshold", v))
        row_h.addWidget(QLabel("±")); row_h.addWidget(thr_sp)

        coord_lbl = QLabel(f"({self._points[idx][0]},{self._points[idx][1]})")
        coord_lbl.setStyleSheet(f"color:{T['dim2']};font-size:9px;")
        row_h.addWidget(coord_lbl); row_h.addStretch()

        self._pt_layout.insertWidget(self._pt_layout.count() - 1, row_w)

    def _remove_last_point_row(self):
        count = self._pt_layout.count()
        if count >= 2:
            item = self._pt_layout.takeAt(count - 2)
            if item and item.widget():
                w = item.widget()
                # remove checkbox ref
                idx = len(self._points)   # already popped
                self._pt_checkboxes.pop(idx, None)
                w.deleteLater()

    def _selected_point_ids(self) -> list:
        """Return sorted list of checked point indices."""
        return sorted(i for i, cb in self._pt_checkboxes.items() if cb.isChecked())

    def _select_all_points(self, state: bool):
        for cb in self._pt_checkboxes.values():
            cb.setChecked(state)

    # ─────────────────────────────────────────────────────────────────────────
    # Group rows
    # ─────────────────────────────────────────────────────────────────────────

    def _next_group_name(self) -> str:
        n = len(self._groups) + 1
        existing = {g.get("name") for g in self._groups}
        while f"group_{n}" in existing:
            n += 1
        return f"group_{n}"

    def _add_group_from_selection(self):
        """Create a group from checked points and append a row."""
        ids = self._selected_point_ids()
        g = {
            "name":      self._next_group_name(),
            "pixel_ids": ids,
            "color":     [255, 140, 0],
            "threshold": 40,
            "min_match": max(1, len(ids)),
        }
        self._groups.append(g)
        self._add_group_row(g)
        # uncheck all points after grouping
        self._select_all_points(False)

    def _add_group_row(self, g: dict = None):
        if g is None:
            g = {
                "name":      self._next_group_name(),
                "pixel_ids": [],
                "color":     [255, 140, 0],
                "threshold": 40,
                "min_match": 1,
            }
            self._groups.append(g)

        # Use list index safely — don't rely on object identity after copies
        try:
            gidx = self._groups.index(g)
        except ValueError:
            gidx = len(self._groups) - 1

        row_w = QWidget()
        row_w.setStyleSheet(f"QWidget{{background:{T['card']};border-radius:4px;}}")
        vlay  = QVBoxLayout(row_w); vlay.setContentsMargins(6,4,6,4); vlay.setSpacing(4)

        # Row 1: name + delete
        r1 = QHBoxLayout(); r1.setSpacing(6)
        name_lbl = QLabel("Name:")
        name_lbl.setStyleSheet(f"color:{T['fg2']};font-size:10px;")
        r1.addWidget(name_lbl)
        name_ed = QLineEdit(g.get("name", ""))
        name_ed.setFixedWidth(100)
        name_ed.setStyleSheet(
            f"QLineEdit{{background:{T['bg']};color:{T['fg']};border:1px solid {T['border2']};"
            f"border-radius:3px;padding:1px 4px;font-size:11px;}}")
        name_ed.setToolTip("Use in script as:  btn.<name>.color_scan(...)")
        name_ed.textChanged.connect(lambda v, _g=g: _g.update({"name": v}))
        r1.addWidget(name_ed)

        api_lbl = QLabel("→ btn.<name>.color_scan()")
        api_lbl.setStyleSheet(f"color:{T['dim']};font-size:9px;font-family:monospace;")
        r1.addWidget(api_lbl)

        del_btn = QPushButton("✕"); del_btn.setFixedSize(20, 20)
        del_btn.setStyleSheet(
            f"QPushButton{{background:{T['red2']};color:white;border:none;"
            f"border-radius:3px;font-size:10px;}}")
        del_btn.clicked.connect(lambda _, rw=row_w, _g=g: self._delete_group_row(rw, _g))
        r1.addStretch(); r1.addWidget(del_btn)
        vlay.addLayout(r1)

        # Row 2: pixel IDs (comma-separated, pre-filled from selection)
        r2 = QHBoxLayout(); r2.setSpacing(6)
        ids_lbl = QLabel("Points:"); ids_lbl.setStyleSheet(f"color:{T['fg2']};font-size:10px;")
        r2.addWidget(ids_lbl)
        ids_ed = QLineEdit(",".join(str(i) for i in g.get("pixel_ids", [])))
        ids_ed.setPlaceholderText("e.g. 0,1,2,5  or tick checkboxes above")
        ids_ed.setStyleSheet(name_ed.styleSheet())
        def _ids_changed(v, _g=g):
            try:
                _g["pixel_ids"] = [int(x.strip()) for x in v.split(",") if x.strip().isdigit()]
            except ValueError: pass
        ids_ed.textChanged.connect(_ids_changed)
        r2.addWidget(ids_ed, 1)
        vlay.addLayout(r2)

        # Row 3: color + threshold + min_match
        r3 = QHBoxLayout(); r3.setSpacing(5)
        rc, gc, bc = g.get("color", [255, 140, 0])

        gswatch = QPushButton()
        gswatch.setFixedSize(30, 20)
        gswatch.setStyleSheet(
            f"background:rgb({rc},{gc},{bc});"
            f"border:1px solid {T['border2']};border-radius:3px;")
        gswatch.setToolTip("Click: color wheel   Right-click: pick from screen")
        gswatch._color = [rc, gc, bc]; gswatch._g = g

        hex_g = QLabel(f"#{rc:02X}{gc:02X}{bc:02X}")
        hex_g.setStyleSheet(f"color:{T['fg2']};font-size:9px;font-family:monospace;min-width:52px;")

        def _gswatch_press(pos, sw=gswatch, hl=hex_g):
            if pos.button() == Qt.RightButton:
                picker = ScreenColorPicker(self)
                if picker.exec() == QDialog.Accepted:
                    r2v, g2v, b2v = picker.picked_color
                    sw._color = [r2v, g2v, b2v]
                    sw.setStyleSheet(
                        f"background:rgb({r2v},{g2v},{b2v});"
                        f"border:1px solid {T['border2']};border-radius:3px;")
                    sw._g["color"] = [r2v, g2v, b2v]
                    hl.setText(f"#{r2v:02X}{g2v:02X}{b2v:02X}")
            else:
                chosen = QColorDialog.getColor(QColor(*sw._color), self, "Group target color")
                if chosen.isValid():
                    sw._color = [chosen.red(), chosen.green(), chosen.blue()]
                    sw.setStyleSheet(
                        f"background:rgb({chosen.red()},{chosen.green()},{chosen.blue()});"
                        f"border:1px solid {T['border2']};border-radius:3px;")
                    sw._g["color"] = sw._color[:]
                    hl.setText(f"#{chosen.red():02X}{chosen.green():02X}{chosen.blue():02X}")

        gswatch.mousePressEvent = _gswatch_press

        thr_g = QSpinBox(); thr_g.setRange(0, 441); thr_g.setValue(g.get("threshold", 40))
        thr_g.setFixedWidth(52); thr_g.setToolTip("±threshold (color distance 0-441)")
        thr_g.setStyleSheet(
            f"QSpinBox{{background:{T['bg']};color:{T['fg']};border:1px solid {T['border2']};"
            f"border-radius:3px;padding:1px 3px;font-size:10px;}}")
        thr_g.valueChanged.connect(lambda v, _g=g: _g.update({"threshold": v}))

        mm_g = QSpinBox(); mm_g.setRange(1, 999); mm_g.setValue(g.get("min_match", 1))
        mm_g.setFixedWidth(44); mm_g.setToolTip("Minimum points that must match")
        mm_g.setStyleSheet(thr_g.styleSheet())
        mm_g.valueChanged.connect(lambda v, _g=g: _g.update({"min_match": v}))

        r3.addWidget(QLabel("Color:")); r3.addWidget(gswatch); r3.addWidget(hex_g)
        r3.addWidget(QLabel("±"));     r3.addWidget(thr_g)
        r3.addWidget(QLabel("min:"));  r3.addWidget(mm_g)
        r3.addStretch()
        vlay.addLayout(r3)

        self._grp_layout.insertWidget(self._grp_layout.count() - 1, row_w)

    def _delete_group_row(self, row_w: QWidget, g: dict):
        if g in self._groups: self._groups.remove(g)
        row_w.deleteLater()

    # ─────────────────────────────────────────────────────────────────────────
    # Output
    # ─────────────────────────────────────────────────────────────────────────

    def get_data(self) -> dict:
        return {
            "points": [list(p) for p in self._points],
            "rules":  [dict(r) for r in self._rules],
            "groups": [dict(g) for g in self._groups],
        }


# ═══════════════════════════════════════════════════════════════════════════════
#  Button Definition Dialog
# ═══════════════════════════════════════════════════════════════════════════════

class ButtonDefDialog(QWidget):
    """
    Non-modal button config window.
    Never uses exec() — uses signals instead so RegionSelector works freely.
    Connect saved(dict) to receive the config when user clicks Save.
    Connect cancelled() to handle dismissal.
    """
    saved     = Signal(dict)   # emits the complete button definition
    cancelled = Signal()

    def __init__(self, existing=None, all_names=None, parent=None):
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("Configure Button / Region")
        self.setMinimumWidth(560)
        self.setStyleSheet(f"background:{T['bg']};color:{T['fg']};")
        self._data = copy.deepcopy(existing or DEFAULT_BTN)
        self._all_names = all_names or []
        self._reg_overlay = None
        self._captured_img = None   # PIL image captured right after region selection
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        # ── Name & type ──
        top = QHBoxLayout()
        top.addWidget(QLabel("Name (unique):"))
        self._name_edit = QLineEdit(self._data.get("name", ""))
        top.addWidget(self._name_edit)
        top.addSpacing(12)
        top.addWidget(QLabel("Type:"))
        self._type_combo = QComboBox()
        self._type_combo.addItems(["button", "region"])
        self._type_combo.setCurrentText(self._data.get("btn_type", "button"))
        top.addWidget(self._type_combo)
        lay.addLayout(top)

        # ── Match mode ──
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Match logic:"))
        self._match_combo = QComboBox()
        self._match_combo.addItems(["any1 (≥1 true)", "any2 (≥2 true)", "all (all true)"])
        modes = {"any1": 0, "any2": 1, "all": 2}
        self._match_combo.setCurrentIndex(modes.get(self._data.get("match_mode", "any1"), 0))
        mode_row.addWidget(self._match_combo); mode_row.addStretch()
        lay.addLayout(mode_row)

        tabs = QTabWidget()
        lay.addWidget(tabs)

        # ── OCR tab ──
        ocr_w = QWidget(); ocr_lay = QVBoxLayout(ocr_w); ocr_lay.setSpacing(8)
        self._cb_ocr = QCheckBox("Enable OCR Detection")
        self._cb_ocr.setChecked(self._data.get("ocr_enabled", True))
        ocr_lay.addWidget(self._cb_ocr)

        ocr_lay.addWidget(QLabel("Text to find (comma-separated):"))
        self._ocr_text = QLineEdit(self._data.get("ocr_text", ""))
        self._ocr_text.setPlaceholderText("e.g.  Confirm,confirm,YES")
        self._ocr_text.setMinimumWidth(300)
        self._ocr_text.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        ocr_lay.addWidget(self._ocr_text)

        r2 = QHBoxLayout()
        r2.addWidget(QLabel("PSM mode:")); self._ocr_psm = QSpinBox()
        self._ocr_psm.setRange(0, 13); self._ocr_psm.setValue(self._data.get("ocr_psm", 6))
        r2.addWidget(self._ocr_psm); r2.addSpacing(12)
        r2.addWidget(QLabel("Min confidence:")); self._ocr_conf = QSpinBox()
        self._ocr_conf.setRange(0, 100); self._ocr_conf.setValue(self._data.get("ocr_min_conf", 18))
        r2.addWidget(self._ocr_conf); r2.addStretch(); ocr_lay.addLayout(r2)

        # ── OCR passes ──────────────────────────────────────────────────────────
        ocr_lay.addWidget(_section_lbl_small("OCR PASSES"))
        passes_hint = QLabel(
            "Each pass preprocesses the image differently before running Tesseract. "
            "Match found on any pass = \u2705.")
        passes_hint.setStyleSheet(f"color:{T['fg2']};font-size:10px;")
        passes_hint.setWordWrap(True)
        ocr_lay.addWidget(passes_hint)

        # Number of passes spinner
        np_row = QHBoxLayout()
        np_row.addWidget(QLabel("Number of passes (1–4):"))
        self._ocr_npass = QSpinBox()
        self._ocr_npass.setRange(1, 4)   # minimum 1 — always show config controls
        saved_passes = self._data.get("ocr_passes", [])
        # If no passes saved yet, start with 1 so selected-invert controls are visible
        self._ocr_npass.setValue(max(1, len(saved_passes)))
        np_row.addWidget(self._ocr_npass); np_row.addStretch()
        ocr_lay.addLayout(np_row)

        # Container that holds per-pass rows, rebuilt when spinner changes
        self._passes_container = QWidget()
        self._passes_layout = QVBoxLayout(self._passes_container)
        self._passes_layout.setSpacing(4)
        self._passes_layout.setContentsMargins(0, 0, 0, 0)
        ocr_lay.addWidget(self._passes_container)

        # Per-pass row widgets stored here:
        # list of (channel_combo, thresh_spin, color_btn, radius_spin)
        # color_btn and radius_spin are None for non-selected-invert passes.
        self._pass_rows: list = []

        def _make_color_btn(color_list):
            """Return a QPushButton swatch for the given [R,G,B] list."""
            r, g, b = color_list
            btn = QPushButton()
            btn.setFixedSize(28, 22)
            btn.setToolTip("Click to pick target colour")
            btn.setStyleSheet(
                f"background:rgb({r},{g},{b});border:1px solid #444;border-radius:3px;"
            )
            btn._color = color_list[:]
            return btn

        def _rebuild_passes(n):
            # Clear existing rows
            while self._passes_layout.count():
                item = self._passes_layout.takeAt(0)
                if item.widget(): item.widget().deleteLater()
            self._pass_rows = []
            saved = self._data.get("ocr_passes", [])
            for i in range(n):
                sv = saved[i] if i < len(saved) else {}

                row_w = QWidget()
                row_h = QHBoxLayout(row_w)
                row_h.setContentsMargins(0, 0, 0, 0); row_h.setSpacing(6)
                lbl = QLabel(f"Pass {i+1}:")
                lbl.setFixedWidth(44)
                lbl.setStyleSheet(f"color:{T['fg2']};font-size:11px;")
                row_h.addWidget(lbl)

                ch_combo = QComboBox()
                for ch in OCR_CHANNELS:
                    ch_combo.addItem(ch)
                saved_ch = sv.get("channel", "selected-invert")
                if saved_ch in OCR_CHANNELS:
                    ch_combo.setCurrentText(saved_ch)
                row_h.addWidget(ch_combo)

                thr_lbl_w = QLabel("Thr:")
                row_h.addWidget(thr_lbl_w)
                th_spin = QSpinBox()
                th_spin.setRange(0, 255)
                th_spin.setToolTip(
                    "Threshold (0=no binarise).\n"
                    "For white-extract: cutoff for 'how white is white' (default 200).\n"
                    "For selected-invert: not used (colour radius controls sensitivity)."
                )
                saved_th = sv.get("threshold", 155)
                th_spin.setValue(saved_th)
                th_spin.setFixedWidth(52)
                row_h.addWidget(th_spin)

                # ── selected-invert extras: colour swatch + radius ────────────
                saved_sc  = sv.get("sel_color",  [255, 255, 255])
                saved_rad = sv.get("sel_radius", 60)

                color_btn  = _make_color_btn(saved_sc)
                radius_lbl = QLabel("Rad:")
                radius_lbl.setStyleSheet(f"color:{T['fg2']};font-size:11px;")
                radius_spin = QSpinBox()
                radius_spin.setRange(1, 442)   # sqrt(3)*255 ≈ 441
                radius_spin.setValue(saved_rad)
                radius_spin.setFixedWidth(52)
                radius_spin.setToolTip(
                    "Colour radius (Euclidean RGB distance).\n"
                    "Lower = stricter match.  60 is a good start."
                )

                row_h.addWidget(color_btn)
                row_h.addWidget(radius_lbl)
                row_h.addWidget(radius_spin)
                row_h.addStretch()

                # Wire up colour picker
                def _pick(checked=False, btn=color_btn):
                    r2, g2, b2 = btn._color
                    init = QColor(r2, g2, b2)
                    chosen = QColorDialog.getColor(init, None, "Pick target colour")
                    if chosen.isValid():
                        btn._color = [chosen.red(), chosen.green(), chosen.blue()]
                        btn.setStyleSheet(
                            f"background:rgb({chosen.red()},{chosen.green()},{chosen.blue()});"
                            f"border:1px solid #444;border-radius:3px;"
                        )
                color_btn.clicked.connect(_pick)

                # Show/hide controls depending on channel:
                # selected-invert → show colour+radius, hide threshold
                # white-extract   → show threshold (controls whiteness cutoff), hide colour+radius
                # everything else → show threshold, hide colour+radius
                def _update_vis(ch_text, cbtn=color_btn, rlbl=radius_lbl, rspin=radius_spin,
                                thr_lbl=thr_lbl_w, thr_w=th_spin):
                    is_sel = ch_text == "selected-invert"
                    cbtn.setVisible(is_sel); rlbl.setVisible(is_sel); rspin.setVisible(is_sel)
                    thr_lbl.setVisible(not is_sel); thr_w.setVisible(not is_sel)

                _update_vis(ch_combo.currentText())
                ch_combo.currentTextChanged.connect(_update_vis)

                self._passes_layout.addWidget(row_w)
                self._pass_rows.append((ch_combo, th_spin, color_btn, radius_spin))

        _rebuild_passes(self._ocr_npass.value())
        self._ocr_npass.valueChanged.connect(_rebuild_passes)

        ocr_lay.addStretch()
        tabs.addTab(ocr_w, "OCR")

        # ── Pixel tab ──
        pix_w = QWidget(); pix_lay = QVBoxLayout(pix_w); pix_lay.setSpacing(8)
        self._cb_pix = QCheckBox("Enable Pixel Match Detection")
        self._cb_pix.setChecked(self._data.get("pixel_enabled", False))
        pix_lay.addWidget(self._cb_pix)

        r3 = QHBoxLayout()
        r3.addWidget(QLabel("Color threshold:"))
        self._pix_thresh = QSpinBox(); self._pix_thresh.setRange(0, 255)
        self._pix_thresh.setValue(self._data.get("pixel_threshold", 35))
        r3.addWidget(self._pix_thresh); r3.addStretch(); pix_lay.addLayout(r3)

        self._pix_info = QLabel("No pixels sampled yet.")
        self._pix_info.setStyleSheet(f"color:{T['fg2']};font-size:11px;")
        if self._data.get("pixel_data"):
            n = len(self._data["pixel_data"].get("points", []))
            req = self._data["pixel_data"].get("count_required", "?")
            self._pix_info.setText(f"{n} pixels sampled, {req} required to match.")
        pix_lay.addWidget(self._pix_info)

        btn_pick = QPushButton("🎯 Pick Pixels from Region")
        btn_pick.setStyleSheet(self._btn_style(T['accent2']))
        btn_pick.clicked.connect(self._pick_pixels)
        pix_lay.addWidget(btn_pick)

        self._cb_pix_vis = QCheckBox("Show pixel positions overlay")
        self._cb_pix_vis.setChecked(self._data.get("pixel_pos_visible", False))
        pix_lay.addWidget(self._cb_pix_vis)
        pix_lay.addStretch()
        tabs.addTab(pix_w, "Pixel")

        # ── Color tab ─────────────────────────────────────────────────────
        col_w = QWidget(); col_lay = QVBoxLayout(col_w); col_lay.setSpacing(8)

        self._cb_col = QCheckBox("Enable Color Match Detection")
        self._cb_col.setChecked(self._data.get("color_enabled", False))
        self._cb_col.setStyleSheet(
            f"QCheckBox{{color:{T['fg']};font-size:11px;spacing:6px;}}"
            f"QCheckBox::indicator{{width:14px;height:14px;border-radius:3px;"
            f"border:1px solid {T['border2']};background:{T['card']};}}"
            f"QCheckBox::indicator:checked{{background:{T['accent']};border-color:{T['accent']};}}")
        col_lay.addWidget(self._cb_col)

        self._col_info = QLabel("No color points defined.")
        self._col_info.setStyleSheet(f"color:{T['fg2']};font-size:11px;")
        cdata = self._data.get("color_data")
        if cdata:
            npts  = len(cdata.get("points", []))
            ngrps = len(cdata.get("groups", []))
            self._col_info.setText(
                f"{npts} point(s)  ·  {ngrps} group(s) defined.")
        col_lay.addWidget(self._col_info)

        # Script-API quick-reference card
        ref = QLabel(
            "Script API\n"
            "──────────────────────────────────────\n"
            "btn.color.scan()\n"
            "   → runs all groups; returns dict\n"
            "   if btn.color.scan()[\"is_orange\"]: ...\n\n"
            "btn.color.scan(\n"
            "   pixel_ids=[0,1,2], color=\"#FF8C00\",\n"
            "   threshold=40)\n\n"
            "btn.color.scan(\n"
            "   color=\"#FFFFFF\", threshold=40,\n"
            "   min_match=10)  # all points vs white"
        )
        ref.setStyleSheet(
            f"color:{T['fg2']};font-size:9px;font-family:monospace;"
            f"background:{T['card']};padding:8px;border-radius:4px;"
            f"border:1px solid {T['border2']};")
        col_lay.addWidget(ref)

        btn_col_pick = QPushButton("🎨 Pick Color Points from Region")
        btn_col_pick.setStyleSheet(
            f"QPushButton{{background:{T['accent2']};color:white;border:none;"
            f"border-radius:5px;padding:6px 12px;font-size:11px;font-weight:bold;}}"
            f"QPushButton:hover{{background:{T['accent']};}}")
        btn_col_pick.clicked.connect(self._pick_color_points)
        col_lay.addWidget(btn_col_pick)

        col_lay.addStretch()
        tabs.addTab(col_w, "Color")

        # ── Image match tab ──
        img_w = QWidget(); img_lay = QVBoxLayout(img_w); img_lay.setSpacing(8)
        self._cb_img = QCheckBox("Enable Image Match Detection")
        self._cb_img.setChecked(self._data.get("image_enabled", False))
        img_lay.addWidget(self._cb_img)

        r4 = QHBoxLayout()
        r4.addWidget(QLabel("Match threshold (0-1):"))
        self._img_thresh = QDoubleSpinBox(); self._img_thresh.setRange(0.1, 1.0)
        self._img_thresh.setSingleStep(0.05); self._img_thresh.setDecimals(2)
        self._img_thresh.setValue(self._data.get("image_threshold", 0.8))
        r4.addWidget(self._img_thresh); r4.addStretch(); img_lay.addLayout(r4)

        self._cb_img_same = QCheckBox("Same region as OCR region")
        self._cb_img_same.setChecked(self._data.get("image_same_as_ocr", True))
        img_lay.addWidget(self._cb_img_same)

        self._img_info = QLabel("No template captured yet.")
        self._img_info.setStyleSheet(f"color:{T['fg2']};font-size:11px;")
        if self._data.get("image_template"):
            self._img_info.setText("✅ Template image captured.")
        img_lay.addWidget(self._img_info)

        btn_cap = QPushButton("📷 Capture Template from Region")
        btn_cap.setStyleSheet(self._btn_style(T['green2']))
        btn_cap.clicked.connect(self._capture_template)
        img_lay.addWidget(btn_cap)

        # Template thumbnail — shown after capture or restored when editing
        self._template_preview_lbl = QLabel("No template captured yet.")
        self._template_preview_lbl.setAlignment(Qt.AlignCenter)
        self._template_preview_lbl.setStyleSheet(
            f"background:{T['card']};border:1px solid {T['border']};"
            f"border-radius:4px;color:{T['dim']};font-size:10px;"
            f"min-height:60px;max-height:120px;")
        self._template_preview_lbl.setMinimumHeight(60)
        self._template_preview_lbl.setMaximumHeight(120)
        self._template_preview_lbl.setScaledContents(False)
        img_lay.addWidget(self._template_preview_lbl)

        # If editing an existing button with a saved template, show it now
        if self._data.get("image_template"):
            self._load_template_preview()

        img_lay.addStretch()
        tabs.addTab(img_w, "Image")

        # ── Highlight tab ──
        hl_w = QWidget(); hl_lay = QVBoxLayout(hl_w); hl_lay.setSpacing(10)

        self._cb_hl = QCheckBox("Enable Highlight Detection")
        self._cb_hl.setChecked(self._data.get("hl_enabled", False))
        self._cb_hl.setToolTip(
            "When enabled, the highlight result participates in the match logic\n"
            "(any1 / any2 / all) alongside OCR, Pixel, and Image.")
        hl_lay.addWidget(self._cb_hl)

        # ── Edge count ──────────────────────────────────────────────────────
        hl_lay.addWidget(_section_lbl_small("EDGES REQUIRED"))
        edge_hint = QLabel(
            "Number of edges (top / bottom / left / right) that must "
            "pass their threshold for the button to count as highlighted.")
        edge_hint.setStyleSheet(f"color:{T['fg2']};font-size:10px;")
        edge_hint.setWordWrap(True)
        hl_lay.addWidget(edge_hint)

        min_row = QHBoxLayout()
        min_row.addWidget(QLabel("Min edges that must pass (1–4):"))
        self._hl_min_edges = QSpinBox()
        self._hl_min_edges.setRange(1, 4)
        self._hl_min_edges.setValue(self._data.get("hl_min_edges", 3))
        self._hl_min_edges.setToolTip(
            "1 = any single edge is enough\n"
            "3 = at least 3 edges must pass\n"
            "4 = all four edges must pass")
        min_row.addWidget(self._hl_min_edges); min_row.addStretch()
        hl_lay.addLayout(min_row)

        # ── Global threshold ─────────────────────────────────────────────────
        hl_lay.addWidget(_section_lbl_small("GLOBAL THRESHOLD"))
        glob_hint = QLabel(
            "Default ratio of black pixels an edge must have to \"pass\". "
            "Used for any edge whose per-edge override is 0.")
        glob_hint.setStyleSheet(f"color:{T['fg2']};font-size:10px;")
        glob_hint.setWordWrap(True)
        hl_lay.addWidget(glob_hint)

        glob_row = QHBoxLayout()
        glob_row.addWidget(QLabel("Global threshold (0.01–1.0):"))
        self._hl_thresh = QDoubleSpinBox()
        self._hl_thresh.setRange(0.01, 1.0)
        self._hl_thresh.setSingleStep(0.05)
        self._hl_thresh.setDecimals(2)
        self._hl_thresh.setValue(self._data.get("hl_threshold", 0.25))
        self._hl_thresh.setToolTip(
            "~0.25 = catches subtle uniform tints\n"
            "~0.55 = stricter, for bright border-only highlights\n"
            "Tip: run the Test button and check the edge ratios in the log.")
        glob_row.addWidget(self._hl_thresh); glob_row.addStretch()
        hl_lay.addLayout(glob_row)

        # ── Per-edge overrides ────────────────────────────────────────────────
        hl_lay.addWidget(_section_lbl_small("PER-EDGE OVERRIDES  (0 = use global)"))
        per_hint = QLabel(
            "Set a non-zero value to override the threshold for that specific "
            "edge only. Leave at 0 to inherit the global threshold above.")
        per_hint.setStyleSheet(f"color:{T['fg2']};font-size:10px;")
        per_hint.setWordWrap(True)
        hl_lay.addWidget(per_hint)

        def _edge_spin(key, default=0.0):
            s = QDoubleSpinBox()
            s.setRange(0.0, 1.0); s.setSingleStep(0.05); s.setDecimals(2)
            s.setValue(self._data.get(key, default))
            s.setFixedWidth(72)
            return s

        # Top row
        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("Top:"))
        self._hl_top_min = _edge_spin("hl_top_min")
        top_row.addWidget(self._hl_top_min)
        top_row.addSpacing(20)
        top_row.addWidget(QLabel("Bottom:"))
        self._hl_bot_min = _edge_spin("hl_bot_min")
        top_row.addWidget(self._hl_bot_min)
        top_row.addStretch()
        hl_lay.addLayout(top_row)

        # Bottom row
        bot_row = QHBoxLayout()
        bot_row.addWidget(QLabel("Left:"))
        self._hl_left_min = _edge_spin("hl_left_min")
        bot_row.addWidget(self._hl_left_min)
        bot_row.addSpacing(20)
        bot_row.addWidget(QLabel("Right:"))
        self._hl_right_min = _edge_spin("hl_right_min")
        bot_row.addWidget(self._hl_right_min)
        bot_row.addStretch()
        hl_lay.addLayout(bot_row)

        # ── Live diagram ─────────────────────────────────────────────────────
        hl_lay.addWidget(_section_lbl_small("EDGE DIAGRAM"))
        self._hl_diagram = QLabel()
        self._hl_diagram.setAlignment(Qt.AlignCenter)
        self._hl_diagram.setStyleSheet(
            f"background:{T['card']};border:1px solid {T['border']};"
            f"border-radius:4px;color:{T['dim']};font-size:10px;min-height:48px;")
        self._hl_diagram.setWordWrap(True)
        hl_lay.addWidget(self._hl_diagram)

        def _update_hl_diagram():
            n = self._hl_min_edges.value()
            g = self._hl_thresh.value()
            t = self._hl_top_min.value()   or g
            b = self._hl_bot_min.value()   or g
            l = self._hl_left_min.value()  or g
            r = self._hl_right_min.value() or g
            self._hl_diagram.setText(
                f"         top ≥ {t:.2f}\n"
                f"left ≥ {l:.2f}  [ button ]  right ≥ {r:.2f}\n"
                f"         bot ≥ {b:.2f}\n"
                f"→ need {n} of 4 edges to pass"
            )

        _update_hl_diagram()
        for w in (self._hl_min_edges, self._hl_thresh,
                  self._hl_top_min, self._hl_bot_min,
                  self._hl_left_min, self._hl_right_min):
            sig = w.valueChanged if hasattr(w, "valueChanged") else None
            if sig: sig.connect(lambda _: _update_hl_diagram())

        hl_lay.addStretch()
        tabs.addTab(hl_w, "Highlight")

        # ── Region assignment ──
        grp = QGroupBox("REGION ASSIGNMENT")
        grp_lay = QVBoxLayout(grp); grp_lay.setSpacing(6)

        self._reg_lbl = QLabel("No region set")
        reg = self._data.get("region")
        if reg: self._reg_lbl.setText(f"📍 {reg[0]},{reg[1]}  {reg[2]}×{reg[3]}")
        self._reg_lbl.setStyleSheet(f"color:{T['fg2']};font-size:11px;")
        grp_lay.addWidget(self._reg_lbl)

        reg_btns = QHBoxLayout()
        b1 = QPushButton("📐 Set Region"); b1.setStyleSheet(self._btn_style(T['accent2']))
        b1.clicked.connect(self._set_region)
        self._btn_toggle_reg = QPushButton("👁 Show")
        self._btn_toggle_reg.setStyleSheet(self._btn_style(T['dim2']))
        self._btn_toggle_reg.clicked.connect(self._toggle_region)
        b3 = QPushButton("✕ Clear"); b3.setStyleSheet(self._btn_style(T['red2']))
        b3.clicked.connect(self._clear_region)
        reg_btns.addWidget(b1); reg_btns.addWidget(self._btn_toggle_reg); reg_btns.addWidget(b3)
        grp_lay.addLayout(reg_btns)

        # Expand / shrink spinboxes
        self._reg_resize_widget = QWidget()
        rr = QHBoxLayout(self._reg_resize_widget)
        rr.setContentsMargins(0,0,0,0); rr.setSpacing(4)
        rr.addWidget(QLabel("X:")); self._sp_rx = QSpinBox()
        self._sp_rx.setRange(-9999,9999); self._sp_rx.setFixedWidth(60); rr.addWidget(self._sp_rx)
        rr.addWidget(QLabel("Y:")); self._sp_ry = QSpinBox()
        self._sp_ry.setRange(-9999,9999); self._sp_ry.setFixedWidth(60); rr.addWidget(self._sp_ry)
        rr.addWidget(QLabel("W:")); self._sp_rw = QSpinBox()
        self._sp_rw.setRange(1,9999); self._sp_rw.setFixedWidth(60); rr.addWidget(self._sp_rw)
        rr.addWidget(QLabel("H:")); self._sp_rh = QSpinBox()
        self._sp_rh.setRange(1,9999); self._sp_rh.setFixedWidth(60); rr.addWidget(self._sp_rh)
        btn_apply_r = QPushButton("Apply")
        btn_apply_r.setFixedHeight(22)
        btn_apply_r.setStyleSheet(self._btn_style(T['accent2']))
        btn_apply_r.clicked.connect(self._apply_reg_resize)
        rr.addWidget(btn_apply_r); rr.addStretch()
        self._reg_resize_widget.setVisible(False)
        grp_lay.addWidget(self._reg_resize_widget)

        # ── Captured screenshot preview ──────────────────────────────────────
        self._preview_lbl = QLabel("No capture yet")
        self._preview_lbl.setAlignment(Qt.AlignCenter)
        self._preview_lbl.setStyleSheet(
            f"background:{T['card']};border:1px solid {T['border']};"
            f"border-radius:4px;color:{T['dim']};font-size:10px;min-height:60px;")
        self._preview_lbl.setFixedHeight(80)
        grp_lay.addWidget(self._preview_lbl)

        # Restore preview if editing an existing button that has a saved snapshot
        if self._data.get("region_snapshot") or self._data.get("image_template"):
            self._load_preview_from_template()

        recap_row = QHBoxLayout()
        self._btn_recap = QPushButton("📸 Re-capture region screenshot")
        self._btn_recap.setStyleSheet(self._btn_style(T['dim2']))
        self._btn_recap.setEnabled(bool(self._data.get("region")))
        self._btn_recap.clicked.connect(self._recapture)
        recap_row.addWidget(self._btn_recap); recap_row.addStretch()
        grp_lay.addLayout(recap_row)

        self._cb_reg_vis = QCheckBox("Show region on screen (persistent)")
        self._cb_reg_vis.setChecked(self._data.get("region_visible", False))
        grp_lay.addWidget(self._cb_reg_vis)
        lay.addWidget(grp)
        # overlay reference
        self._reg_overlay: RegionHighlighter | None = None

        # ── Save / Cancel ──
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        b_cancel = QPushButton("Cancel")
        b_cancel.setFixedHeight(32)
        b_cancel.setStyleSheet(
            f"QPushButton{{background:{T['dim2']};color:{T['fg2']};border:none;"
            f"border-radius:5px;padding:0 20px;font-size:12px;}}"
            f"QPushButton:hover{{background:{T['border2']};color:{T['fg']};}}")
        b_cancel.clicked.connect(self._on_cancel)
        b_save = QPushButton("Save")
        b_save.setFixedHeight(32)
        b_save.setStyleSheet(
            f"QPushButton{{background:{T['accent2']};color:white;border:none;"
            f"border-radius:5px;padding:0 24px;font-size:12px;font-weight:bold;}}"
            f"QPushButton:hover{{background:{T['accent']};color:black;}}")
        b_save.clicked.connect(self._on_accept)
        btn_row.addWidget(b_cancel); btn_row.addWidget(b_save)
        lay.addLayout(btn_row)

    def _btn_style(self, color):
        return (f"QPushButton{{background:{color};color:{T['fg']};border:none;"
                f"border-radius:4px;padding:5px 12px;font-size:11px;}}"
                f"QPushButton:hover{{opacity:0.8;}}")

    def _set_region(self):
        # Use the callback-based RegionSelector — no exec(), no nesting,
        # the dialog stays alive and the main window stays responsive.
        RegionSelector.open(self._on_region)

    def _on_region(self, x, y, w, h):
        self._data["region"] = [x, y, w, h]
        self._reg_lbl.setText(f"📍 {x},{y}  {w}×{h}")
        self._btn_recap.setEnabled(True)
        # Capture IMMEDIATELY while the game is still on screen —
        # the region overlay just closed so the game content is visible.
        # Small delay lets the OS repaint before we grab.
        QTimer.singleShot(80, lambda: self._do_capture(x, y, w, h))
        self.raise_()
        self.activateWindow()

    def _do_capture(self, x, y, w, h):
        """Capture screenshot of the region, store in memory + save to JSON data."""
        img = capture([x, y, w, h])
        if img:
            self._captured_img = img
            self._show_preview(img)
            try:
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                b64 = base64.b64encode(buf.getvalue()).decode()
                # Always save as region_snapshot (independent of image detection toggle)
                self._data["region_snapshot"] = b64
                # Also auto-set image template so image detection works out of the box
                self._data["image_template"] = b64
                self._img_info.setText("✅ Template auto-captured from region.")
            except Exception: pass

    def _show_preview(self, img):
        """Display PIL image as thumbnail in the preview label."""
        if not img: return
        try:
            thumb = img.copy()
            thumb.thumbnail((320, 74), Image.Resampling.LANCZOS)
            thumb = thumb.convert("RGB")
            data = thumb.tobytes("raw", "RGB")
            from PySide6.QtGui import QImage
            qi = QImage(data, thumb.width, thumb.height,
                        thumb.width * 3, QImage.Format_RGB888).copy()
            px = QPixmap.fromImage(qi).scaled(
                self._preview_lbl.width() - 4,
                self._preview_lbl.height() - 4,
                Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self._preview_lbl.setPixmap(px)
            self._preview_lbl.setText("")
        except Exception as e:
            self._preview_lbl.setText(f"Preview error: {e}")

    def _load_preview_from_template(self):
        """
        Restore _captured_img from saved JSON data when editing an existing button.
        Prefers region_snapshot (always saved) over image_template (only when
        image detection is enabled).
        """
        b64 = self._data.get("region_snapshot") or self._data.get("image_template")
        if not b64:
            return
        try:
            img = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
            self._captured_img = img
            self._show_preview(img)
        except Exception: pass

    def _recapture(self):
        """Take a fresh screenshot of the current region and save to JSON data."""
        reg = self._data.get("region")
        if not reg: return
        img = capture(reg)
        if img:
            self._captured_img = img
            self._show_preview(img)
            try:
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                b64 = base64.b64encode(buf.getvalue()).decode()
                self._data["region_snapshot"] = b64
                self._data["image_template"]  = b64
                self._img_info.setText("✅ Template re-captured.")
            except Exception: pass

    def _toggle_region(self):
        if self._reg_overlay and self._reg_overlay.isVisible():
            self._reg_overlay.close()
            self._reg_overlay = None
            self._btn_toggle_reg.setText("👁 Show")
            self._reg_resize_widget.setVisible(False)
            return
        reg = self._data.get("region")
        if not reg:
            return
        self._reg_overlay = RegionHighlighter(*reg, label=self._data.get("name",""),
                                               color=QColor(T['accent']))
        self._reg_overlay.show()
        self._btn_toggle_reg.setText("🙈 Hide")
        self._sp_rx.setValue(reg[0]); self._sp_ry.setValue(reg[1])
        self._sp_rw.setValue(reg[2]); self._sp_rh.setValue(reg[3])
        self._reg_resize_widget.setVisible(True)

    def _apply_reg_resize(self):
        x = self._sp_rx.value(); y = self._sp_ry.value()
        w = self._sp_rw.value(); h = self._sp_rh.value()
        self._data["region"] = [x, y, w, h]
        self._reg_lbl.setText(f"📍 {x},{y}  {w}×{h}")
        if self._reg_overlay and self._reg_overlay.isVisible():
            self._reg_overlay.update_geometry(x, y, w, h)

    def _clear_region(self):
        self._data["region"] = None
        self._reg_lbl.setText("No region set")

    def _pick_pixels(self):
        if not self._data.get("region"):
            QMessageBox.warning(self, "No Region", "Set a region first.")
            return
        # Use the screenshot captured right after region selection.
        # Fall back to live capture only if none stored yet.
        img = self._captured_img
        if img is None:
            img = capture(self._data["region"])
        if not img:
            QMessageBox.warning(self, "Capture Failed", "Could not capture region.")
            return
        dlg = PixelPickerDlg(img, existing=self._data.get("pixel_data"), parent=self)
        _center_on_screen(dlg)
        if dlg.exec() == QDialog.Accepted:
            self._data["pixel_data"] = dlg.get_data()
            n = len(self._data["pixel_data"]["points"])
            req = self._data["pixel_data"]["count_required"]
            self._pix_info.setText(f"{n} pixels sampled, {req} required to match.")

    def _pick_color_points(self):
        if not self._data.get("region"):
            QMessageBox.warning(self, "No Region", "Set a region first.")
            return
        img = self._captured_img
        if img is None:
            img = capture(self._data["region"])
        if not img:
            QMessageBox.warning(self, "Capture Failed", "Could not capture region.")
            return
        dlg = ColorPickerDlg(img, existing=self._data.get("color_data"), parent=self)
        _center_on_screen(dlg)
        if dlg.exec() == QDialog.Accepted:
            self._data["color_data"] = dlg.get_data()
            npts  = len(self._data["color_data"]["points"])
            ngrps = len(self._data["color_data"]["groups"])
            self._col_info.setText(
                f"{npts} point(s)  ·  {ngrps} group(s) defined.")

    def _load_template_preview(self):
        """Render the saved image_template into the Image tab thumbnail label."""
        b64 = self._data.get("image_template")
        if not b64:
            self._template_preview_lbl.setPixmap(QPixmap())
            self._template_preview_lbl.setText("No template captured yet.")
            return
        try:
            img = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
            # Scale to fit the label width while keeping aspect ratio
            max_w = max(self._template_preview_lbl.width() - 4, 200)
            max_h = 116
            thumb = img.copy()
            thumb.thumbnail((max_w, max_h), Image.Resampling.LANCZOS)
            data = thumb.tobytes("raw", "RGB")
            from PySide6.QtGui import QImage
            qi = QImage(data, thumb.width, thumb.height,
                        thumb.width * 3, QImage.Format_RGB888).copy()
            px = QPixmap.fromImage(qi)
            self._template_preview_lbl.setPixmap(px)
            self._template_preview_lbl.setText("")
        except Exception as e:
            self._template_preview_lbl.setText(f"Preview error: {e}")

    def _capture_template(self):
        # Use the stored screenshot if available (captured right after region draw).
        # "Re-capture region screenshot" button updates _captured_img for a fresh shot.
        img = self._captured_img
        if img is None:
            reg = self._data.get("region")
            if not self._data.get("image_same_as_ocr"):
                img_reg = self._data.get("image_region")
                if img_reg: reg = img_reg
            if not reg:
                QMessageBox.warning(self, "No Region", "Set a region first.")
                return
            img = capture(reg)
        if not img:
            QMessageBox.warning(self, "Capture Failed", "Could not capture region.")
            return
        buf = io.BytesIO(); img.save(buf, format="PNG")
        self._data["image_template"] = base64.b64encode(buf.getvalue()).decode()
        self._img_info.setText("✅ Template image captured.")
        self._show_preview(img)
        self._load_template_preview()

    def _on_accept(self):
        name = self._name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Name Required", "Enter a unique button name.")
            return
        # Check uniqueness (excluding self)
        existing_name = self._data.get("name", "")
        if name != existing_name and name in self._all_names:
            QMessageBox.warning(self, "Duplicate Name", f"'{name}' already exists.")
            return

        modes_map = {0: "any1", 1: "any2", 2: "all"}
        # Collect per-pass settings
        ocr_passes = []
        for row_tuple in self._pass_rows:
            ch_combo, th_spin, color_btn, radius_spin = row_tuple
            entry = {
                "channel":   ch_combo.currentText(),
                "threshold": th_spin.value(),
            }
            if ch_combo.currentText() == "selected-invert":
                entry["sel_color"]  = color_btn._color[:]
                entry["sel_radius"] = radius_spin.value()
            ocr_passes.append(entry)
        self._data.update({
            "name":          name,
            "btn_type":      self._type_combo.currentText(),
            "ocr_enabled":   self._cb_ocr.isChecked(),
            "ocr_text":      self._ocr_text.text().strip(),
            "ocr_psm":       self._ocr_psm.value(),
            "ocr_min_conf":  self._ocr_conf.value(),
            "ocr_passes":    ocr_passes,
            "pixel_enabled": self._cb_pix.isChecked(),
            "pixel_threshold": self._pix_thresh.value(),
            "pixel_pos_visible": self._cb_pix_vis.isChecked(),
            "color_enabled": self._cb_col.isChecked(),
            "image_enabled": self._cb_img.isChecked(),
            "image_threshold": self._img_thresh.value(),
            "hl_enabled":    self._cb_hl.isChecked(),
            "hl_threshold":  self._hl_thresh.value(),
            "hl_min_edges":  self._hl_min_edges.value(),
            "hl_top_min":    self._hl_top_min.value(),
            "hl_bot_min":    self._hl_bot_min.value(),
            "hl_left_min":   self._hl_left_min.value(),
            "hl_right_min":  self._hl_right_min.value(),
            "image_same_as_ocr": self._cb_img_same.isChecked(),
            "match_mode":    modes_map[self._match_combo.currentIndex()],
            "region_visible": self._cb_reg_vis.isChecked(),
        })
        if self._reg_overlay:
            try: self._reg_overlay.close()
            except Exception: pass
        self.saved.emit(copy.deepcopy(self._data))
        self.close()

    def _on_cancel(self):
        if self._reg_overlay:
            try: self._reg_overlay.close()
            except Exception: pass
        self.cancelled.emit()
        self.close()

    def get_data(self): return self._data


# ═══════════════════════════════════════════════════════════════════════════════
#  Control Definition Dialog (selector / slider)
# ═══════════════════════════════════════════════════════════════════════════════

class ControlDefDialog(QDialog):
    def __init__(self, existing=None, all_names=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configure Logic Control")
        self.setMinimumWidth(400)
        self.setStyleSheet(f"background:{T['bg']};color:{T['fg']};")
        self._data = copy.deepcopy(existing or DEFAULT_CTRL)
        self._all_names = all_names or []
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)

        row = QHBoxLayout()
        row.addWidget(QLabel("Name:"))
        self._name = QLineEdit(self._data.get("name", ""))
        row.addWidget(self._name)
        row.addSpacing(12)
        row.addWidget(QLabel("Type:"))
        self._type_combo = QComboBox()
        self._type_combo.addItems(["selector", "slider"])
        self._type_combo.setCurrentText(self._data.get("ctrl_type", "selector"))
        self._type_combo.currentTextChanged.connect(self._on_type_change)
        row.addWidget(self._type_combo)
        lay.addLayout(row)

        self._stack = QStackedWidget()

        # Selector page
        sel_w = QWidget(); sel_lay = QVBoxLayout(sel_w)
        sel_lay.addWidget(QLabel("Options (one per line):"))
        self._options_edit = QTextEdit()
        self._options_edit.setFixedHeight(120)
        self._options_edit.setPlainText("\n".join(self._data.get("options", [])))
        sel_lay.addWidget(self._options_edit)
        sel_lay.addWidget(QLabel("Default selection:"))
        self._sel_default = QLineEdit(self._data.get("selected", ""))
        sel_lay.addWidget(self._sel_default)
        self._stack.addWidget(sel_w)

        # Slider page
        sld_w = QWidget(); sld_lay = QFormLayout(sld_w)
        self._sld_min = QSpinBox(); self._sld_min.setRange(-99999, 99999)
        self._sld_min.setValue(self._data.get("min", 0))
        self._sld_max = QSpinBox(); self._sld_max.setRange(-99999, 99999)
        self._sld_max.setValue(self._data.get("max", 100))
        self._sld_val = QSpinBox(); self._sld_val.setRange(-99999, 99999)
        self._sld_val.setValue(self._data.get("value", 0))
        self._sld_step = QSpinBox(); self._sld_step.setRange(1, 9999)
        self._sld_step.setValue(self._data.get("step", 1))
        sld_lay.addRow("Min:", self._sld_min)
        sld_lay.addRow("Max:", self._sld_max)
        sld_lay.addRow("Default value:", self._sld_val)
        sld_lay.addRow("Step:", self._sld_step)
        self._stack.addWidget(sld_w)

        lay.addWidget(self._stack)
        self._on_type_change(self._type_combo.currentText())

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _on_type_change(self, t):
        self._stack.setCurrentIndex(0 if t == "selector" else 1)

    def _on_accept(self):
        name = self._name.text().strip()
        if not name:
            QMessageBox.warning(self, "Name Required", "Enter a name."); return
        existing_name = self._data.get("name", "")
        if name != existing_name and name in self._all_names:
            QMessageBox.warning(self, "Duplicate Name", f"'{name}' already exists."); return
        ctype = self._type_combo.currentText()
        self._data["name"] = name
        self._data["ctrl_type"] = ctype
        if ctype == "selector":
            opts = [l.strip() for l in self._options_edit.toPlainText().splitlines() if l.strip()]
            self._data["options"] = opts
            self._data["selected"] = self._sel_default.text().strip() or (opts[0] if opts else "")
        else:
            self._data["min"] = self._sld_min.value()
            self._data["max"] = self._sld_max.value()
            self._data["value"] = self._sld_val.value()
            self._data["step"] = self._sld_step.value()
        self.accept()

    def get_data(self): return self._data


# ═══════════════════════════════════════════════════════════════════════════════
#  Python Syntax Highlighter
# ═══════════════════════════════════════════════════════════════════════════════

class PythonHighlighter(QSyntaxHighlighter):
    def __init__(self, doc):
        super().__init__(doc)
        self._rules = []
        kw_fmt = QTextCharFormat(); kw_fmt.setForeground(QColor("#569cd6")); kw_fmt.setFontWeight(QFont.Bold)
        for kw in ["if","else","elif","while","for","in","return","import","from","def","class",
                   "True","False","None","and","or","not","break","continue","pass"]:
            self._rules.append((re.compile(rf"\b{kw}\b"), kw_fmt))
        str_fmt = QTextCharFormat(); str_fmt.setForeground(QColor("#ce9178"))
        self._rules.append((re.compile(r'"[^"]*"'), str_fmt))
        self._rules.append((re.compile(r"'[^']*'"), str_fmt))
        cmt_fmt = QTextCharFormat(); cmt_fmt.setForeground(QColor(T['dim']))
        self._rules.append((re.compile(r"#[^\n]*"), cmt_fmt))
        num_fmt = QTextCharFormat(); num_fmt.setForeground(QColor("#b5cea8"))
        self._rules.append((re.compile(r"\b\d+\.?\d*\b"), num_fmt))
        fn_fmt = QTextCharFormat(); fn_fmt.setForeground(QColor(T['yellow']))
        self._rules.append((re.compile(r"\b[a-zA-Z_]\w*(?=\()"), fn_fmt))

    def highlightBlock(self, text):
        for pattern, fmt in self._rules:
            for m in pattern.finditer(text):
                self.setFormat(m.start(), m.end()-m.start(), fmt)


# ═══════════════════════════════════════════════════════════════════════════════
#  Main Window
# ═══════════════════════════════════════════════════════════════════════════════

def _mkbtn(text, color, slot, sz=12, h=32, min_w=None):
    b = QPushButton(text)
    hover = QColor(color).lighter(115).name() if color != T['dim2'] else T['dim']
    press = QColor(color).darker(115).name()
    mw = f"min-width:{min_w}px;" if min_w else ""
    b.setStyleSheet(f"""QPushButton{{background:{color};color:{T['fg']};border:none;
        border-radius:5px;padding:5px 14px;font-size:{sz}px;font-weight:bold;{mw}}}
        QPushButton:hover{{background:{hover};border:1px solid rgba(255,255,255,0.15);}}
        QPushButton:pressed{{background:{press};}}
        QPushButton:disabled{{background:{T['border']};color:{T['dim']};}}""")
    b.setFixedHeight(h)
    if min_w: b.setMinimumWidth(min_w)
    b.clicked.connect(slot)
    return b

def _sep():
    f = QFrame(); f.setFrameShape(QFrame.HLine)
    f.setStyleSheet(f"color:{T['border']};max-height:1px;")
    return f

def _sep_v():
    f = QFrame(); f.setFrameShape(QFrame.VLine)
    f.setFixedWidth(1)
    f.setStyleSheet(f"background:{T['border']};")
    return f

def _section_lbl(t):
    l = QLabel(t)
    l.setStyleSheet(f"color:{T['dim']};font-size:9px;font-weight:bold;letter-spacing:2px;padding:6px 0 2px 0;")
    return l

def _section_lbl_small(t):
    l = QLabel(t)
    l.setStyleSheet(f"color:{T['dim']};font-size:9px;font-weight:bold;letter-spacing:1px;padding:4px 0 1px 0;")
    return l


class PopupPreview(QWidget):
    """
    Floating picture-in-picture style preview window.
    Shows the last captured region (raw + preprocessed side by side).
    Stays on top, draggable, resizable — like a mini video player popup.
    """
    def __init__(self):
        super().__init__(None)
        self.setWindowTitle("Preview")
        self.setWindowFlags(Qt.Tool | Qt.WindowStaysOnTopHint | Qt.WindowTitleHint |
                             Qt.WindowCloseButtonHint | Qt.WindowMinimizeButtonHint)
        self.resize(640, 220)
        self.setStyleSheet(f"background:{T['card']};border:1px solid {T['border2']};")
        lay = QVBoxLayout(self); lay.setContentsMargins(4,4,4,4); lay.setSpacing(2)

        hdr = QHBoxLayout()
        self._title_lbl = QLabel("No capture")
        self._title_lbl.setStyleSheet(f"color:{T['dim']};font-size:9px;font-weight:bold;letter-spacing:1px;")
        hdr.addWidget(self._title_lbl); hdr.addStretch()
        lay.addLayout(hdr)

        img_row = QHBoxLayout(); img_row.setSpacing(3)
        _ss = (f"background:{T['bg']};border:1px solid {T['border']};"
               f"border-radius:3px;color:{T['dim']};font-size:9px;")

        self._lbl_raw = QLabel("raw"); self._lbl_raw.setAlignment(Qt.AlignCenter)
        self._lbl_raw.setStyleSheet(_ss)
        img_row.addWidget(self._lbl_raw, 1)

        pre_col = QVBoxLayout(); pre_col.setSpacing(1); pre_col.setContentsMargins(0,0,0,0)
        self._pre_hdr = QLabel("preprocessed")
        self._pre_hdr.setAlignment(Qt.AlignCenter)
        self._pre_hdr.setFixedHeight(13)
        self._pre_hdr.setStyleSheet(f"color:{T['dim']};font-size:8px;letter-spacing:1px;")
        pre_col.addWidget(self._pre_hdr)
        self._lbl_pre = QLabel(""); self._lbl_pre.setAlignment(Qt.AlignCenter)
        self._lbl_pre.setStyleSheet(_ss)
        pre_col.addWidget(self._lbl_pre, 1)
        pre_w = QWidget(); pre_w.setLayout(pre_col)
        img_row.addWidget(pre_w, 1)
        lay.addLayout(img_row, 1)

    def update_images(self, raw_pil, pre_pil=None, label=""):
        self._title_lbl.setText(label or "Preview")
        self._set_lbl(self._lbl_raw, raw_pil)
        if pre_pil is not None:
            p = pre_pil[0] if isinstance(pre_pil, list) else pre_pil
            self._set_lbl(self._lbl_pre, p.convert("RGB") if p else None)
            self._lbl_pre.show(); self._pre_hdr.show()
        else:
            self._lbl_pre.clear(); self._lbl_pre.hide(); self._pre_hdr.hide()

    def _set_lbl(self, lbl, pil_img):
        if not pil_img or not PIL_OK: return
        try:
            img = pil_img.convert("RGB")
            img.thumbnail((self.width(), self.height()-30), Image.LANCZOS)
            data = img.tobytes("raw","RGB")
            qi = QImage(data, img.width, img.height, img.width*3, QImage.Format_RGB888).copy()
            lbl.setPixmap(QPixmap.fromImage(qi).scaled(
                lbl.width(), lbl.height(),
                Qt.KeepAspectRatio, Qt.SmoothTransformation))
        except Exception: pass


