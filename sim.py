"""Симулятор RTL-SDR: шум + FM-речь на морских каналах + пакеты АИС.

Нужен для самопроверки (selftest.py) и для режима «python app.py --demo» без ключа.
Интерфейс совпадает с нужной частью pyrtlsdr.RtlSdr.
"""
import time
import numpy as np

from dsp import FS
from ais import crc16, FLAG
from marine_channels import ship_hz, coast_hz, AIS1_HZ, AIS2_HZ


# ---------------------------------------------------------------- сообщения АИС
def pack(fields):
    bits = []
    for v, n in fields:
        v = int(v) & ((1 << n) - 1)
        bits += [(v >> k) & 1 for k in range(n - 1, -1, -1)]
    return np.array(bits, np.uint8)


def t6(s, n):
    s = s.upper().ljust(n, "@")[:n]
    return [((ord(c) - 64) if ord(c) >= 64 else ord(c), 6) for c in s]


def msg18(mmsi, lat, lon, sog=5.3, cog=123.0):
    return pack([(18, 6), (0, 2), (mmsi, 30), (0, 8), (int(sog * 10), 10), (1, 1),
                 (int(lon * 600000), 28), (int(lat * 600000), 27), (int(cog * 10), 12),
                 (511, 9), (30, 6), (0, 2), (1, 1), (1, 1), (1, 1), (1, 1), (0, 1), (0, 1), (0, 1), (0, 20)])


def msg5(mmsi, name, call, dest, shiptype=70):
    return pack([(5, 6), (0, 2), (mmsi, 30), (0, 2), (9876543, 30)] + t6(call, 7) + t6(name, 20) +
                [(shiptype, 8), (100, 9), (20, 9), (10, 6), (5, 6), (1, 4), (11, 4), (2, 5), (12, 5), (30, 6),
                 (65, 8)] + t6(dest, 20) + [(0, 1), (0, 1)])


GPSD_T1 = "177KQJ5000G?tO`K>RA1wUbN0TKH"      # пример из документации gpsd, MMSI 477553000


def stuff(bits):
    out, ones = [], 0
    for b in bits:
        out.append(int(b))
        ones = ones + 1 if b else 0
        if ones == 5:
            out.append(0)
            ones = 0
    return np.array(out, np.uint8)


def frame_bits(payload):
    data = np.packbits(payload, bitorder="little").tobytes()
    crc = crc16(data) ^ 0xFFFF
    allb = np.unpackbits(np.frombuffer(data + bytes([crc & 255, crc >> 8]), np.uint8), bitorder="little")
    pre = np.tile(np.array([0, 1], np.uint8), 12)
    return np.concatenate((pre, FLAG, stuff(allb), FLAG, np.zeros(8, np.uint8)))


def ais_burst_iq(payload, amp=0.05, f_off_hz=0.0, pad=2000):
    """GMSK (BT 0.4, 9600 бод, девиация ±2.4 кГц) на частоте 1.92 МГц, база в нуле."""
    fb = frame_bits(payload)
    levels = (1 + np.cumsum(fb == 0)) % 2                # NRZI: 0 = смена уровня
    sym = np.repeat(levels * 2.0 - 1.0, 5)               # 48 кГц
    sigma = np.sqrt(np.log(2)) / (2 * np.pi * 0.4) * 5   # в отсчётах
    k = np.arange(-10, 11)
    g = np.exp(-0.5 * (k / sigma) ** 2)
    g /= g.sum()
    f = np.convolve(sym, g, mode="same") * 2400.0
    f40 = np.repeat(f, 40)
    ph = 2 * np.pi * np.cumsum(f40 + f_off_hz) / FS
    env = np.ones(len(ph))
    r = 400
    env[:r], env[-r:] = np.linspace(0, 1, r), np.linspace(1, 0, r)
    iq = amp * env * np.exp(1j * ph)
    return np.concatenate((np.zeros(pad), iq, np.zeros(pad))).astype(np.complex64)


