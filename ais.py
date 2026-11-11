"""АИС: демодуляция GMSK 9600 бод (161.975 / 162.025 МГц), HDLC, CRC-16, NMEA !AIVDM."""
import time
import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from filt import firwin
from dsp import Mixer, make_front_decimators, FIR, fm_demod, AUDIO_FS
from marine_channels import AIS1_HZ, AIS2_HZ

AIS_CENTER_HZ = 162_000_000
SPS = 5                      # 48000 / 9600 отсчётов на символ
DEV_HZ = 2400.0              # девиация GMSK (индекс 0.5)
FLAG = np.array([0, 1, 1, 1, 1, 1, 1, 0], np.uint8)

# ---------------------------------------------------------------- CRC / HDLC
_CRC_TAB = []
for _b in range(256):
    _c = _b
    for _ in range(8):
        _c = (_c >> 1) ^ 0x8408 if _c & 1 else _c >> 1
    _CRC_TAB.append(_c)


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc = (crc >> 8) ^ _CRC_TAB[(crc ^ b) & 0xFF]
    return crc


CRC_GOOD = 0xF0B8     # остаток при верном FCS (X.25 / PPP)


def destuff(bits):
    out, ones = [], 0
    for b in bits:
        if b:
            ones += 1
            if ones > 5:
                return None
            out.append(1)
        else:
            if ones == 5:
                ones = 0
                continue
            ones = 0
            out.append(0)
    return out


def decode_frame(bits):
    """Биты между флагами -> биты сообщения (без FCS) или None."""
    d = destuff(bits.tolist())
    if d is None or len(d) % 8 or len(d) < 56:
        return None
    data = np.packbits(np.array(d, np.uint8), bitorder="little").tobytes()
    if crc16(data) != CRC_GOOD:
        return None
    return np.array(d[:-16], np.uint8)


# допустимые длины полезной части (защита от ложных CRC-совпадений в шуме)
_LEN = {1: (168, 168), 2: (168, 168), 3: (168, 168), 4: (168, 168), 5: (420, 424),
        9: (168, 168), 18: (168, 168), 19: (312, 312), 21: (272, 360),
        24: (160, 168), 27: (96, 168)}


def plausible(bits):
    if len(bits) < 38:
        return False
    t = uint(bits, 0, 6)
    if not 1 <= t <= 27 or uint(bits, 8, 38) == 0:
        return False
    lo, hi = _LEN.get(t, (40, 1064))
    return lo <= len(bits) <= hi


class HdlcPhase:
    """Поиск флагов 0x7E в потоке NRZI-декодированных битов одной фазы."""

    def __init__(self):
        self.buf = np.zeros(0, np.uint8)

    def feed(self, dec):
        b = self.buf = np.concatenate((self.buf, dec))
        if len(b) < 16:
            return []
        w = sliding_window_view(b, 8)
        idx = np.flatnonzero((w == FLAG).all(axis=1))
        frames = []
        for i, j in zip(idx[:-1], idx[1:]):
            n = j - (i + 8)
            if 56 <= n <= 1500:
                f = decode_frame(b[i + 8:j])
                if f is not None and plausible(f):
                    frames.append(f)
        self.buf = b[idx[-1]:] if len(idx) else b[-1600:]
        if len(self.buf) > 4000:
            self.buf = self.buf[-1600:]
        return frames


