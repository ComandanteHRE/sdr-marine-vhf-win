"""Ядро приёма: поток, владеющий RTL-SDR. Режимы: VOICE (канал), SCAN (обзор диапазона), AIS."""
import queue
import threading
import time

import numpy as np

from dsp import (FS, BLOCK, VOICE_OFFSET_HZ, NfmVoice, psd_block, channel_power_db, excess_over_floor)
from ais import AisDecoder, AIS_CENTER_HZ, nmea_sentences, parse_ais, describe
from marine_channels import (CHANNELS, DUPLEX, ship_hz, coast_hz, AIS1_HZ, AIS2_HZ, SCAN_HOPS)

# (центр ступени, первая частота 25-кГц сетки); 57 позиций в каждой
SCAN_GRIDS = [(SCAN_HOPS[0], 156_025_000), (SCAN_HOPS[1], 160_625_000)]
GRID_N = 57


def _label_map():
    m = {}
    for ch in CHANNELS:
        m[ship_hz(ch)] = (ch, True)
        if ch in DUPLEX:
            m[coast_hz(ch)] = (ch, False)
    m[AIS1_HZ] = ("AIS1", False)
    m[AIS2_HZ] = ("AIS2", False)
    return m


LABELS = _label_map()


class Receiver(threading.Thread):
    def __init__(self, sdr_factory, events: queue.Queue, audio=None, recorder=None, nmea=None,
                 cfg=None, name="rx"):
        super().__init__(daemon=True, name=name)
        self.factory, self.events, self.audio, self.recorder, self.nmea = sdr_factory, events, audio, recorder, nmea
        self.cfg = cfg or {}
        self.lock = threading.Lock()
        self.pending = {}
        self.running = True
        self.mode = "IDLE"
        self.freq_hz = 156_800_000
        self.sql = int(self.cfg.get("squelch", 3))
        self.gain = self.cfg.get("gain", "auto")
        self.rec_only_open = bool(self.cfg.get("record_only_when_open", True))
        self.voice = self.ais = None
        self.sc = None
        self.ais_count = 0
        self._tail = False

    # ---------- команды из GUI (потокобезопасно)
    def _set(self, **kw):
        with self.lock:
            self.pending.update(kw)

    def tune(self, hz):
        self._set(freq=hz)

    def set_mode(self, mode):
        self._set(mode=mode)

    def set_sql(self, level):
        self.sql = int(level)

    def set_gain(self, g):
        self._set(gain=g)

    def set_ppm(self, ppm):
        self._set(ppm=ppm)

    def stop(self):
        self.running = False

    def emit(self, kind, data=None):
        self.events.put((kind, data))

    # ---------- основной цикл
    def run(self):
        """Открывает ключ и принимает; при ошибке/отключении ключа сообщает и пробует снова каждые 2 с
        (RTL-SDR можно подключить уже после запуска программы)."""
        failed = False
        while self.running:
            sdr = None
            try:
                sdr = self.factory()
                sdr.sample_rate = FS
                ppm = float(self.cfg.get("ppm", 0) or 0)
                if ppm:
                    sdr.freq_correction = ppm
                sdr.gain = self.gain
                self.emit("ready", getattr(sdr, "info", ""))
                failed = False
                self._enter(sdr, self.mode if self.mode != "IDLE" else "IDLE")
                while self.running:
                    self._apply(sdr)
                    if self.mode == "VOICE":
                        self._voice(sdr)
                    elif self.mode == "SCAN":
                        self._scan(sdr)
                    elif self.mode == "AIS":
                        self._ais(sdr)
                    else:
                        time.sleep(0.05)
            except Exception as e:
                if self.running:
                    self.emit("error", f"RTL-SDR: {e}")
                failed = True
            finally:
                if sdr is not None:
                    try:
                        sdr.close()
                    except Exception:
                        pass
            for _ in range(40):                       # пауза 2 с, но быстро реагируем на выход
                if not self.running:
                    break
                time.sleep(0.05)

    def _apply(self, sdr):
        with self.lock:
            p, self.pending = self.pending, {}
        if "gain" in p:
            self.gain = p["gain"]
            sdr.gain = self.gain
        if "ppm" in p:
            sdr.freq_correction = p["ppm"]
        if "freq" in p:
            self.freq_hz = p["freq"]
            if self.mode == "VOICE":
                self._enter(sdr, "VOICE")
        if "mode" in p and (p["mode"] != self.mode or p["mode"] == "SCAN"):
            self.mode = p["mode"]
            self._enter(sdr, self.mode)
            self.emit("mode", self.mode)

    def _enter(self, sdr, mode):
        if mode == "VOICE":
            sdr.center_freq = self.freq_hz - VOICE_OFFSET_HZ
            if self.voice is None:
                self.voice = NfmVoice()
            sdr.read_samples(BLOCK)                      # выбросить остатки старой настройки
            if self.audio:
                self.audio.clear()
        elif mode == "AIS":
            sdr.center_freq = AIS_CENTER_HZ
            self.ais = AisDecoder()
            sdr.read_samples(BLOCK)
        elif mode == "SCAN":
            self.sc = {"hop": 0, "k": 0, "acc": None, "nseg": 0, "all": {}}

    # ---------- голос
    def _voice(self, sdr):
        audio, info = self.voice.process(sdr.read_samples(BLOCK), self.sql)
        # при закрытом шумодаве звук не отправляем (экономия CPU); один блок «хвоста» — чтобы не обрывать слово
        if self.audio and (info["open"] or self._tail):
            self.audio.push(audio)
        self._tail = info["open"]
        if self.recorder and self.recorder.active and (info["open"] or not self.rec_only_open):
            self.recorder.write(audio)
        info["freq"] = self.freq_hz
        self.emit("voice", info)

    # ---------- АИС
    def _ais(self, sdr):
        iq = sdr.read_samples(BLOCK)
        for m in self.ais.process(iq):
            sents = nmea_sentences(m["bits"], m["chan"])
            if self.nmea:
                for s in sents:
                    self.nmea.send(s)
            self.ais_count += 1
            info = parse_ais(m["bits"])
            self.emit("ais", {"chan": m["chan"], "sentences": sents, "info": info,
                              "text": describe(info), "count": self.ais_count})
        self.emit("ais_level", (self.ais.ch[0].rssi_db, self.ais.ch[1].rssi_db))

    # ---------- обзор диапазона
    def _scan(self, sdr):
        sc = self.sc
        blocks = int(self.cfg.get("scan_blocks", 6))
        thr = float(self.cfg.get("scan_threshold_db", 8.0))
        center, first = SCAN_GRIDS[sc["hop"]]
        grid = [first + 25_000 * i for i in range(GRID_N)]
        if sc["k"] == 0:
            sdr.center_freq = center
            sdr.read_samples(BLOCK)                      # установление после перестройки
            sc["acc"], sc["nseg"] = None, 0
        psd, n = psd_block(sdr.read_samples(BLOCK))
        sc["acc"] = psd if sc["acc"] is None else sc["acc"] + psd
        sc["nseg"] += n
        sc["k"] += 1
        exc = excess_over_floor(channel_power_db(sc["acc"] / sc["nseg"], center, grid))
        res = []
        for hz, db in zip(grid, exc):
            lab = LABELS.get(hz)
            if lab:
                res.append({"ch": lab[0], "ship": lab[1], "hz": hz, "db": float(db),
                            "active": bool(db >= thr and lab[0] not in ("AIS1", "AIS2"))})
        self.emit("scan_progress", {"hop": sc["hop"], "hops": len(SCAN_GRIDS), "k": sc["k"],
                                    "K": blocks, "res": res,
                                    "span": (grid[0], grid[-1])})
        if sc["k"] >= blocks:
            sc["all"][sc["hop"]] = res
            sc["hop"] += 1
            sc["k"] = 0
            if sc["hop"] >= len(SCAN_GRIDS):
                allres = [r for h in sorted(sc["all"]) for r in sc["all"][h]]
                self.emit("scan_done", allres)
                sc["hop"], sc["all"] = 0, {}
