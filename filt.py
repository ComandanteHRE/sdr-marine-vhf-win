"""Фильтры на чистом numpy (без scipy): проектирование КИХ, прореживание, КИХ с состоянием,
однополюсный ФНЧ (деэмфазис), медианный фильтр. Результаты совпадают с scipy.signal (см. selftest.py)."""
import numpy as np


def firwin(numtaps, cutoff, fs, pass_zero=True):
    """КИХ методом окна (Хэмминг), нормировка как у scipy.signal.firwin.
    cutoff — число или [низ, верх]; pass_zero=False — верхние частоты / полоса."""
    cut = np.atleast_1d(np.asarray(cutoff, dtype=np.float64)) / (fs / 2.0)
    bands = list(cut)
    if pass_zero:
        bands = [0.0] + bands
    if len(bands) % 2:
        bands.append(1.0)
    bands = np.array(bands).reshape(-1, 2)
    m = np.arange(numtaps) - (numtaps - 1) / 2.0
    h = np.zeros(numtaps)
    for lo, hi in bands:
        h += hi * np.sinc(hi * m) - lo * np.sinc(lo * m)
    h *= np.hamming(numtaps)
    lo, hi = bands[0]
    if lo == 0:
        f0 = 0.0
    elif hi == 1:
        f0 = 1.0
    else:
        f0 = (lo + hi) / 2.0
    h /= np.sum(h * np.cos(np.pi * m * f0))
    return h


def decimate_fir(h, ext, m):
    """y[n] = sum_j h[j] * ext[n*m - j] для n = 0 .. ceil(len(ext)/m)-1 (как upfirdn(h, ext, down=m))."""
    n_out = (len(ext) + m - 1) // m
    y = None
    for r in range(m):
        hr = h[r::m]
        if len(hr) == 0:
            continue
        xr = np.concatenate((np.zeros(r, dtype=ext.dtype), ext))[::m]
        part = np.convolve(xr, hr)[:n_out]
        if len(part) < n_out:
            part = np.concatenate((part, np.zeros(n_out - len(part), dtype=part.dtype)))
        y = part if y is None else y + part
    return y


class FirState:
    """КИХ-фильтр с памятью между блоками: y = h * x, длина выхода равна длине входа."""

    def __init__(self, taps, cplx=False):
        self.h = np.asarray(taps, dtype=np.float64)
        self.tail = np.zeros(len(self.h) - 1, dtype=np.complex128 if cplx else np.float64)

    def process(self, x):
        ext = np.concatenate((self.tail, x))
        self.tail = ext[len(x):]
        return np.convolve(ext, self.h, mode="valid")


class OnePole:
    """y[n] = a*x[n] + (1-a)*y[n-1] без Python-цикла по отсчётам (блоки по 256 с замкнутой формой)."""

    CH = 256

    def __init__(self, a):
        self.a, self.r, self.y = float(a), 1.0 - float(a), 0.0
        k = np.arange(self.CH)
        self.rp = self.r ** (k + 1)               # r^(k+1)
        self.rk = self.r ** k                     # r^k
        self.rinv = self.r ** (-k.astype(np.float64))

    def process(self, x):
        out = np.empty(len(x), dtype=np.float64)
        for s in range(0, len(x), self.CH):
            c = np.asarray(x[s:s + self.CH], dtype=np.float64)
            n = len(c)
            # y[k] = r^(k+1)*y0 + a * r^k * sum_{j<=k} x[j] * r^-j
            y = self.rp[:n] * self.y + self.a * self.rk[:n] * np.cumsum(c * self.rinv[:n])
            out[s:s + n] = y
            self.y = y[-1]
        return out


def medfilt(x, k):
    """Медианный фильтр длиной k (нечётное), края — нулями (как scipy.signal.medfilt)."""
    x = np.asarray(x, dtype=np.float64)
    p = k // 2
    xp = np.concatenate((np.zeros(p), x, np.zeros(p)))
    win = np.lib.stride_tricks.sliding_window_view(xp, k)
    return np.median(win, axis=1)
