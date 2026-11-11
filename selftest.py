"""Самопроверка без железа: python selftest.py  (использует симулятор sim.py)."""
import queue
import socket
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

import ais
import marine_channels as mc
from audio import Mp3Recorder
from netout import NmeaOutput
from radio import Receiver
from sim import SimSdr, demo_scene, pack_gpsd, GPSD_T1

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
bad = 0


def check(name, cond, extra=""):
    global bad
    bad += 0 if cond else 1
    print(f"[{OK if cond else FAIL}] {name} {extra}")


def drain(q, secs, kinds):
    out, t0 = [], time.time()
    while time.time() - t0 < secs:
        try:
            k, d = q.get(timeout=0.2)
        except queue.Empty:
            continue
        if k == "error":
            print("ERROR:", d)
        if k in kinds:
            out.append((k, d))
    return out


def run_rx(mode, secs, freq=None, scene=None, nmea=None, audio=None, rec=None, cfg=None):
    q = queue.Queue()
    sdr = SimSdr(scene if scene is not None else demo_scene(), realtime=False)
    rx = Receiver(lambda: sdr, q, audio=audio, recorder=rec, nmea=nmea, cfg=cfg or {})
    rx.start()
    if freq:
        rx.tune(freq)
    rx.set_mode(mode)
    ev = drain(q, secs, {"voice", "scan_done", "ais", "mode"})
    rx.stop()
    rx.join(2)
    return ev


# 1. таблица каналов
check("канал 16 = 156.800", mc.ship_hz(16) == 156_800_000)
check("канал 70 = 156.525", mc.ship_hz(70) == 156_525_000)
check("канал 1 береговая 160.650", mc.coast_hz(1) == 160_650_000)
check("канал 88 = 157.425", mc.ship_hz(88) == 157_425_000)
check("▲ с 28 -> 60, ▼ с 1 -> 88", mc.next_channel(28, 1) == 60 and mc.next_channel(1, -1) == 88)

# 1b. логика панели
from panel_logic import PanelLogic
p = PanelLogic()
for k in "09":
    p.key(k)
p.enter()
ok1 = p.channel == 9
p.step(1)
ok2 = p.channel == 10
p.toggle_input()
for k in "161975":
    p.key(k)
p.enter()
ok3 = p.freq_hz == 161_975_000
p.toggle_input()
p.ch16()
check("панель: ввод канала, ▲, ввод частоты (кГц), кнопка 16", ok1 and ok2 and ok3 and p.channel == 16)

# 2. NMEA
s = ais.nmea_sentences(pack_gpsd(), "B")[0]
check("NMEA из примера gpsd (контрольная сумма 5C)", s == f"!AIVDM,1,1,,B,{GPSD_T1},0*5C", s)

# 3. голос + шумоподавитель
class Cap:
    def __init__(self):
        self.blocks = []
    def push(self, b):
        self.blocks.append(b)
    def clear(self):
        pass

cap = Cap()
ev = run_rx("VOICE", 3.0, freq=mc.ship_hz(16), audio=cap, cfg={"squelch": 3})
opened = [d["open"] for k, d in ev if k == "voice"]
check("шумоподавитель открывается на канале 16", any(opened) and not all(opened),
      f"(открыт {sum(opened)}/{len(opened)} блоков)")
aud = np.concatenate(cap.blocks) if cap.blocks else np.zeros(1)
spec = np.abs(np.fft.rfft(aud * np.hanning(len(aud))))
pk = np.fft.rfftfreq(len(aud), 1 / 48000)[spec.argmax()]
check("демодулированный тон 900 Гц", abs(pk - 900) < 15, f"(пик {pk:.0f} Гц)")

# 4. обзор диапазона
ev = run_rx("SCAN", 6.0)
done = [d for k, d in ev if k == "scan_done"]
act = {}
for cyc in done:
    for r in cyc:
        if r["active"]:
            act[(r["ch"], r["ship"])] = max(act.get((r["ch"], r["ship"]), 0), r["db"])
want = {(16, True), (12, True), (22, False), (6, True)}
check("обзор находит каналы 16, 12, 6 и береговой 22", want <= set(act), f"({len(done)} циклов; найдено {sorted(map(str, act))})")
check("ложных срабатываний нет", set(act) <= want, "" if set(act) <= want else f"лишние: {set(act) - want}")

