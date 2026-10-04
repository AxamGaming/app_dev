#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OP Auto Clicker 4.1 - Wayland Edition (Linux / EndeavourOS / KDE Plasma)
========================================================================
A full-featured GUI auto clicker clone of "OP Auto Clicker 4.1" that works
natively on Wayland (KDE Plasma, GNOME, Hyprland, Sway, ...) and on X11.

Why it works on Wayland
-----------------------
Wayland compositors forbid applications from injecting synthetic input or
grabbing global hotkeys through the old X11 tricks (xdotool etc.).  The only
compositor-independent, reliable way is kernel-level synthetic input through
the `uinput` subsystem plus passive reading of `/dev/input/event*` devices.
This app uses python-evdev for both:

  * op-autoclicker-mouse     : relative mouse device  -> clicks (any button)
  * op-autoclicker-keyboard  : keyboard device        -> playback of keys
  * op-autoclicker-tablet    : absolute pen tablet    -> cursor teleport for
                               "Pick location" (libinput treats it like a
                               Wacom pen: proximity-in moves the cursor)
  * passive /dev/input reader: global hotkey (default F6) + Record & Playback

One-time setup (udev rule + `input` group) is required - see
setup_permissions.sh / README.md.  Without it the GUI still opens but the
input backend stays disabled and tells you what to run.

Single-file "run anywhere" mode: this whole application is this one script.
Copy it to any machine with Python + PySide6 + evdev and run:
    python3 op_autoclicker.py
