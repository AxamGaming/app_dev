#!/usr/bin/env python3
"""Headless smoke tests for op_autoclicker.py (offscreen Qt + fake backend)."""
import os, sys, time, threading, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["HOME"] = tempfile.mkdtemp(prefix="oac-test-")   # isolate settings
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "op-autoclicker"))

import op_autoclicker as oa
from evdev import ecodes as e
from PySide6.QtCore import QPoint, QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

FAILS = []
def check(cond, msg):
    print(("PASS  " if cond else "FAIL  ") + msg)
    if not cond:
        FAILS.append(msg)

class FakeBackend:
    def __init__(self):
        self.clicks = []
        self.moves = []
        self.sent = []
        self.lock = threading.RLock()
    def click(self, btn, hold=0.0):
        with self.lock:
            self.clicks.append(btn)
    def move_absolute(self, x, y):
        with self.lock:
            self.moves.append((x, y))
    def send(self, t, c, v):
        with self.lock:
            self.sent.append((t, c, v))
    def close(self):
        pass

WARN = []
QMessageBox.warning = staticmethod(lambda *a, **k: WARN.append(a[1] if len(a) > 1 else a))

app = QApplication(sys.argv)
win = oa.MainWindow()
win.show()

# --- UI structure ---------------------------------------------------------
check(win.windowTitle() == oa.APP_NAME, "window title")
check(win.btn_start.text() == "Start (F6)" and win.btn_stop.text() == "Stop (F6)", "F6 labels")
check(not win.btn_stop.isEnabled(), "Stop disabled initially")
check(win.sp_ms.value() == 100 and win.sp_jitter.value() == 40, "default interval 100ms / jitter 40")
check(win.rb_until.isChecked() and win.rb_current.isChecked(), "defaults: until stopped + current location")
check([win.cb_type.itemText(i) for i in range(3)] == ["Single", "Double", "Triple"], "click types")
check([win.cb_button.itemText(i) for i in range(5)] == ["Left", "Right", "Middle", "Back", "Forward"], "buttons")

# --- backend offline path --------------------------------------------------
check(win.backend is None, "backend offline in container (expected)")
win.start_clicking()
check(len(WARN) == 1 and win.clicker is None, "start with no backend shows warning, no thread")

# --- clicker engine with fake backend ---------------------------------------
fb = FakeBackend()
win.backend = fb
win.sp_times.setValue(3)
win.rb_times.setChecked(True)
win.sp_ms.setValue(5); win.sp_h.setValue(0); win.sp_m.setValue(0); win.sp_s.setValue(0)
win.start_clicking()
check(win.btn_stop.isEnabled() and not win.btn_start.isEnabled(), "button states while running")
loop = QEventLoop()
def _poll():
    if win.clicker is None:
        loop.quit()
t = QTimer(); t.timeout.connect(_poll); t.start(50)
QTimer.singleShot(5000, loop.quit)
loop.exec()
t.stop()
check(len(fb.clicks) == 3, "repeat 3 times -> 3 clicks (got %d)" % len(fb.clicks))
check(win.total_clicks == 3 and "Clicks: 3" in win.status_clicks.text(), "click counter UI")
check(not win.btn_stop.isEnabled() and win.btn_start.isEnabled(), "button states after finish")

# double click type => 2 clicks per cycle
fb2 = FakeBackend(); win.backend = fb2
win.cb_type.setCurrentIndex(1)  # Double
win.sp_times.setValue(2)
win.start_clicking()
loop = QEventLoop(); t = QTimer(); t.timeout.connect(_poll); t.start(50)
QTimer.singleShot(5000, loop.quit); loop.exec(); t.stop()
check(len(fb2.clicks) == 4, "double x2 cycles -> 4 clicks (got %d)" % len(fb2.clicks))

# repeat until stopped + stop button
fb3 = FakeBackend(); win.backend = fb3
win.rb_until.setChecked(True); win.sp_ms.setValue(10)
win.start_clicking()
time.sleep(0.15)
win.stop_clicking()
loop = QEventLoop(); t = QTimer(); t.timeout.connect(_poll); t.start(50)
QTimer.singleShot(5000, loop.quit); loop.exec(); t.stop()
check(win.clicker is None and len(fb3.clicks) > 0, "until-stopped mode runs and stops (%d clicks)" % len(fb3.clicks))