# 5. АИС + TCP/UDP
out = NmeaOutput(tcp_port=20110, udp_port=20111)
out.start()
tcp = socket.create_connection(("127.0.0.1", 20110))
tcp.settimeout(0.5)
udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
udp.bind(("127.0.0.1", 20111))
udp.settimeout(0.5)
time.sleep(0.3)
ev = run_rx("AIS", 7.0, nmea=out)
msgs = [d for k, d in ev if k == "ais"]
types = sorted({d["info"]["type"] for d in msgs})
check("АИС декодирует типы 1, 5, 18", types == [1, 5, 18], f"(всего {len(msgs)} сообщений)")
tcp_data = b""
try:
    while True:
        c = tcp.recv(65536)
        if not c:
            break
        tcp_data += c
except socket.timeout:
    pass
udp_n = 0
try:
    while True:
        udp.recv(2048)
        udp_n += 1
except socket.timeout:
    pass
lines = tcp_data.decode().split("\r\n")
lines = [l for l in lines if l]
good = all(l.startswith("!AIVDM") and l.split("*")[1] == ais.nmea_checksum(l[1:].split("*")[0]) for l in lines)
check("TCP: строки NMEA с верной контрольной суммой", len(lines) >= len(msgs) and good, f"({len(lines)} строк)")
check("UDP: те же строки пришли датаграммами", udp_n == len(lines), f"({udp_n})")
m5 = [d for d in msgs if d["info"]["type"] == 5]
check("тип 5: название судна разобрано и разбит на 2 фрагмента",
      bool(m5) and m5[0]["info"]["name"] == "DEMO ROTTERDAM" and len(m5[0]["sentences"]) == 2,
      f"({m5[0]['info'].get('name') if m5 else None})")
out.close()

# 6. запись MP3
rec = Mp3Recorder("/tmp/marine_test_rec")
if rec.available():
    p = rec.start("test")
    t = np.arange(48000 * 2) / 48000
    tone = (0.3 * np.sin(2 * np.pi * 800 * t)).astype(np.float32)
    for i in range(0, len(tone), 3200):
        rec.write(tone[i:i + 3200])
    rec.stop()
    import shutil
    dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                         capture_output=True, text=True).stdout.strip() if shutil.which("ffprobe") else ""
    check("MP3-файл записан", Path(p).exists() and Path(p).stat().st_size > 2000,
          f"({Path(p).stat().st_size} байт, {dur or '?'} с)")
else:
    print("[пропуск] нет lameenc/ffmpeg")

# ---- раскладка интерфейса: вертикальный и горизонтальный вид, любые размеры окна
import ui
for (W, H) in ((340, 480), (420, 760), (560, 900), (1000, 1000), (1100, 700), (700, 420), (1600, 900)):
    L = ui.make_layout(W, H)
    rects = list(L.keys.items())
    inside = all(r.x0 >= 0 and r.y0 >= 0 and r.x1 <= W and r.y1 <= H and r.w > 8 and r.h > 8 for _, r in rects)
    inside &= all(L.chassis.x0 <= r.x0 and r.x1 <= L.chassis.x1 and r.y1 <= L.chassis.y1 for _, r in rects)
    overlap = [(a, b) for i, (a, ra) in enumerate(rects) for b, rb in rects[i + 1:]
               if ra.x0 < rb.x1 - 0.5 and rb.x0 < ra.x1 - 0.5 and ra.y0 < rb.y1 - 0.5 and rb.y0 < ra.y1 - 0.5]
    hits = all(ui.hit(L, r.cx, r.cy) == ("key", n) for n, r in rects)
    check(f"раскладка {W}x{H} ({'гориз.' if L.land else 'вертик.'}): {len(rects)} кнопок в рамках, без наложений, "
          f"попадание мышью", inside and not overlap and hits and L.plot.w > 100 and L.plot.h > 40, str(overlap[:2]))

# ---- собственные фильтры совпадают со scipy (если scipy установлен на машине разработчика)
try:
    from scipy import signal as _sg
    import filt
    _x = (np.random.default_rng(1).standard_normal(6400) + 0j)
    _h = _sg.firwin(101, 8e3, fs=48e3)
    check("filt.firwin == scipy", np.abs(_h - filt.firwin(101, 8e3, 48e3)).max() < 1e-12)
    check("filt.FirState == scipy.lfilter", np.abs(_sg.lfilter(_h, 1, _x) - filt.FirState(_h, True).process(_x)).max() < 1e-9)
except ImportError:
    print("[пропуск] scipy не установлен — сравнение фильтров со scipy не выполняется")

print("\nИТОГ:", "всё в порядке" if not bad else f"провалено проверок: {bad}")
sys.exit(1 if bad else 0)
