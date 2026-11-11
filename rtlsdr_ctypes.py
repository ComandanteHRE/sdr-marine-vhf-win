"""Минимальная обёртка над librtlsdr через ctypes (вместо pyrtlsdr).

Зачем: загружает ту DLL, которую вы положили рядом с программой, по полному пути, и не требует
функций, которых нет в вашей сборке librtlsdr. Ошибки — с понятными причинами.
"""
import ctypes
import ctypes.util
import os
import sys
from pathlib import Path

import numpy as np

_NAMES = ["librtlsdr.dll", "rtlsdr.dll"] if os.name == "nt" else ["librtlsdr.so.2", "librtlsdr.so.0", "librtlsdr.so"]


def _candidates(search_dirs):
    out = []
    for d in search_dirs:
        for n in _NAMES:
            out += [Path(d) / n, Path(d) / "dlls" / n]
    return [p for p in out if p.exists()]


def load_library(search_dirs=(), lib_path=None):
    """(CDLL, путь). Порядок: явный путь -> папки программы -> системный поиск."""
    tried, paths = [], ([Path(lib_path)] if lib_path else _candidates(search_dirs))
    for p in paths:
        try:
            if os.name == "nt" and hasattr(os, "add_dll_directory"):
                os.add_dll_directory(str(p.parent))
            return ctypes.CDLL(str(p)), str(p)
        except OSError as e:
            tried.append(f"{p}: {e}")
            if "193" in str(e):
                raise RuntimeError(f"{p.name} другой разрядности (нужна 64-bit, как у программы)") from e
            if "126" in str(e):
                raise RuntimeError(f"{p.name} не загрузилась: рядом нет libusb-1.0.dll (и др. DLL из того же архива)") from e
    for n in ("rtlsdr", "librtlsdr"):
        found = ctypes.util.find_library(n)
        if found:
            try:
                return ctypes.CDLL(found), found
            except OSError as e:
                tried.append(f"{found}: {e}")
    where = ", ".join(str(d) for d in search_dirs)
    raise RuntimeError("не найдена librtlsdr.dll — положите её (и libusb-1.0.dll) рядом с программой"
                       f" [искали в: {where}]" + (f"; ошибки: {'; '.join(tried)}" if tried else ""))