# pick mode teleports before each cycle
fb4 = FakeBackend(); win.backend = fb4
win.rb_pick.setChecked(True); win.sp_x.setValue(640); win.sp_y.setValue(360)
win.rb_times.setChecked(True); win.sp_times.setValue(2)
win.start_clicking()
loop = QEventLoop(); t = QTimer(); t.timeout.connect(_poll); t.start(50)
QTimer.singleShot(5000, loop.quit); loop.exec(); t.stop()
check(len(fb4.moves) == 2 and fb4.moves[0] == (640, 360), "pick mode teleports to X/Y each cycle")
win._on_picked(QPoint(111, 222))
check(win.sp_x.value() == 111 and win.sp_y.value() == 222 and win.rb_pick.isChecked(), "_on_picked fills X/Y")

# --- hotkey -----------------------------------------------------------------
win.set_hotkey(e.KEY_F9)
check(win.btn_start.text() == "Start (F9)", "hotkey relabel")
dlg = oa.HotkeyDialog(win)
dlg._on_captured(e.KEY_F7)
check(win.hotkey_code == e.KEY_F7 and win.monitor.hotkey_code == e.KEY_F7, "capture updates monitor hotkey")
check(oa.key_name(e.KEY_F6) == "F6" and oa.key_name(e.BTN_LEFT) == "Left", "key_name mapping")
dlg.close()

# --- monitor: hotkey + recording logic (synthetic events) --------------------
mon = win.monitor
check(mon is not None, "monitor thread alive")
class Ev:
    def __init__(self, t, c, v):
        self.type, self.code, self.value = t, c, v
        self.sec, self.usec = int(time.time()), 0
    def timestamp(self):
        return self.sec + self.usec / 1e6
got = []
mon.hotkey_pressed.connect(lambda: got.append("hk"))
mon._handle(Ev(e.EV_KEY, e.KEY_F7, 1))
app.processEvents()
check(got == ["hk"], "synthetic F7 press triggers hotkey signal")
mon.start_recording()
mon._handle(Ev(e.EV_KEY, e.KEY_A, 1))
mon._handle(Ev(e.EV_KEY, e.KEY_A, 0))
mon._handle(Ev(e.EV_REL, e.REL_X, -3))
mon._handle(Ev(e.EV_KEY, e.KEY_A, 2))  # autorepeat must be ignored
evs = mon.stop_recording()
check(len(evs) == 3 and evs[0][1] == e.EV_KEY and evs[2][3] == -3, "recording captures key+rel, drops autorepeat")

# --- playback ----------------------------------------------------------------
pb = oa.PlaybackThread(fb, [[0, e.EV_KEY, e.KEY_A, 1], [5, e.EV_KEY, e.KEY_A, 0],
                            [5, e.EV_KEY, e.BTN_LEFT, 1], [5, e.EV_KEY, e.BTN_LEFT, 0]], False)
done = []
pb.done.connect(lambda r: done.append(r))
pb.start(); pb.wait(3000)
app.processEvents()
check(done == ["finished"] and len(fb.sent) == 4, "playback replays 4 events routed correctly")
check(fb.sent[2][0] == e.EV_KEY and fb.sent[2][1] == e.BTN_LEFT, "mouse-button routing")

# --- record dialog state machine ----------------------------------------------
rd = oa.RecordDialog(win)
rd.events = [[0, e.EV_KEY, e.KEY_A, 1]]
rd._set_state("idle")
check(rd.btn_play.isEnabled() and not rd.btn_stop_rec.isEnabled(), "record dialog idle state")
rd._set_state("recording")
check(rd.btn_stop_rec.isEnabled() and not rd.btn_play.isEnabled(), "record dialog recording state")
rd.close()

# --- settings persistence ------------------------------------------------------
win.sp_h.setValue(1); win.sp_ms.setValue(250); win.chk_jitter.setChecked(True)
win.close()
cfg = oa.load_config()
check(cfg.get("h") == 1 and cfg.get("ms") == 250 and cfg.get("jitter_on") is True
      and cfg.get("hotkey_code") == e.KEY_F7, "settings persisted to ~/.config/op-autoclicker")

print()
if FAILS:
    print("%d FAILURES: %s" % (len(FAILS), FAILS))
    sys.exit(1)
print("ALL SMOKE TESTS PASSED")
