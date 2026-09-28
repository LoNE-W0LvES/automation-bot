# ⚡ Automation Bot (Driver Mode)

> **High-Performance Desktop Automation, Computer Vision & Scripting Workstation**

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![GUI](https://img.shields.io/badge/GUI-PySide6-41CD52.svg)](https://wiki.qt.io/Qt_for_Python)
[![Vision](https://img.shields.io/badge/Vision-OpenCV%20%7C%20Tesseract-orange.svg)](https://opencv.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows-0078D6.svg)](https://microsoft.com/windows)
[![Driver Mode](https://img.shields.io/badge/Driver-Interception%20Kernel-red.svg)](https://github.com/oblitum/Interception)

---

## 📖 Overview

**Automation Bot** is an advanced, multi-threaded automation suite designed for high-speed input automation, computer vision detection, and dynamic Python scripting. Built on **PySide6**, it offers a responsive dark-themed GUI workstation capable of hardware-level input dispatch, sub-millisecond timer resolution, OpenCV template matching, and live multi-monitor region configuration.

Whether automating repetitive gaming mechanics, building vision-guided macro bots, or running multi-threaded procedural scripts, Automation Bot provides low-level control with high-level ease of use.

---

## ✨ Key Features

### 🎮 Hardware-Level & Low-Level Input (`keys.py`)
- **Interception Kernel Driver**: True hardware-level keyboard and mouse simulation that operates below the OS hook layer, bypassing standard software detection.
- **Win32 API Direct Calls**: Low-overhead `SendInput` and `mouse_event` fallback for ultra-fast, zero-dependency native execution.
- **Dynamic Backend Switching**: Switch between Driver, Win32, and Software (`keyboard`/`pyautogui`) modes on the fly.
- **Humanized Cursor Trajectories**: Cosine-eased Bezier-like mouse motion with realistic acceleration, micro-jitter, and hardware jiggles.

### 👁️ Computer Vision & OCR (`core/vision.py`)
- **OpenCV Template Matching**: Multi-scale template matching using normalized cross-correlation (`cv2.TM_CCOEFF_NORMED`) with sub-millisecond execution.
- **High-FPS Screen Capture**: Thread-local `mss` multi-monitor screen grabber eliminating context-switching overhead.
- **Tesseract OCR Integration**: Real-time on-screen text recognition and bounding box extraction.
- **Multi-Point Color & Pixel Checks**: Fast RGB tolerance checking and multi-point pixel validation.

### ⚙️ Multi-Threaded Scripting Engine (`core/engine.py`)
- **Concurrent Python Scripts**: Run multiple automation scripts simultaneously in isolated worker threads.
- **Intuitive Proxy API**: Simplified scripting surface via `buttons.scan()`, `buttons.click()`, `controls.get()`, `key_tap()`, `sleep()`, and more.
- **1ms Timer Resolution**: Automatically engages Windows multimedia high-resolution timer (`timeBeginPeriod(1)`) to ensure sub-50ms sleeps remain accurate.

### 🎯 Interactive Visual Calibration (`core/dialogs.py`)
- **Frameless Multi-Monitor Overlays**: Interactive region selector supporting multi-monitor virtual desktops.
- **Live Visual Highlighters**: Real-time visual feedback boxes highlighting detected buttons, matching zones, and active coordinates.
- **Button Definition Modals**: Inspect matched templates, set threshold sliders, configure click offsets, and preview captures in real time.

### 📼 Macro Recorder & Playback (`core/macro.py`)
- **High-Frequency Macro Capture**: Record precise mouse movements, button clicks, and keystrokes.
- **Smooth Playback Interpolation**: Replay macros with speed scaling, repeat counts, and smoothed motion trajectories.
- **JSON Serialization**: Import and export macros directly into application profiles.

### 🛡️ Atomic Reliability & Crash Protection
- **Atomic Configuration Writes**: Configuration saves write to a temporary file before renaming (`.tmp` + `os.replace`), preventing corruption even during abrupt termination.
- **Graceful Thread Teardown**: Guaranteed release of held keys, macro workers, and kernel driver contexts on application exit.

---

## 📂 Project Architecture

The codebase is organized into modular components designed for maintainability and reusability:

```
robloxx/
├── Automation_bot.py          # PySide6 GUI Application & Entry Point
├── keys.py                    # Hardware & OS Input Dispatcher (Interception / Win32 / Fallback)
├── core/                      # Modular Subsystems
│   ├── __init__.py            # Reusable package exports
│   ├── models.py              # Data models & default button/control schemas
│   ├── theme.py               # Dark UI styling palette, stylesheets & component factories
│   ├── vision.py              # OpenCV matching, OCR, color checks & screen capture
│   ├── macro.py               # Macro recording, smoothing & serialization
│   ├── engine.py              # Multithreaded scripting execution engine & proxies
│   └── dialogs.py             # Multi-monitor region selectors & configuration modals
├── configs/                   # Saved profiles & active profile pointer (default.cfg)
│   ├── Split_Steal.json       # Game configuration profile (regions, buttons, scripts)
│   └── default.cfg            # Active profile pointer
├── automation_bot_config.json # Base fallback application configuration
├── requirements.txt           # Dependency manifest
└── README.md                  # Project documentation
```

---

## 🚀 Getting Started

### 1. Prerequisites

- **Operating System**: Windows 10 or Windows 11 (64-bit)
- **Python**: Python 3.10, 3.11, 3.12, 3.13, or 3.14

#### Optional (For Hardware Driver Mode):
To use the `interception` driver backend:
1. Download the [Interception Driver installer](https://github.com/oblitum/Interception/releases).
2. Run `install-interception.exe /install` in an Administrator command prompt.
3. Reboot your system.

#### Optional (For OCR Text Recognition):
1. Download and install [Tesseract-OCR for Windows](https://github.com/UB-Mannheim/tesseract/wiki).
2. Default path expected: `C:\Program Files\Tesseract-OCR\tesseract.exe` (or add it to your system `PATH`).

---

### 2. Installation

1. Clone or download this repository:
   ```bash
   git clone https://github.com/LoNE-W0LvES/automation-bot.git
   cd automation-bot
   ```

2. Create and activate a Python virtual environment:
   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   ```

3. Install required packages:
   ```bash
   pip install -r requirements.txt
   ```

---

### 3. Launching Automation Bot

Run the main application:
```bash
python Automation_bot.py
```
*(Tip: Run your terminal as Administrator if using the Interception driver backend).*

---

## 💻 Scripting API Reference

Scripts in Automation Bot are written in Python and executed inside the integrated `ScriptEngine`. The following objects and functions are injected directly into the script scope:

### Vision & Button Proxies
```python
# Scan all defined buttons in the active profile against the current screen
buttons.scan()

# Check if a specific button was detected
if buttons.confirm_btn.found:
    print(f"Found button at ({buttons.confirm_btn.x}, {buttons.confirm_btn.y})")
    buttons.confirm_btn.click()  # Clicks the button's matched center
```

### Controls & Toggles
```python
# Access UI controls defined in the configuration
if controls.auto_accept.value:
    buttons.accept.click()
```

### High-Precision Delays & Timing
```python
# High-precision sleep (Windows 1ms multimedia timer backed)
sleep(0.05)   # Sleeps 50ms without OS drift
```

### Input Simulation
```python
# Mouse controls
mouse_click("left")
mouse_move(500, 400)
mouse_human(800, 600, duration=0.2)  # Humanized cosine movement

# Keyboard controls
key_tap("e")
key_combo("ctrl", "shift", "esc")
```

---

## ⚙️ Configuration Files

- Profiles are stored under the [`configs/`](file:///C:/Users/nafit/PycharmProjects/robloxx/configs/) directory in standard JSON format.
- Button definitions store base64-encoded image templates, match thresholds, click coordinates, and color verification parameters.
- Switching profiles or saving updates is done atomically to eliminate risk of file truncation.

---

## 🔒 License & Disclaimer

This project is released under the [MIT License](LICENSE).

*Disclaimer: This software is intended for educational, testing, and accessibility automation purposes. Please comply with the Terms of Service of any target application or game.*