class AisChannel:
    """Один канал АИС: IQ(1.92 МГц) -> кадры. 5 параллельных фаз символьной синхронизации.

    Детектор: частота после FM-демодулятора минус скользящее среднее за 25 символов
    (убирает расстройку по частоте), знак -> NRZI -> HDLC.
    """
    MEAN_WIN = 125            # 25 символов

    def __init__(self, name, offset_hz):
        self.name = name
        self.mixer = Mixer(offset_hz)
        self.d1, self.d2 = make_front_decimators()
        self.chan = FIR(firwin(101, 10e3, AUDIO_FS), cplx=True)
        self.smooth = FIR(firwin(11, 6.5e3, AUDIO_FS))
        self.mean = FIR(np.ones(self.MEAN_WIN) / self.MEAN_WIN)
        self.prev_x = 1 + 0j
        self.prev_sg = np.zeros(SPS, np.uint8)
        self.phases = [HdlcPhase() for _ in range(SPS)]
        self.rssi_db = -120.0

    def process(self, iq):
        x = self.chan.process(self.d2.process(self.d1.process(self.mixer.mix(iq)))).astype(np.complex64)
        self.rssi_db = 10 * np.log10(float(np.mean(np.abs(x) ** 2)) + 1e-12)
        d, self.prev_x = fm_demod(x, self.prev_x, dev_hz=DEV_HZ)
        d = self.smooth.process(d)
        y = d - self.mean.process(d)
        s = y[: len(y) // SPS * SPS].reshape(-1, SPS)
        out = []
        for p in range(SPS):
            sg = (s[:, p] > 0).astype(np.uint8)
            full = np.concatenate(([self.prev_sg[p]], sg))
            self.prev_sg[p] = sg[-1]
            dec = (full[1:] == full[:-1]).astype(np.uint8)   # NRZI: 1 = нет перехода
            out += self.phases[p].feed(dec)
        return out


class AisDecoder:
    """Оба канала АИС (центр 162.000 МГц). process(iq) -> [{'chan','bits','t'}]."""

    def __init__(self, ppm=0.0):
        self.ch = [AisChannel("A", AIS1_HZ - AIS_CENTER_HZ), AisChannel("B", AIS2_HZ - AIS_CENTER_HZ)]
        self.recent = {}
        self.blk = 0

    def process(self, iq):
        """Дубликаты от разных фаз синхронизации отбрасываются по номеру блока (не по часам)."""
        now, res = time.time(), []
        self.blk += 1
        self.recent = {k: b for k, b in self.recent.items() if self.blk - b <= 3}
        for c in self.ch:
            for bits in c.process(iq):
                key = (c.name, bits.tobytes())
                if key in self.recent:
                    continue
                self.recent[key] = self.blk
                res.append({"chan": c.name, "bits": bits, "t": now})
        return res


# ---------------------------------------------------------------- NMEA
def armor(bits):
    """Биты -> (6-битные символы NMEA, число добавленных нулевых бит)."""
    pad = (-len(bits)) % 6
    b = np.concatenate((bits, np.zeros(pad, np.uint8))).reshape(-1, 6)
    vals = b.dot(np.array([32, 16, 8, 4, 2, 1]))
    return "".join(chr(v + 48 if v < 40 else v + 56) for v in vals), pad


def dearmor(text):
    bits = []
    for c in text:
        v = ord(c) - 48
        v -= 8 if v > 40 else 0
        bits += [(v >> k) & 1 for k in (5, 4, 3, 2, 1, 0)]
    return np.array(bits, np.uint8)


def nmea_checksum(body):
    cs = 0
    for ch in body:
        cs ^= ord(ch)
    return f"{cs:02X}"


_seq = [0]


def nmea_sentences(bits, chan="A", max_chars=60):
    """Биты сообщения -> список строк '!AIVDM,...*CS' (без CRLF)."""
    pay, fill = armor(bits)
    parts = [pay[i:i + max_chars] for i in range(0, len(pay), max_chars)]
    sid = ""
    if len(parts) > 1:
        _seq[0] = (_seq[0] + 1) % 10
        sid = str(_seq[0])
    out = []
    for n, p in enumerate(parts, 1):
        body = f"AIVDM,{len(parts)},{n},{sid},{chan},{p},{fill if n == len(parts) else 0}"
        out.append(f"!{body}*{nmea_checksum(body)}")
    return out


# ---------------------------------------------------------------- разбор сообщений
def uint(bits, a, b):
    v = 0
    for x in bits[a:b]:
        v = (v << 1) | int(x)
    return v


def sint(bits, a, b):
    v = uint(bits, a, b)
    return v - (1 << (b - a)) if v >= 1 << (b - a - 1) else v


def text6(bits, a, b):
    s = ""
    for i in range(a, min(b, len(bits) - 5), 6):
        v = uint(bits, i, i + 6)
        s += chr(v + 64) if v < 32 else chr(v)
    return s.replace("@", " ").strip()


NAV_STATUS = ["ход под машиной", "на якоре", "не управляется", "ограничен в маневре",
              "ограничен осадкой", "ошвартован", "на мели", "рыбалка",
              "ход под парусом", "-", "-", "-", "-", "-", "АИС-SART", "не задан"]


def _pos(bits, a_lon, a_lat):
    lon, lat = sint(bits, a_lon, a_lon + 28) / 600000, sint(bits, a_lat, a_lat + 27) / 600000
    return (lat, lon) if abs(lon) <= 180 and abs(lat) <= 90 else (None, None)


def parse_ais(bits):
    """Основные поля сообщений; всегда есть type и mmsi."""
    t = uint(bits, 0, 6)
    m = {"type": t, "mmsi": uint(bits, 8, 38)}
    try:
        if t in (1, 2, 3):
            m["status"] = NAV_STATUS[uint(bits, 38, 42)]
            m["sog"] = uint(bits, 50, 60) / 10
            m["lat"], m["lon"] = _pos(bits, 61, 89)
            m["cog"] = uint(bits, 116, 128) / 10
        elif t == 5:
            m.update(callsign=text6(bits, 70, 112), name=text6(bits, 112, 232),
                     shiptype=uint(bits, 232, 240), dest=text6(bits, 302, 422))
        elif t in (18, 19):
            m["sog"] = uint(bits, 46, 56) / 10
            m["lat"], m["lon"] = _pos(bits, 57, 85)
            m["cog"] = uint(bits, 112, 124) / 10
            if t == 19:
                m["name"] = text6(bits, 143, 263)
        elif t == 21:
            m["name"] = text6(bits, 43, 163)
            m["lat"], m["lon"] = _pos(bits, 164, 192)
        elif t == 24:
            if uint(bits, 38, 40) == 0:
                m["name"] = text6(bits, 40, 160)
            else:
                m.update(shiptype=uint(bits, 40, 48), callsign=text6(bits, 90, 132))
    except Exception:
        pass
    return m


def describe(m):
    """Короткая строка для журнала."""
    s = f"тип {m['type']:>2}  MMSI {m['mmsi']:09d}"
    if m.get("lat") is not None:
        s += f"  {abs(m['lat']):.4f}°{'N' if m['lat'] >= 0 else 'S'} {abs(m['lon']):.4f}°{'E' if m['lon'] >= 0 else 'W'}"
    if "sog" in m:
        s += f"  {m['sog']:.1f} уз  {m.get('cog', 0):.0f}°"
    if m.get("name"):
        s += f"  «{m['name']}»"
    if m.get("dest"):
        s += f"  → {m['dest']}"
    if m.get("status"):
        s += f"  [{m['status']}]"
    return s