class RtlSdrDev:
    """Интерфейс, совместимый с нужной частью pyrtlsdr.RtlSdr."""

    def __init__(self, device_index=0, search_dirs=(), lib_path=None):
        L, self.dll_path = load_library(search_dirs, lib_path)
        self.L = L
        c, vp, ci, cu = ctypes, ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32
        sig = {"rtlsdr_get_device_count": (cu, []), "rtlsdr_get_device_name": (ctypes.c_char_p, [cu]),
               "rtlsdr_open": (ci, [c.POINTER(vp), cu]), "rtlsdr_close": (ci, [vp]),
               "rtlsdr_set_sample_rate": (ci, [vp, cu]), "rtlsdr_set_center_freq": (ci, [vp, cu]),
               "rtlsdr_set_freq_correction": (ci, [vp, ci]), "rtlsdr_set_tuner_gain_mode": (ci, [vp, ci]),
               "rtlsdr_get_tuner_gains": (ci, [vp, c.POINTER(ci)]), "rtlsdr_set_tuner_gain": (ci, [vp, ci]),
               "rtlsdr_reset_buffer": (ci, [vp]),
               "rtlsdr_read_sync": (ci, [vp, vp, ci, c.POINTER(ci)])}
        for name, (res, args) in sig.items():
            try:
                f = getattr(L, name)
            except AttributeError as e:
                raise RuntimeError(f"в {Path(self.dll_path).name} нет функции {name}: DLL не librtlsdr?") from e
            f.restype, f.argtypes = res, args
        self._agc = getattr(L, "rtlsdr_set_agc_mode", None)          # необязательная
        if self._agc:
            self._agc.restype, self._agc.argtypes = ci, [vp, ci]

        n = L.rtlsdr_get_device_count()
        if n == 0:
            raise RuntimeError("RTL-SDR не найден: проверьте USB и драйвер WinUSB (Zadig) для «Bulk-In, Interface (Interface 0)»")
        if device_index >= n:
            raise RuntimeError(f"нет ключа с номером {device_index} (найдено устройств: {n})")
        name = L.rtlsdr_get_device_name(device_index)
        self.dev = vp()
        r = L.rtlsdr_open(c.byref(self.dev), device_index)
        if r != 0:
            raise RuntimeError(f"не удалось открыть ключ (код {r}): занят другой программой (SDR#, rtl_tcp)?")
        self.info = f"{(name or b'RTL-SDR').decode(errors='replace')} · {self.dll_path}"
        self._rate = self._freq = 0
        self._gain, self._ppm = "auto", 0
        ng = L.rtlsdr_get_tuner_gains(self.dev, None)
        arr = (ci * max(ng, 1))()
        self.valid_gains = sorted(arr[:L.rtlsdr_get_tuner_gains(self.dev, arr)]) if ng > 0 else []

    def _chk(self, what, r):
        if r != 0:
            raise RuntimeError(f"{what}: ошибка librtlsdr {r}")

    @property
    def sample_rate(self):
        return self._rate

    @sample_rate.setter
    def sample_rate(self, v):
        self._chk("sample_rate", self.L.rtlsdr_set_sample_rate(self.dev, int(v)))
        self._rate = int(v)

    @property
    def center_freq(self):
        return self._freq

    @center_freq.setter
    def center_freq(self, v):
        self._chk("center_freq", self.L.rtlsdr_set_center_freq(self.dev, int(round(v))))
        self._freq = int(round(v))

    @property
    def freq_correction(self):
        return self._ppm

    @freq_correction.setter
    def freq_correction(self, ppm):
        r = self.L.rtlsdr_set_freq_correction(self.dev, int(ppm))
        if r not in (0, -2):                       # -2: значение не изменилось
            self._chk("freq_correction", r)
        self._ppm = ppm

    @property
    def gain(self):
        return self._gain

    @gain.setter
    def gain(self, g):
        if g == "auto":
            self._chk("gain", self.L.rtlsdr_set_tuner_gain_mode(self.dev, 0))
            if self._agc:
                self._agc(self.dev, 1)
        else:
            self._chk("gain", self.L.rtlsdr_set_tuner_gain_mode(self.dev, 1))
            tenth = int(round(float(g) * 10))
            if self.valid_gains:
                tenth = min(self.valid_gains, key=lambda x: abs(x - tenth))
            self._chk("gain", self.L.rtlsdr_set_tuner_gain(self.dev, tenth))
            if self._agc:
                self._agc(self.dev, 0)
        self._gain = g

    def read_samples(self, n):
        """n комплексных отсчётов, complex64 в диапазоне [-1, 1)."""
        total, buf = 2 * n, (ctypes.c_ubyte * (2 * n))()
        if not getattr(self, "_started", False):
            self.L.rtlsdr_reset_buffer(self.dev)
            self._started = True
        got, nread = 0, ctypes.c_int(0)
        while got < total:
            ptr = ctypes.c_void_p(ctypes.addressof(buf) + got)
            r = self.L.rtlsdr_read_sync(self.dev, ptr, total - got, ctypes.byref(nread))
            if r != 0 or nread.value <= 0:
                raise RuntimeError(f"чтение с ключа прервано (код {r}): ключ отключён?")
            got += nread.value
        a = np.frombuffer(buf, dtype=np.uint8).astype(np.float32)
        a = (a - 127.5) / 127.5
        return (a[0::2] + 1j * a[1::2]).astype(np.complex64)

    def close(self):
        if self.dev:
            self.L.rtlsdr_close(self.dev)
            self.dev = None
