"""
core/theme.py — UI Color palette, stylesheets, and visual widget helpers
"""

from PySide6.QtWidgets import QApplication, QPushButton, QFrame, QLabel
from PySide6.QtGui import QColor

# ── Color Palette ─────────────────────────────────────────────────────────────
T = {
    "bg": "#08090d", "panel": "#0e1018", "card": "#12151f",
    "border": "#1c2030", "border2": "#252a3a",
    "accent": "#00d4ff", "accent2": "#0099bb",
    "green": "#00e676", "green2": "#00a854",
    "red": "#ff3d5a", "red2": "#aa1a30",
    "yellow": "#ffd740", "orange": "#ff9800",
    "dim": "#4a5168", "dim2": "#2e3348",
    "fg": "#d8dce8", "fg2": "#8892a8",
}

# ── Global Stylesheet ─────────────────────────────────────────────────────────
def _ss() -> str:
    return f"""
    QMainWindow,QWidget,QDialog{{background:{T['bg']};color:{T['fg']};
        font-family:'Segoe UI','Consolas',sans-serif;font-size:12px;}}
    QSplitter::handle{{background:{T['border']};width:1px;height:1px;}}
    QScrollBar:vertical{{background:{T['bg']};width:6px;margin:0;border:none;}}
    QScrollBar::handle:vertical{{background:{T['border2']};border-radius:3px;min-height:24px;}}
    QScrollBar::handle:vertical:hover{{background:{T['dim']};}}
    QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;border:none;}}
    QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{{background:none;}}
    QScrollBar:horizontal{{background:{T['bg']};height:6px;margin:0;border:none;}}
    QScrollBar::handle:horizontal{{background:{T['border2']};border-radius:3px;min-width:24px;}}
    QTextEdit,QPlainTextEdit{{background:#040508;border:none;border-top:1px solid {T['border']};
        color:#6ddc6d;font-family:'Consolas','Courier New',monospace;font-size:11px;padding:8px;}}
    QSpinBox,QDoubleSpinBox{{background:{T['panel']};border:1px solid {T['border2']};
        border-radius:5px;color:{T['fg']};padding:4px 8px;font-size:12px;}}
    QSpinBox:focus,QDoubleSpinBox:focus{{border-color:{T['accent']};}}
    QSpinBox::up-button,QSpinBox::down-button,
    QDoubleSpinBox::up-button,QDoubleSpinBox::down-button{{
        background:{T['border2']};border:none;width:18px;}}
    QLineEdit{{background:{T['panel']};border:1px solid {T['border2']};
        border-radius:5px;color:{T['fg']};padding:4px 8px;font-size:12px;}}
    QLineEdit:focus{{border-color:{T['accent']};}}
    QComboBox{{background:{T['panel']};border:1px solid {T['border2']};
        border-radius:5px;color:{T['fg']};padding:5px 10px;font-size:12px;}}
    QComboBox:focus{{border-color:{T['accent']};}}
    QComboBox::drop-down{{subcontrol-origin:padding;subcontrol-position:top right;
        width:28px;border-left:1px solid {T['border2']};background:{T['border2']};}}
    QComboBox QAbstractItemView{{background:{T['card']};color:{T['fg']};
        border:1px solid {T['border2']};selection-background-color:{T['accent2']};}}
    QTabWidget::pane{{border:1px solid {T['border']};border-top:none;background:{T['panel']};}}
    QTabBar::tab{{background:{T['bg']};color:{T['dim']};padding:8px 16px;
        border:1px solid {T['border']};border-bottom:none;font-size:11px;
        font-weight:bold;letter-spacing:1px;margin-right:1px;min-width:60px;}}
    QTabBar::tab:selected{{background:{T['panel']};color:{T['accent']};
        border-bottom:2px solid {T['accent']};margin-bottom:-1px;}}
    QTabBar::tab:hover:!selected{{background:{T['card']};color:{T['fg2']};}}
    QListWidget{{background:{T['panel']};border:1px solid {T['border']};
        border-radius:5px;color:{T['fg']};padding:2px;outline:none;}}
    QListWidget::item{{padding:5px 8px;border-radius:3px;}}
    QListWidget::item:selected{{background:{T['accent2']};color:white;}}
    QListWidget::item:hover:!selected{{background:{T['border2']};}}
    QGroupBox{{border:1px solid {T['border']};border-radius:6px;margin-top:16px;
        padding-top:10px;color:{T['dim']};font-size:10px;font-weight:bold;letter-spacing:2px;}}
    QGroupBox::title{{subcontrol-origin:margin;left:12px;padding:0 5px;}}
    QCheckBox{{color:{T['fg']};spacing:8px;font-size:12px;}}
    QCheckBox::indicator{{width:15px;height:15px;border:1px solid {T['border2']};
        border-radius:4px;background:{T['panel']};}}
    QCheckBox::indicator:hover{{border-color:{T['accent']};}}
    QCheckBox::indicator:checked{{background:{T['accent']};border-color:{T['accent']};}}
    QRadioButton{{color:{T['fg']};spacing:8px;font-size:12px;}}
    QRadioButton::indicator{{width:16px;height:16px;border-radius:8px;
        border:2px solid {T['dim']};background:{T['panel']};}}
    QRadioButton::indicator:checked{{border-color:{T['accent']};background:{T['accent']};}}
    QSlider::groove:horizontal{{height:4px;background:{T['border2']};border-radius:2px;}}
    QSlider::handle:horizontal{{width:14px;height:14px;margin:-5px 0;
        background:{T['accent']};border-radius:7px;}}
    QSlider::sub-page:horizontal{{background:{T['accent2']};border-radius:2px;}}
    QToolTip{{background:{T['card']};color:{T['fg']};border:1px solid {T['border2']};
        border-radius:4px;padding:5px 8px;font-size:11px;}}
    QMessageBox QPushButton,QDialogButtonBox QPushButton{{
        background:{T['card']};color:{T['fg']};border:1px solid {T['border2']};
        border-radius:5px;padding:6px 20px;min-width:80px;font-size:12px;}}
    QMessageBox QPushButton:hover,QDialogButtonBox QPushButton:hover{{
        background:{T['border2']};border-color:{T['accent']};color:{T['accent']};}}
    """

# ── Geometry & Layout Helpers ─────────────────────────────────────────────────
def _center_on_screen(widget):
    """Move widget to the centre of the primary monitor, regardless of parent position."""
    screen_geo = QApplication.primaryScreen().availableGeometry()
    widget.adjustSize()
    w = widget.width()  if widget.width()  > 10 else widget.minimumWidth()
    h = widget.height() if widget.height() > 10 else widget.minimumSizeHint().height()
    x = screen_geo.x() + (screen_geo.width()  - w) // 2
    y = screen_geo.y() + (screen_geo.height() - h) // 2
    widget.move(x, y)

def _vdesk() -> tuple[int, int, int, int]:
    """Return the bounding rect of the full virtual desktop (all monitors combined)
    as (x, y, width, height). Used by RegionSelector to cover every screen."""
    try:
        screens = QApplication.screens()
        if not screens:
            raise RuntimeError("no screens")
        left   = min(s.geometry().left()   for s in screens)
        top    = min(s.geometry().top()    for s in screens)
        right  = max(s.geometry().right()  for s in screens)
        bottom = max(s.geometry().bottom() for s in screens)
        return left, top, right - left + 1, bottom - top + 1
    except Exception:
        g = QApplication.primaryScreen().geometry()
        return g.x(), g.y(), g.width(), g.height()

# ── Widget Creation Helpers ───────────────────────────────────────────────────
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
