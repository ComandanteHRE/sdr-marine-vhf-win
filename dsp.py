"""DSP: децимация, узкополосный FM-приёмник (NFM) с шумоподавителем, обзор спектра."""
import numpy as np
import filt
from filt import firwin, OnePole, FirState, decimate_fir, medfilt

FS = 1_920_000          # частота дискретизации RTL-SDR (комплексных отсчётов/с)
AUDIO_FS = 48_000       # FS / 40
BLOCK = 128_000         # отсчётов в блоке (66.7 мс): кратно 40 и 256
AUDIO_BLOCK = BLOCK // 40
VOICE_OFFSET_HZ = 250_000   # сдвиг настройки, чтобы пик DC RTL-SDR не попал на канал

# Типичный уровень «шумовой» метрики шумоподавителя при пустом канале (см. selftest.py)
NOISE_REF = 0.94


class Mixer:
    """Перенос частоты freq_hz в ноль; блок фиксированной длины, фаза непрерывна."""

    def __init__(self, freq_hz, n=BLOCK, fs=FS):
        w = -2 * np.pi * freq_hz / fs
        self.tab = np.exp(1j * w * np.arange(n)).astype(np.complex64)
        self.step = np.exp(1j * w * n)
        self.ph = 1 + 0j

    def mix(self, x):
        y = x * self.tab
        y *= np.complex64(self.ph)
        self.ph *= self.step
        self.ph /= abs(self.ph)
        return y


class Decimator:
    """КИХ-прореживание с сохранением состояния между блоками (полифазно)."""

    def __init__(self, taps, m):
        assert len(taps) % m == 0
        self.h = np.asarray(taps, dtype=np.float64)
        self.m = m
        self.tail = np.zeros(len(self.h), np.complex64)

    def process(self, x):
        n = len(x)
        assert n % self.m == 0 and n >= len(self.h)
        ext = np.concatenate((self.tail, x))
        self.tail = ext[-len(self.h):]
        y = decimate_fir(self.h, ext, self.m)
        j0 = len(self.h) // self.m
        return y[j0:j0 + n // self.m].astype(np.complex64)


FIR = FirState                       # КИХ с состоянием (filt.py)


def make_front_decimators():
    """1.92 МГц -> 240 кГц -> 48 кГц."""
    return (Decimator(firwin(64, 80e3, FS), 8),
            Decimator(firwin(60, 14e3, FS / 8), 5))


def fm_demod(x, prev, dev_hz=5000.0, fs=AUDIO_FS):
    """Квадратурный FM-демодулятор. Возвращает (звук, последний отсчёт)."""
    xx = np.concatenate((np.array([prev], dtype=x.dtype), x))
    d = np.angle(xx[1:] * np.conj(xx[:-1]))
    return (d * (fs / (2 * np.pi * dev_hz))).astype(np.float64), x[-1]


class NfmVoice:
    """Тракт приёма речи: IQ(1.92 МГц) -> звук 48 кГц, моно.

    Шумоподавитель шумовой: мерится энергия демодулированного сигнала в полосе
    5.5-8 кГц (в пустом канале она велика, при несущей падает).
    """

    DEEMPH_HZ = 700.0

    def __init__(self, offset_hz=VOICE_OFFSET_HZ):
        self.mixer = Mixer(offset_hz)
        self.d1, self.d2 = make_front_decimators()
        self.chan = FIR(firwin(101, 8e3, AUDIO_FS), cplx=True)
        self.noise_hp = FIR(firwin(63, 5.5e3, AUDIO_FS, pass_zero=False))
        self.audio_bp = FIR(firwin(127, [300, 3400], AUDIO_FS, pass_zero=False))
        a = 1 - np.exp(-2 * np.pi * self.DEEMPH_HZ / AUDIO_FS)
        self.deemph = OnePole(a)
        self.prev = 1 + 0j
        self.agc = 8.0
        self.env = 0.0
        self.is_open = False
        self.gain_prev = 0.0
        self.hang = 0

    def process(self, iq, sql_level=3):
        """Возвращает (audio float32[AUDIO_BLOCK], info)."""
        x = self.d2.process(self.d1.process(self.mixer.mix(iq)))
        x = self.chan.process(x).astype(np.complex64)

        p_db = 10 * np.log10(float(np.mean(np.abs(x) ** 2)) + 1e-12)
        dem, self.prev = fm_demod(x, self.prev)
        noise = float(np.sqrt(np.mean(self.noise_hp.process(dem) ** 2)))
        # «заглушение» шума несущей, дБ (0 — пустой канал): шкала S-метра, не зависит от усиления
        rssi = float(np.clip(-20 * np.log10(max(noise, 1e-6) / NOISE_REF), 0, 45))

        if sql_level <= 0:
            opened = True
        else:
            thr = NOISE_REF * (1.0 - 0.085 * sql_level)
            opened = noise < (thr * 1.2 if self.is_open else thr)
        if opened:
            self.hang = 4                     # ~0.27 с «хвоста»
        elif self.hang > 0:
            self.hang -= 1
            opened = True
        self.is_open = opened

        y = self.audio_bp.process(dem)
        y = self.deemph.process(y)
        if opened:
            rms = float(np.sqrt(np.mean(y ** 2)))
            self.env = max(rms, 0.9 * self.env)
            want = float(np.clip(0.18 / max(self.env, 1e-3), 0.5, 40.0))
            self.agc += 0.4 * (want - self.agc)
        g_end = 1.0 if opened else 0.0
        ramp = np.ones(len(y)) * g_end
        r = 160
        ramp[:r] = np.linspace(self.gain_prev, g_end, r)
        self.gain_prev = g_end
        out = np.clip(y * self.agc * ramp, -0.95, 0.95).astype(np.float32)
        return out, {"rssi": rssi, "level_db": p_db, "open": self.is_open, "noise": noise}


# ---------------------------------------------------------------- обзор спектра
NFFT = 8192
_WIN = np.hanning(NFFT).astype(np.float32)


def psd_block(iq):
    """Усреднённый по блоку спектр мощности (fftshift), сумма по сегментам."""
    n = len(iq) // NFFT
    seg = iq[:n * NFFT].reshape(n, NFFT) * _WIN
    s = np.fft.fft(seg, axis=1)
    return np.fft.fftshift((s.real ** 2 + s.imag ** 2).sum(axis=0)), n


def channel_power_db(psd, center_hz, freqs_hz, bw_hz=14_000):
    """Мощность (дБ, отн.) в полосе bw вокруг каждой частоты из freqs_hz."""
    cs = np.concatenate(([0.0], np.cumsum(psd)))
    binhz = FS / NFFT
    half = int(round(bw_hz / 2 / binhz))
    out = []
    for f in freqs_hz:
        c = int(round(NFFT / 2 + (f - center_hz) / binhz))
        lo, hi = max(0, c - half), min(NFFT, c + half + 1)
        out.append(10 * np.log10(max(cs[hi] - cs[lo], 1e-30)))
    return np.array(out)


def excess_over_floor(power_db, kernel=15):
    """Превышение над локальным медианным «полом» (устойчиво к наклону АЧХ)."""
    k = min(kernel, len(power_db) // 2 * 2 - 1)
    floor = medfilt(power_db, k)
    return power_db - floor
