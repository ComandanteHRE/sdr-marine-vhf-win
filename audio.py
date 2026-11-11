"""Вывод звука (sounddevice) и запись в MP3 (lameenc, запасной вариант — ffmpeg)."""
import collections
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path

import numpy as np

try:
    import sounddevice as sd
except Exception as e:                       # нет PortAudio / модуля
    sd, SD_ERROR = None, e

try:
    import lameenc
except Exception:
    lameenc = None

AUDIO_FS = 48_000


class AudioOut:
    """Кольцевая очередь блоков float32 -> звуковая карта."""

    def __init__(self):
        self.q = collections.deque()
        self.lock = threading.Lock()
        self.volume = 0.6
        self.stream = None
        self.error = ""
        self.cur = np.zeros(0, np.float32)

    def start(self):
        if sd is None:
            self.error = f"нет звука: {SD_ERROR}"
            return
        try:
            self.stream = sd.OutputStream(samplerate=AUDIO_FS, channels=1, dtype="float32",
                                          blocksize=1024, callback=self._cb)
            self.stream.start()
        except Exception as e:
            self.error = f"нет звука: {e}"
            self.stream = None

    def _cb(self, out, frames, _t, _s):
        need, buf = frames, []
        with self.lock:
            while need > 0:
                if len(self.cur) == 0:
                    if not self.q:
                        break
                    self.cur = self.q.popleft()
                take = self.cur[:need]
                buf.append(take)
                self.cur = self.cur[len(take):]
                need -= len(take)
        data = np.concatenate(buf) if buf else np.zeros(0, np.float32)
        out[:, 0] = 0
        out[:len(data), 0] = np.clip(data * 2.0 * self.volume ** 2, -1, 1)   # квадратичная «громкость»

    def push(self, block):
        with self.lock:
            self.q.append(block)
            while len(self.q) > 15:                 # не копим задержку > ~1 с
                self.q.popleft()

    def clear(self):
        with self.lock:
            self.q.clear()
            self.cur = np.zeros(0, np.float32)

    def stop(self):
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None


class Mp3Recorder:
    """Пишет моно 48 кГц в .mp3. Кодирует в фоне, чтобы не тормозить приём."""

    def __init__(self, folder="recordings", bitrate=64):
        self.folder, self.bitrate = Path(folder), bitrate
        self.active = False
        self.path = None
        self.q = queue.Queue()
        self.t0 = 0.0
        self.error = ""
        self._enc = self._proc = self._fh = self._thr = None

    @staticmethod
    def available():
        return lameenc is not None or shutil.which("ffmpeg") is not None

    def start(self, label=""):
        if self.active:
            return self.path
        if not self.available():
            self.error = "нет кодера MP3: pip install lameenc (или установите ffmpeg)"
            return None
        self.folder.mkdir(parents=True, exist_ok=True)
        name = time.strftime("%Y%m%d_%H%M%S") + (f"_{label}" if label else "") + ".mp3"
        self.path = self.folder / name
        self._fh = open(self.path, "wb")
        if lameenc is not None:
            self._enc = lameenc.Encoder()
            self._enc.set_bit_rate(self.bitrate)
            self._enc.set_in_sample_rate(AUDIO_FS)
            self._enc.set_channels(1)
            self._enc.set_quality(2)
        else:
            self._proc = subprocess.Popen(
                ["ffmpeg", "-loglevel", "error", "-y", "-f", "s16le", "-ar", str(AUDIO_FS), "-ac", "1",
                 "-i", "-", "-b:a", f"{self.bitrate}k", "-f", "mp3", "-"],
                stdin=subprocess.PIPE, stdout=self._fh)
        self.active, self.t0, self.error = True, time.time(), ""
        self._thr = threading.Thread(target=self._run, daemon=True)
        self._thr.start()
        return self.path

    def write(self, block):
        if self.active:
            self.q.put((np.clip(block, -1, 1) * 32767).astype("<i2").tobytes())

    def _run(self):
        while self.active or not self.q.empty():
            try:
                pcm = self.q.get(timeout=0.25)           # блокируемся, а не крутим цикл
            except queue.Empty:
                continue
            try:
                if self._enc:
                    self._fh.write(self._enc.encode(pcm))
                else:
                    self._proc.stdin.write(pcm)
            except Exception as e:
                self.error = str(e)
                break

    def stop(self):
        if not self.active:
            return None
        self.active = False
        if self._thr:
            self._thr.join(timeout=3)
        try:
            if self._enc:
                self._fh.write(self._enc.flush())
            else:
                self._proc.stdin.close()
                self._proc.wait(timeout=10)
        except Exception as e:
            self.error = str(e)
        self._fh.close()
        return self.path

    @property
    def seconds(self):
        return time.time() - self.t0 if self.active else 0.0