# ---------------------------------------------------------------- источники сигнала
class Voice:
    def __init__(self, freq_hz, amp=0.15, tone=900.0, dev=3000.0, period=5.0, on=1.5, phase=0.0):
        self.freq, self.amp, self.tone, self.dev = freq_hz, amp, tone, dev
        self.period, self.on, self.phase = period, on, phase

    def render(self, n0, n):
        t = (n0 + np.arange(n)) / FS
        gate = ((t + self.phase) % self.period) < self.on
        if not gate.any():
            return None
        ph = -(self.dev / self.tone) * np.cos(2 * np.pi * self.tone * t)
        return (self.amp * gate * np.exp(1j * ph)).astype(np.complex64)


class AisSource:
    def __init__(self, freq_hz, payloads, interval=1.3, amp=0.05, f_off_hz=0.0, start=0.3):
        self.freq, self.interval, self.start = freq_hz, interval, start
        self.bursts = [ais_burst_iq(p, amp, f_off_hz) for p in payloads]

    def render(self, n0, n):
        out, hit = np.zeros(n, np.complex64), False
        # какие передачи пересекают блок [n0, n0+n)
        for i, b in enumerate(self.bursts):
            period = self.interval * len(self.bursts) * FS
            base = int((self.start + i * self.interval) * FS)
            m = max(0, (n0 - base - len(b)) // int(period))
            while True:
                s = base + m * int(period)
                if s >= n0 + n:
                    break
                lo, hi = max(s, n0), min(s + len(b), n0 + n)
                if hi > lo:
                    out[lo - n0:hi - n0] += b[lo - s:hi - s]
                    hit = True
                m += 1
        return out if hit else None


def demo_scene(ais_amp=0.05, ais_off_hz=0.0):
    t1 = pack_gpsd()
    ais_msgs = [t1, msg18(244630001, 52.3702, 4.8952), msg5(244630001, "DEMO ROTTERDAM", "PH1234", "HAMBURG")]
    return [Voice(ship_hz(16), amp=0.15, tone=900, period=6.0, on=2.0),
            Voice(ship_hz(12), amp=0.04, tone=600, period=9.0, on=1.5, phase=2.0),
            Voice(coast_hz(22), amp=0.10, tone=1200, period=11.0, on=2.5, phase=4.0),
            Voice(ship_hz(6), amp=0.08, tone=700, period=13.0, on=1.0, phase=7.0),
            AisSource(AIS1_HZ, ais_msgs, interval=0.8, amp=ais_amp, f_off_hz=ais_off_hz),
            AisSource(AIS2_HZ, ais_msgs[::-1], interval=1.1, amp=ais_amp, f_off_hz=ais_off_hz, start=0.7)]


def pack_gpsd():
    from ais import dearmor
    return dearmor(GPSD_T1)


class SimSdr:
    """Подмена RtlSdr: шум + источники. realtime=True — выдаёт данные в темпе эфира."""

    def __init__(self, sources=None, noise=0.01, realtime=False, seed=1):
        self.sample_rate = FS
        self.center_freq = 156.8e6
        self.gain = "auto"
        self.freq_correction = 0
        self.sources = sources if sources is not None else demo_scene()
        self.noise, self.realtime = noise, realtime
        self.rng = np.random.default_rng(seed)
        self.n = 0
        self._t = time.time()

    def read_samples(self, num):
        n0 = self.n
        x = (self.rng.standard_normal(num) + 1j * self.rng.standard_normal(num)).astype(np.complex64) * self.noise
        idx = None
        for s in self.sources:
            d = s.freq - self.center_freq
            if abs(d) > FS * 0.47:
                continue
            b = s.render(n0, num)
            if b is None:
                continue
            if idx is None:
                idx = n0 + np.arange(num)
            x += b * np.exp(2j * np.pi * d * idx / FS).astype(np.complex64)
        self.n += num
        if self.realtime:
            wait = self._t + num / FS - time.time()
            self._t += num / FS
            if wait > 0:
                time.sleep(wait)
            elif wait < -1:
                self._t = time.time()
        return x

    def close(self):
        pass