"""

import argparse
import json
import os
import random
import select
import sys
import threading
import time

APP_NAME = "OP Auto Clicker 4.1"
APP_VERSION = "4.1-wl1"
DEVICE_PREFIX = "op-autoclicker"
CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "op-autoclicker")
CONFIG_FILE = os.path.join(CONFIG_DIR, "settings.json")

try:
    from PySide6.QtCore import Qt, QObject, QThread, Signal, QTimer, QPoint, QPointF
    from PySide6.QtGui import (QColor, QCursor, QFont, QGuiApplication, QPainter,
                               QKeyEvent)
    from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog,
                                   QFileDialog, QGridLayout, QGroupBox,
                                   QHBoxLayout, QLabel, QMainWindow, QMessageBox,
                                   QPushButton, QRadioButton, QSpinBox,
                                   QToolButton, QVBoxLayout, QWidget)
    QT_OK = True
except ImportError:  # pragma: no cover
    QT_OK = False

HAVE_EVDEV = False
try:
    import evdev
    from evdev import AbsInfo, UInput
    from evdev import ecodes as e
    HAVE_EVDEV = True
except ImportError:  # pragma: no cover
    evdev = None
    e = None

# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def key_name(code):
    """evdev key code -> pretty name (KEY_F6 -> F6)."""
    if not HAVE_EVDEV:
        return "F6"
    name = e.keys.get(code, "KEY_%d" % code)
    if isinstance(name, (list, tuple)):
        name = name[0]
    for p in ("KEY_", "BTN_"):
        if name.startswith(p):
            name = name[len(p):]
    return name.replace("_", " ").capitalize()


def load_config():
    try:
        with open(CONFIG_FILE) as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_config(cfg):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w") as fh:
            json.dump(cfg, fh, indent=2)
    except Exception:
        pass


# --------------------------------------------------------------------------
# input backend (uinput synthetic input + /dev/input reading)
# --------------------------------------------------------------------------

class BackendError(Exception):
    pass


class UInputBackend(object):
    """Kernel level synthetic input. Works on every Wayland compositor & X11."""

    def __init__(self, virtual_size=(3840, 2160)):
        if not HAVE_EVDEV:
            raise BackendError("python-evdev is not installed "
                               "(pacman -S python-evdev / pip install evdev)")
        w = max(2, int(virtual_size[0]) - 1)
        h = max(2, int(virtual_size[1]) - 1)
        self._lock = threading.RLock()
        self.max_x, self.max_y = w, h
        try:
            self.mouse = UInput(
                name=DEVICE_PREFIX + "-mouse",
                events={
                    e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE,
                               e.BTN_SIDE, e.BTN_EXTRA],
                    e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL, e.REL_HWHEEL],
                })
            self.kbd = UInput(
                name=DEVICE_PREFIX + "-keyboard",
                events={e.EV_KEY: list(range(1, e.KEY_MAX + 1))})
            absinfo = AbsInfo(value=0, min=0, max=w, fuzz=0, flat=0, resolution=0)
            absinfo_y = AbsInfo(value=0, min=0, max=h, fuzz=0, flat=0, resolution=0)
            press = AbsInfo(value=0, min=0, max=1024, fuzz=0, flat=0, resolution=0)
            self.pen = UInput(
                name=DEVICE_PREFIX + "-tablet",
                events={
                    e.EV_ABS: [(e.ABS_X, absinfo), (e.ABS_Y, absinfo_y),
                               (e.ABS_PRESSURE, press)],
                    e.EV_KEY: [e.BTN_TOOL_PEN, e.BTN_TOUCH],
                })
        except OSError as exc:
            raise BackendError(
                "cannot create synthetic input devices (%s). "
                "Run ./setup_permissions.sh once, then log out & back in." % exc)
        self.devices = (self.mouse, self.kbd, self.pen)

    # -- clicking ---------------------------------------------------------
    def click(self, btn_code, hold=0.015):
        with self._lock:
            self.mouse.write(e.EV_KEY, btn_code, 1)
            self.mouse.syn()
            time.sleep(hold)
            self.mouse.write(e.EV_KEY, btn_code, 0)
            self.mouse.syn()

    # -- absolute cursor teleport (pen proximity-in) -----------------------
    def move_absolute(self, x, y):
        x = int(max(0, min(self.max_x, x)))
        y = int(max(0, min(self.max_y, y)))
        with self._lock:
            self.pen.write(e.EV_KEY, e.BTN_TOOL_PEN, 1)
            self.pen.write(e.EV_ABS, e.ABS_X, x)
            self.pen.write(e.EV_ABS, e.ABS_Y, y)
            self.pen.write(e.EV_ABS, e.ABS_PRESSURE, 0)
            self.pen.syn()
            time.sleep(0.025)
            self.pen.write(e.EV_KEY, e.BTN_TOOL_PEN, 0)
            self.pen.syn()

    # -- generic event injection (used by playback) ------------------------
    def send(self, etype, code, value):
        with self._lock:
            if etype == e.EV_REL or (etype == e.EV_KEY and code >= 0x100):
                dev = self.mouse
            elif etype == e.EV_KEY:
                dev = self.kbd
            else:
                return
            dev.write(etype, code, value)
            dev.syn()

    def close(self):
        for dev in self.devices:
            try:
                dev.close()
            except Exception:
                pass


def scan_input_devices():
    """Return (keyboards, pointers) passive-readable devices, ours excluded."""
    keyboards, pointers = [], []
    if not HAVE_EVDEV:
        return keyboards, pointers
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
        except Exception:
            continue
        name = dev.name or ""
        if name.startswith(DEVICE_PREFIX):
            dev.close()
            continue
        try:
            caps = dev.capabilities()
        except Exception:
            dev.close()
            continue
        keys = caps.get(e.EV_KEY, [])
        rels = caps.get(e.EV_REL, [])
        if e.KEY_A in keys and e.KEY_SPACE in keys:
            keyboards.append(dev)
        elif e.BTN_LEFT in keys or e.REL_X in rels:
            pointers.append(dev)
        else:
            dev.close()
    return keyboards, pointers


class DeviceMonitor(QThread):
    """Passively watches keyboards/mice: global hotkey, key capture, recording."""

    hotkey_pressed = Signal()
    captured = Signal(int)          # evdev key code
    record_tick = Signal(int)       # number of recorded events so far

    def __init__(self, parent=None):
        QThread.__init__(self, parent)
        self._stop = threading.Event()
        self.hotkey_code = e.KEY_F6 if HAVE_EVDEV else 60
        self.capture_mode = False
        self.recording = False
        self.rec_events = []        # [dt_ms, type, code, value]
        self._devices = []
        self._last_ts = None
        self._last_scan = 0.0

    def set_hotkey(self, code):
        self.hotkey_code = code

    def stop(self):
        self._stop.set()

    def start_recording(self):
        self.rec_events = []
        self._last_ts = None
        self.recording = True

    def stop_recording(self):
        self.recording = False
        return list(self.rec_events)

    # -- internals ---------------------------------------------------------
    def _rescan(self):
        have = {d.path for d in self._devices}
        kbs, pts = scan_input_devices()
        for dev in kbs + pts:
            if dev.path not in have:
                self._devices.append(dev)

    def _handle(self, ev):
        if ev.type == e.EV_KEY:
            if self.capture_mode and ev.value == 1:
                self.capture_mode = False
                self.captured.emit(ev.code)
                return
            if (not self.capture_mode and ev.value == 1
                    and ev.code == self.hotkey_code):
                self.hotkey_pressed.emit()
            if self.recording and ev.value in (0, 1):
                self._record(ev)
        elif ev.type == e.EV_REL and self.recording:
            self._record(ev)

    def _record(self, ev):
        now = ev.timestamp() if hasattr(ev, "timestamp") else ev.sec + ev.usec / 1e6
        if self._last_ts is None:
            dt = 0.0
        else:
            dt = max(0.0, (now - self._last_ts) * 1000.0)
        self._last_ts = now
        self.rec_events.append([round(dt, 3), ev.type, ev.code, ev.value])
        self.record_tick.emit(len(self.rec_events))

    def run(self):
        self._rescan()
        self._last_scan = time.time()
        while not self._stop.is_set():
            if time.time() - self._last_scan > 5:
                for d in list(self._devices):
                    try:
                        d.fd
                    except Exception:
                        self._devices.remove(d)
                self._rescan()
                self._last_scan = time.time()
            fds = {}
            for dev in self._devices:
                try:
                    fds[dev.fd] = dev
                except Exception:
                    pass
            if not fds:
                self._stop.wait(0.5)
                continue
            try:
                ready, _, _ = select.select(list(fds.keys()), [], [], 0.25)
            except OSError:
                self._devices = []
                continue
            for fd in ready:
                dev = fds[fd]
                try:
                    for ev in dev.read():
                        self._handle(ev)
                except OSError:
                    if dev in self._devices:
                        self._devices.remove(dev)
                except Exception:
                    pass
        for dev in self._devices:
            try:
                dev.close()
            except Exception:
                pass


# --------------------------------------------------------------------------
# worker threads
# --------------------------------------------------------------------------

class ClickerThread(QThread):
    count_changed = Signal(int)
    done = Signal(str)

    def __init__(self, backend, params, parent=None):
        QThread.__init__(self, parent)
        self.backend = backend
        self.p = params
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        p = self.p
        clicks = 0
        cycles = 0
        finished = False
        while not self._stop.is_set():
            if p["pos_mode"] == "pick":
                try:
                    self.backend.move_absolute(p["x"], p["y"])
                    time.sleep(0.02)
                except Exception:
                    pass
            for i in range(p["clicks_per"]):
                if self._stop.is_set():
                    break
                try:
                    self.backend.click(p["button"])
                except Exception:
                    break
                clicks += 1
                self.count_changed.emit(clicks)
                if i < p["clicks_per"] - 1:
                    self._stop.wait(0.04)
            cycles += 1
            if p["repeat_times"] is not None and cycles >= p["repeat_times"]:
                finished = True
                break
            wait = p["interval"]
            if p["jitter"] > 0:
                wait += random.uniform(-p["jitter"], p["jitter"])
            wait = max(0.0005, wait)
            self._stop.wait(wait)
        self.done.emit("finished" if finished else "stopped")


class PlaybackThread(QThread):
    progress = Signal(int)
    done = Signal(str)

    def __init__(self, backend, events, loop, parent=None):
        QThread.__init__(self, parent)
        self.backend = backend
        self.events = events
        self.loop = loop
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        played = 0
        while not self._stop.is_set():
            for dt, etype, code, value in self.events:
                if self._stop.is_set():
                    break
                if dt > 0:
                    self._stop.wait(min(dt / 1000.0, 30.0))
                try:
                    self.backend.send(etype, code, value)
                except Exception:
                    break
                played += 1
                self.progress.emit(played)
            if not self.loop:
                break
        self.done.emit("stopped" if self._stop.is_set() else "finished")


# --------------------------------------------------------------------------
# pick-location overlay (Wayland safe: click-through-surface positioning)
# --------------------------------------------------------------------------

class PickOverlay(QWidget):
    """Full screen translucent overlay per screen; a click yields global coords.

    On Wayland QCursor.pos() is unreliable, but a full screen surface knows its
    own origin, so local click position + screen origin = global position.
    """

    def __init__(self, screen, callback, parent=None):
        QWidget.__init__(self, parent)
        self.screen_geo = screen.geometry()
        self.callback = callback
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setCursor(Qt.CrossCursor)
        self.setGeometry(self.screen_geo)
        self.showFullScreen()
        self.raise_()

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 110))
        p.setPen(QColor(255, 255, 255))
        f = QFont()
        f.setPointSize(16)
        f.setBold(True)
        p.setFont(f)
        p.drawText(self.rect(), Qt.AlignCenter,
                   "Click the target location\n(Esc to cancel)")
        p.end()

    def mousePressEvent(self, ev):
        local = ev.position().toPoint()
        glob = self.screen_geo.topLeft() + local
        cb = self.callback
        self.callback = None
        if cb:
            cb(glob)
        ev.accept()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape:
            cb = self.callback
            self.callback = None
            if cb:
                cb(None)
        ev.accept()


class CountdownDialog(QDialog):
    """X11 fallback picker: 3..2..1 then read QCursor.pos()."""

    def __init__(self, parent=None):
        QDialog.__init__(self, parent)
        self.setWindowTitle("Pick location")
        self.setModal(True)
        lay = QVBoxLayout(self)
        self.label = QLabel("3")
        f = QFont()
        f.setPointSize(48)
        f.setBold(True)
        self.label.setFont(f)
        self.label.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.label)
        hint = QLabel("Place the cursor where clicking should happen...")
        hint.setAlignment(Qt.AlignCenter)
        lay.addWidget(hint)
        self.result_pos = None
        self._n = 3
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)
        self._timer.start(1000)

    def _step(self):
        self._n -= 1
        if self._n <= 0:
            self._timer.stop()
            self.result_pos = QCursor.pos()
            self.accept()
        else:
            self.label.setText(str(self._n))

    def reject(self):
        self._timer.stop()
        QDialog.reject(self)


# --------------------------------------------------------------------------
# dialogs
# --------------------------------------------------------------------------

class HotkeyDialog(QDialog):
    def __init__(self, main, parent=None):
        QDialog.__init__(self, parent)
        self.main = main
        self.new_code = None
        self.setWindowTitle("Hotkey setting")
        self.setModal(True)
        lay = QVBoxLayout(self)
        self.info = QLabel()
        self.info.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.info)
        self.status = QLabel("")
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setStyleSheet("color: #888;")
        lay.addWidget(self.status)
        row = QHBoxLayout()
        self.btn_set = QPushButton("Set new hotkey")
        self.btn_reset = QPushButton("Reset to F6")
        self.btn_close = QPushButton("Close")
        row.addWidget(self.btn_set)
        row.addWidget(self.btn_reset)
        row.addWidget(self.btn_close)
        lay.addLayout(row)
        self.btn_set.clicked.connect(self._begin_capture)
        self.btn_reset.clicked.connect(self._reset)
        self.btn_close.clicked.connect(self.accept)
        if main.monitor is not None:
            main.monitor.captured.connect(self._on_captured)
        self._refresh()
        self._capturing = False

    def _refresh(self):
        self.info.setText("Current start/stop hotkey:  %s"
                          % key_name(self.main.hotkey_code))

    def _begin_capture(self):
        self._capturing = True
        if self.main.monitor is not None:
            self.main.monitor.capture_mode = True
            self.status.setText("Press the new hotkey now (global listener)...")
        else:
            self.status.setText("Input backend unavailable - press the key "
                                "while this window has focus.")

    def _on_captured(self, code):
        self._capturing = False
        self.new_code = code
        self.main.set_hotkey(code)
        self._refresh()
        self.status.setText("Hotkey set to %s." % key_name(code))

    def keyPressEvent(self, ev):
        if self._capturing and self.main.monitor is None:
            code = self._qt_key_to_evdev(ev.key())
            if code is not None:
                self._on_captured(code)
            else:
                self.status.setText("Unsupported key.")
            return
        QDialog.keyPressEvent(self, ev)

    @staticmethod
    def _qt_key_to_evdev(qtkey):
        if not HAVE_EVDEV:
            return None
        m = {}
        for i in range(1, 13):
            m[getattr(Qt, "Key_F%d" % i)] = getattr(e, "KEY_F%d" % i)
        m[Qt.Key_Space] = e.KEY_SPACE
        m[Qt.Key_Return] = e.KEY_ENTER
        m[Qt.Key_Tab] = e.KEY_TAB
        return m.get(qtkey)

    def _reset(self):
        if HAVE_EVDEV:
            self._on_captured(e.KEY_F6)
        self.new_code = e.KEY_F6 if HAVE_EVDEV else None


class RecordDialog(QDialog):
    def __init__(self, main, parent=None):
        QDialog.__init__(self, parent)
        self.main = main
        self.events = []
        self.play_thread = None
        self.setWindowTitle("Record & Playback")
        self.setModal(False)
        lay = QVBoxLayout(self)
        self.info = QLabel("Idle.")
        lay.addWidget(self.info)
        g1 = QGridLayout()
        self.btn_rec = QPushButton("Record")
        self.btn_stop_rec = QPushButton("Stop recording")
        self.btn_play = QPushButton("Play")
        self.btn_stop_play = QPushButton("Stop playback")
        self.btn_stop_rec.setEnabled(False)
        self.btn_stop_play.setEnabled(False)
        g1.addWidget(self.btn_rec, 0, 0)
        g1.addWidget(self.btn_stop_rec, 0, 1)
        g1.addWidget(self.btn_play, 1, 0)
        g1.addWidget(self.btn_stop_play, 1, 1)
        lay.addLayout(g1)
        g2 = QGridLayout()
        self.btn_save = QPushButton("Save...")
        self.btn_load = QPushButton("Load...")
        self.btn_clear = QPushButton("Clear")
        self.chk_loop = QCheckBox("Loop until stopped")
        g2.addWidget(self.btn_save, 0, 0)
        g2.addWidget(self.btn_load, 0, 1)
        g2.addWidget(self.btn_clear, 0, 2)
        g2.addWidget(self.chk_loop, 1, 0, 1, 3)
        lay.addLayout(g2)
        self.btn_rec.clicked.connect(self._record)
        self.btn_stop_rec.clicked.connect(self._stop_record)
        self.btn_play.clicked.connect(self._play)
        self.btn_stop_play.clicked.connect(self._stop_play)
        self.btn_save.clicked.connect(self._save)
        self.btn_load.clicked.connect(self._load)
        self.btn_clear.clicked.connect(self._clear)
        if main.monitor is not None:
            main.monitor.record_tick.connect(self._tick)
        self._set_state("idle")

    def _tick(self, n):
        self.info.setText("Recording... %d events" % n)

    def _set_state(self, state):
        self._state = state
        idle = state == "idle"
        rec = state == "recording"
        play = state == "playing"
        self.btn_rec.setEnabled(idle)
        self.btn_stop_rec.setEnabled(rec)
        self.btn_play.setEnabled(idle and bool(self.events))
        self.btn_stop_play.setEnabled(play)
        self.btn_save.setEnabled(idle and bool(self.events))
        self.btn_clear.setEnabled(idle)
        self.btn_load.setEnabled(idle)

    def _record(self):
        if self.main.monitor is None:
            QMessageBox.warning(self, "Record",
                                "Input backend unavailable. Run "
                                "setup_permissions.sh and relogin first.")
            return
        self.main.monitor.start_recording()
        self._set_state("recording")
        self.info.setText("Recording... 0 events")

    def _stop_record(self):
        self.events = self.main.monitor.stop_recording()
        dur = sum(ev[0] for ev in self.events) / 1000.0
        self._set_state("idle")
        self.info.setText("Recorded %d events (%.1f s)." % (len(self.events), dur))

    def _play(self):
        if self.main.backend is None:
            QMessageBox.warning(self, "Playback",
                                "Input backend unavailable. Run "
                                "setup_permissions.sh and relogin first.")
            return
        if not self.events:
            return
        self._set_state("playing")
        self.info.setText("Playing...")
        self.play_thread = PlaybackThread(self.main.backend, self.events,
                                          self.chk_loop.isChecked())
        self.play_thread.progress.connect(
            lambda n: self.info.setText("Playing... %d events" % n))
        self.play_thread.done.connect(self._play_done)
        self.play_thread.start()

    def _play_done(self, reason):
        self._set_state("idle")
        self.info.setText("Playback %s (%d events)." % (reason, len(self.events)))

    def _stop_play(self):
        if self.play_thread is not None:
            self.play_thread.stop()

    def _save(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save recording", os.path.expanduser("~/recording.json"),
            "JSON files (*.json)")
        if path:
            with open(path, "w") as fh:
                json.dump({"app": APP_NAME, "events": self.events}, fh)
            self.info.setText("Saved %d events to %s" % (len(self.events), path))

    def _load(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load recording", os.path.expanduser("~"),
            "JSON files (*.json)")
        if not path:
            return
        try:
            with open(path) as fh:
                data = json.load(fh)
            self.events = [list(ev) for ev in data["events"]]
        except Exception as exc:
            QMessageBox.critical(self, "Load", "Cannot load file: %s" % exc)
            return
        self._set_state("idle")
        self.info.setText("Loaded %d events from %s" % (len(self.events), path))

    def _clear(self):
        self.events = []
        self._set_state("idle")
        self.info.setText("Idle.")

    def closeEvent(self, ev):
        self._stop_play()
        if self.main.monitor is not None and self.main.monitor.recording:
            self.main.monitor.stop_recording()
        ev.accept()


# --------------------------------------------------------------------------
# main window
# --------------------------------------------------------------------------

BTN_MAP = [("Left", "BTN_LEFT"), ("Right", "BTN_RIGHT"), ("Middle", "BTN_MIDDLE"),
           ("Back", "BTN_SIDE"), ("Forward", "BTN_EXTRA")]
TYPE_MAP = [("Single", 1), ("Double", 2), ("Triple", 3)]


class MainWindow(QMainWindow):
    def __init__(self):
        QMainWindow.__init__(self)
        self.setWindowTitle(APP_NAME)
        self.cfg = load_config()
        self.backend = None
        self.backend_error = ""
        self.monitor = None
        self.clicker = None
        self.overlays = []
        self.record_dialog = None
        self.total_clicks = 0
        self.hotkey_code = int(self.cfg.get("hotkey_code",
                                            e.KEY_F6 if HAVE_EVDEV else 60))

        # ---- backend ----------------------------------------------------
        vsize = (3840, 2160)
        try:
            scr = QGuiApplication.primaryScreen()
            if scr is not None:
                vg = scr.virtualGeometry()
                vsize = (vg.width(), vg.height())
        except Exception:
            pass
        try:
            self.backend = UInputBackend(vsize)
        except Exception as exc:
            self.backend = None
            self.backend_error = str(exc)
        if HAVE_EVDEV:
            try:
                self.monitor = DeviceMonitor(self)
                self.monitor.set_hotkey(self.hotkey_code)
                self.monitor.hotkey_pressed.connect(self.toggle_clicking)
                self.monitor.start()
            except Exception:
                self.monitor = None

        self._build_ui()
        self._load_settings_to_ui()
        self._update_hotkey_labels()
        self._update_status()

    # ---------------- UI --------------------------------------------------
    def _build_ui(self):
        cw = QWidget()
        self.setCentralWidget(cw)
        root = QVBoxLayout(cw)
        root.setSpacing(8)

        # click interval
        gb = QGroupBox("Click interval")
        v = QVBoxLayout(gb)
        r1 = QHBoxLayout()
        self.sp_h = QSpinBox(); self.sp_h.setRange(0, 99)
        self.sp_m = QSpinBox(); self.sp_m.setRange(0, 59)
        self.sp_s = QSpinBox(); self.sp_s.setRange(0, 59)
        self.sp_ms = QSpinBox(); self.sp_ms.setRange(0, 999)
        for sp, txt in ((self.sp_h, "hours"), (self.sp_m, "mins"),
                        (self.sp_s, "secs"), (self.sp_ms, "milliseconds")):
            sp.setMinimumWidth(70)
            r1.addWidget(sp)
            r1.addWidget(QLabel(txt))
        v.addLayout(r1)
        r2 = QHBoxLayout()
        help_btn = QToolButton()
        help_btn.setText("?")
        help_btn.setToolTip(
            "The interval is the pause between two click cycles.\n"
            "Enable 'Random offset' to add a random jitter of +/- N ms\n"
            "to every interval (useful to look less mechanical).")
        help_btn.clicked.connect(lambda: QMessageBox.information(
            self, "Click interval",
            "The interval is the pause between two click cycles.\n"
            "'Random offset +- N milliseconds' adds a random jitter of\n"
            "+/- N ms to every interval (useful to look less mechanical)."))
        r2.addWidget(help_btn)
        self.chk_jitter = QCheckBox("Random offset + -")
        r2.addWidget(self.chk_jitter)
        self.sp_jitter = QSpinBox(); self.sp_jitter.setRange(0, 10000)
        self.sp_jitter.setValue(40)
        self.sp_jitter.setMinimumWidth(70)
        r2.addWidget(self.sp_jitter)
        r2.addWidget(QLabel("milliseconds"))
        r2.addStretch(1)
        v.addLayout(r2)
        root.addWidget(gb)

        # options + repeat
        mid = QHBoxLayout()
        gb_opt = QGroupBox("Click options")
        g = QGridLayout(gb_opt)
        g.addWidget(QLabel("Mouse button:"), 0, 0)
        self.cb_button = QComboBox()
        for label, _ in BTN_MAP:
            self.cb_button.addItem(label)
        g.addWidget(self.cb_button, 0, 1)
        g.addWidget(QLabel("Click type:"), 1, 0)
        self.cb_type = QComboBox()
        for label, _ in TYPE_MAP:
            self.cb_type.addItem(label)
        g.addWidget(self.cb_type, 1, 1)
        mid.addWidget(gb_opt)

        gb_rep = QGroupBox("Click repeat")
        g = QGridLayout(gb_rep)
        self.rb_times = QRadioButton("Repeat")
        self.sp_times = QSpinBox(); self.sp_times.setRange(1, 999999999)
        self.sp_times.setMinimumWidth(80)
        self.rb_until = QRadioButton("Repeat until stopped")
        self.rb_until.setChecked(True)
        self.rb_times.toggled.connect(self.sp_times.setEnabled)
        self.sp_times.setEnabled(False)
        g.addWidget(self.rb_times, 0, 0)
        g.addWidget(self.sp_times, 0, 1)
        g.addWidget(QLabel("times"), 0, 2)
        g.addWidget(self.rb_until, 1, 0, 1, 3)
        mid.addWidget(gb_rep)
        root.addLayout(mid)

        # cursor position
        gb_pos = QGroupBox("Cursor position")
        r = QHBoxLayout(gb_pos)
        self.rb_current = QRadioButton("Current location")
        self.rb_current.setChecked(True)
        self.rb_pick = QRadioButton("")
        self.btn_pick = QPushButton("Pick location")
        self.btn_pick.clicked.connect(self.pick_location)
        self.sp_x = QSpinBox(); self.sp_x.setRange(0, 32767); self.sp_x.setMinimumWidth(70)
        self.sp_y = QSpinBox(); self.sp_y.setRange(0, 32767); self.sp_y.setMinimumWidth(70)
        r.addWidget(self.rb_current)
        r.addStretch(1)
        r.addWidget(self.rb_pick)
        r.addWidget(self.btn_pick)
        r.addWidget(QLabel("X"))
        r.addWidget(self.sp_x)
        r.addWidget(QLabel("Y"))
        r.addWidget(self.sp_y)
        root.addWidget(gb_pos)

        # buttons 2x2
        g = QGridLayout()
        self.btn_start = QPushButton("Start (F6)")
        self.btn_stop = QPushButton("Stop (F6)")
        self.btn_stop.setEnabled(False)
        self.btn_hotkey = QPushButton("Hotkey setting")
        self.btn_record = QPushButton("Record && Playback")
        self.btn_start.clicked.connect(self.start_clicking)
        self.btn_stop.clicked.connect(self.stop_clicking)
        self.btn_hotkey.clicked.connect(self.open_hotkey_dialog)
        self.btn_record.clicked.connect(self.open_record_dialog)
        g.addWidget(self.btn_start, 0, 0)
        g.addWidget(self.btn_stop, 0, 1)
        g.addWidget(self.btn_hotkey, 1, 0)
        g.addWidget(self.btn_record, 1, 1)
        root.addLayout(g)

        # status bar
        self.status_msg = QLabel("")
        self.status_clicks = QLabel("Clicks: 0")
        sb = self.statusBar()
        sb.addWidget(self.status_msg, 1)
        sb.addPermanentWidget(self.status_clicks)

        self.setMinimumWidth(520)

    # ---------------- settings --------------------------------------------
    def _load_settings_to_ui(self):
        c = self.cfg
        self.sp_h.setValue(int(c.get("h", 0)))
        self.sp_m.setValue(int(c.get("m", 0)))
        self.sp_s.setValue(int(c.get("s", 0)))
        self.sp_ms.setValue(int(c.get("ms", 100)))
        self.chk_jitter.setChecked(bool(c.get("jitter_on", False)))
        self.sp_jitter.setValue(int(c.get("jitter_ms", 40)))
        self.cb_button.setCurrentIndex(max(0, int(c.get("button_idx", 0))))
        self.cb_type.setCurrentIndex(max(0, int(c.get("type_idx", 0))))
        if c.get("repeat_mode", "until") == "times":
            self.rb_times.setChecked(True)
        else:
            self.rb_until.setChecked(True)
        self.sp_times.setValue(int(c.get("repeat_times", 1)))
        if c.get("pos_mode", "current") == "pick":
            self.rb_pick.setChecked(True)
        else:
            self.rb_current.setChecked(True)
        self.sp_x.setValue(int(c.get("x", 0)))
        self.sp_y.setValue(int(c.get("y", 0)))

    def _collect_settings(self):
        return {
            "h": self.sp_h.value(), "m": self.sp_m.value(),
            "s": self.sp_s.value(), "ms": self.sp_ms.value(),
            "jitter_on": self.chk_jitter.isChecked(),
            "jitter_ms": self.sp_jitter.value(),
            "button_idx": self.cb_button.currentIndex(),
            "type_idx": self.cb_type.currentIndex(),
            "repeat_mode": "times" if self.rb_times.isChecked() else "until",
            "repeat_times": self.sp_times.value(),
            "pos_mode": "pick" if self.rb_pick.isChecked() else "current",
            "x": self.sp_x.value(), "y": self.sp_y.value(),
            "hotkey_code": self.hotkey_code,
        }

    def set_hotkey(self, code):
        self.hotkey_code = code
        if self.monitor is not None:
            self.monitor.set_hotkey(code)
        self._update_hotkey_labels()

    def _update_hotkey_labels(self):
        name = key_name(self.hotkey_code)
        self.btn_start.setText("Start (%s)" % name)
        self.btn_stop.setText("Stop (%s)" % name)

    def _update_status(self):
        if self.backend is None:
            self.status_msg.setText("Input backend OFFLINE - run "
                                    "setup_permissions.sh once, then relogin. "
                                    "(%s)" % self.backend_error[:80])
        elif self.monitor is None:
            self.status_msg.setText("Clicking ready; global hotkey unavailable "
                                    "(no readable /dev/input devices).")
        else:
            self.status_msg.setText("Ready. Backend: uinput (Wayland/X11). "
                                    "Hotkey: %s" % key_name(self.hotkey_code))

    # ---------------- clicker control --------------------------------------
    def start_clicking(self):
        if self.clicker is not None:
            return
        if self.backend is None:
            QMessageBox.warning(
                self, "Input backend unavailable",
                "Synthetic input is not available yet.\n\n"
                "Wayland does not let apps inject clicks without kernel-level "
                "access. One-time fix:\n"
                "  1. run:  ./setup_permissions.sh\n"
                "  2. log out and back in (or reboot)\n\n"
                "Details: %s" % self.backend_error)
            return
        c = self._collect_settings()
        interval = (c["h"] * 3600 + c["m"] * 60 + c["s"]
                    + c["ms"] / 1000.0)
        params = {
            "button": getattr(e, BTN_MAP[c["button_idx"]][1]),
            "clicks_per": TYPE_MAP[c["type_idx"]][1],
            "interval": interval,
            "jitter": (c["jitter_ms"] / 1000.0) if c["jitter_on"] else 0.0,
            "repeat_times": c["repeat_times"] if c["repeat_mode"] == "times" else None,
            "pos_mode": c["pos_mode"],
            "x": c["x"], "y": c["y"],
        }
        self.total_clicks = 0
        self.status_clicks.setText("Clicks: 0")
        self.clicker = ClickerThread(self.backend, params)
        self.clicker.count_changed.connect(self._on_count)
        self.clicker.done.connect(self._on_clicker_done)
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.status_msg.setText("Running...")
        self.clicker.start()

    def stop_clicking(self):
        if self.clicker is not None:
            self.clicker.stop()

    def toggle_clicking(self):
        if self.clicker is not None:
            self.stop_clicking()
        else:
            self.start_clicking()

    def _on_count(self, n):
        self.total_clicks = n
        self.status_clicks.setText("Clicks: %d" % n)

    def _on_clicker_done(self, reason):
        self.clicker = None
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.status_msg.setText("Stopped (%s). Clicks: %d"
                                % (reason, self.total_clicks))

    # ---------------- pick location -----------------------------------------
    def pick_location(self):
        self.rb_pick.setChecked(True)
        if QGuiApplication.platformName() == "wayland":
            for scr in QGuiApplication.screens():
                ov = PickOverlay(scr, self._on_picked)
                self.overlays.append(ov)
        else:
            dlg = CountdownDialog(self)
            if dlg.exec() == QDialog.Accepted and dlg.result_pos is not None:
                self._on_picked(QPoint(dlg.result_pos.x(), dlg.result_pos.y()))
            else:
                self._on_picked(None)

    def _on_picked(self, pos):
        for ov in self.overlays:
            ov.close()
            ov.deleteLater()
        self.overlays = []
        if pos is None:
            return
        self.sp_x.setValue(pos.x())
        self.sp_y.setValue(pos.y())
        self.rb_pick.setChecked(True)
        self.status_msg.setText("Pick location set to X=%d Y=%d" % (pos.x(), pos.y()))

    # ---------------- dialogs ------------------------------------------------
    def open_hotkey_dialog(self):
        dlg = HotkeyDialog(self, self)
        dlg.exec()
        self._update_status()

    def open_record_dialog(self):
        if self.record_dialog is None:
            self.record_dialog = RecordDialog(self, self)
        self.record_dialog.show()
        self.record_dialog.raise_()

    # ---------------- shutdown ------------------------------------------------
    def closeEvent(self, ev):
        save_config(self._collect_settings())
        try:
            self.stop_clicking()
            if self.clicker is not None:
                self.clicker.wait(2000)
        except Exception:
            pass
        if self.record_dialog is not None:
            try:
                self.record_dialog._stop_play()
            except Exception:
                pass
        if self.monitor is not None:
            self.monitor.stop()
            self.monitor.wait(2000)
        if self.backend is not None:
            self.backend.close()
        ev.accept()


# --------------------------------------------------------------------------
# self test / entry point
# --------------------------------------------------------------------------

def selftest():
    print("%s %s - self test" % (APP_NAME, APP_VERSION))
    print("python            : %s" % sys.version.split()[0])
    print("Qt platform       : %s" % (os.environ.get("QT_QPA_PLATFORM", "auto")))
    print("session type      : %s" % os.environ.get("XDG_SESSION_TYPE", "?"))
    print("desktop           : %s" % os.environ.get("XDG_CURRENT_DESKTOP", "?"))
    print("evdev module      : %s" % ("OK" if HAVE_EVDEV else "MISSING"))
    if not HAVE_EVDEV:
        return 1
    ok = True
    dev = "/dev/uinput"
    writable = os.access(dev, os.W_OK)
    print("/dev/uinput       : %s (writable: %s)" % ("present" if os.path.exists(dev) else "MISSING", writable))
    if not writable:
        ok = False
        print("  -> run ./setup_permissions.sh, then log out & back in")
    kbs, pts = scan_input_devices()
    print("/dev/input devices: %d keyboards, %d pointers readable" % (len(kbs), len(pts)))
    for d in kbs + pts:
        d.close()
    if not kbs and not pts:
        ok = False
        print("  -> no readable input devices; run ./setup_permissions.sh + relogin")
    try:
        b = UInputBackend((1920, 1080))
        print("uinput devices    : created OK (mouse/keyboard/tablet)")
        b.close()
    except Exception as exc:
        ok = False
        print("uinput devices    : FAIL - %s" % exc)
    print("result            : %s" % ("ALL GOOD" if ok else "SETUP NEEDED"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=APP_NAME)
    ap.add_argument("--selftest", action="store_true",
                    help="diagnose Wayland/uinput/permissions and exit")
    ap.add_argument("--version", action="store_true")
    args = ap.parse_args()
    if args.version:
        print("%s %s" % (APP_NAME, APP_VERSION))
        return 0
    if not QT_OK:
        print("PySide6 is required. On EndeavourOS:  sudo pacman -S python-pyside6"
              "   (or: pip install PySide6)", file=sys.stderr)
        return 1
    if args.selftest:
        return selftest()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
