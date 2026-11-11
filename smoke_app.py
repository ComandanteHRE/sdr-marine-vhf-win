"""Дымовой тест App без экрана: подменяет tkinter заглушкой, прогоняет события, мышь, клавиши, слои."""
import sys, types, queue, time
from unittest.mock import MagicMock


class FakeW:
    def __init__(self, *a, **k): self._w, self._h = 560, 900
    def __getattr__(self, n):
        if n.startswith("__"): raise AttributeError(n)
        return MagicMock()
    def winfo_width(self): return self._w
    def winfo_height(self): return self._h
    def state(self): return "normal"
    def geometry(self, *a): return "560x900+0+0"
    def after(self, ms, fn=None, *a): return 1
    def pack(self, *a, **k): pass


tkm = types.ModuleType("tkinter")
tkm.Tk = type("Tk", (FakeW,), {})
tkm.Canvas = FakeW; tkm.Toplevel = FakeW; tkm.Entry = tkm.Text = type("X", (), {})
tkm.TclError = Exception
for n in ("Frame", "Button", "Label", "Checkbutton", "StringVar", "BooleanVar", "Scrollbar"): setattr(tkm, n, MagicMock())
tkm.messagebox = types.SimpleNamespace(showerror=lambda *a, **k: None)
sys.modules["tkinter"] = tkm
sys.modules["tkinter.messagebox"] = tkm.messagebox
fm = types.ModuleType("tkinter.font")
fm.Font = lambda **k: types.SimpleNamespace(measure=lambda t: 7 * len(t))
sys.modules["tkinter.font"] = fm
sys.argv = ["app.py", "--demo", "--ais"]
import app as A

args = A.parse_args()
ev = queue.Queue()
inst = types.SimpleNamespace(primary=True)
A.CFG_PATH = A.Path("/tmp/claude-0/test_cfg.json")
a = A.App(args, ev, inst)
a.cv._w, a.cv._h = 560, 900
a._relayout()
E = lambda x, y: types.SimpleNamespace(x=x, y=y, delta=120, widget=None, char="", keysym="")
L = a.L
def click(name):
    r = L.keys.get(name) or L.chips[name]
    a._press(E(r.cx, r.cy)); assert a.pressed == name, name
    a._release(E(r.cx, r.cy))
for n in ("k1", "k2", "kEnt", "b16", "bUp", "bDown", "bMode", "k1", "k5", "k6", "kDot", "k5", "kEnt", "bMode", "kClr", "bScan", "bScan", "bAis", "bRec", "bRec", "cSide", "cAuto", "cAuto"):
    click(n)
time.sleep(1.5)
a._poll()
print("mode", a.mode, "connected", a.connected, "error", a.error[:60])
# слайдеры
r = L.sl_vol; a._press(E(r.cx, (r.y0 + r.y1) / 2)); a._motion(E(r.cx, r.y0 + 10)); a._release(E(r.cx, r.y0 + 10)); print("vol", a.vol)
a._wheel(E(L.sl_sql.cx, L.sl_sql.cy), 1); print("sql", a.sql)
# сканирование и клик по графику
a.scanning = True; a._sync_radio()
t0 = time.time()
while time.time() - t0 < 25 and not a.found:
    time.sleep(0.3); a._poll()
print("scan found", a.found, "data points", len(a.scan_data))
a.scanning = False; a._sync_radio()
x0, x1 = A.ui.chart_cols(L)[CH_I := A.CHANNELS.index(16)]
a._press(E((x0 + x1) / 2, L.plot.cy)); a._release(E(0, 0)); print("после клика по графику:", a.logic.channel)
# АИС
a.ais_on = True; a._sync_radio()
t0 = time.time()
while time.time() - t0 < 30 and a.ais_count < 3:
    time.sleep(0.3); a._poll()
print("ais_count", a.ais_count, "lines", len(a.ais_log), "footer:", a.footer[:90])
# смена ориентации
a.cv._w, a.cv._h = 900, 600; a._relayout(); a._poll(); print("land:", a.L.land)
a.cv._w, a.cv._h = 340, 480; a._relayout(); a._poll(); print("small portrait:", not a.L.land)
a._close(); print("OK")
