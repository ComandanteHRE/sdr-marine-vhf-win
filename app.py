#!/usr/bin/env python3
"""Морская УКВ-радиостанция на RTL-SDR (версия 2): голос, обзор диапазона, АИС (NMEA по TCP/UDP для OpenCPN),
запись в MP3. Окно можно делать вертикальным или горизонтальным — панель перестраивается сама.

Запуск:  MarineVHF.exe                      обычный
         MarineVHF.exe --ais --minimized    для OpenCPN: сразу слушать АИС, свернуться
         python app.py --demo               без ключа (синтетический эфир)
Параметры: см. python app.py --help
"""
import argparse
import collections
import json
import os
import queue
import socket
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

# --- Portable: DLL RTL-SDR лежат рядом с программой (рядом с .exe и во временной папке PyInstaller)
FROZEN = getattr(sys, "frozen", False)
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
for _d in {str(APP_DIR), getattr(sys, "_MEIPASS", str(APP_DIR))}:
    os.environ["PATH"] = _d + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, "add_dll_directory"):
        try:
            os.add_dll_directory(_d)
        except OSError:
            pass

import gfx
import ui
from audio import AudioOut, Mp3Recorder
from marine_channels import CHANNELS, DUPLEX
from netout import NmeaOutput
from panel_logic import PanelLogic
from radio import Receiver


def data_dir():
    """Папка настроек и записей: рядом с программой (portable); если туда нельзя писать — %APPDATA%\\MarineVHF."""
    try:
        t = APP_DIR / ".w"
        t.write_text("x")
        t.unlink()
        return APP_DIR
    except OSError:
        d = Path(os.environ.get("APPDATA", str(Path.home()))) / "MarineVHF"
        d.mkdir(parents=True, exist_ok=True)
        return d


DATA_DIR = data_dir()
CFG_PATH = DATA_DIR / "marine_vhf.json"
DEFAULTS = {
    "device_index": 0,            # номер RTL-SDR для голоса и обзора
    "ais_device_index": None,     # второй ключ только под АИС (иначе АИС и голос делят один ключ)
    "ppm": 0, "gain": "auto",
    "volume": 6, "squelch": 3,
    "tcp_port": 10110, "udp_port": 10110, "udp_host": "127.0.0.1", "nmea_public": False,
    "record_dir": "recordings", "mp3_kbps": 64, "record_only_when_open": True,
    "scan_threshold_db": 8.0, "scan_blocks": 6, "autostop": False,
    "last_channel": 16, "listen_ship": False, "geometry": "", "instance_port": 47931,
}


def load_cfg():
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CFG_PATH.read_text(encoding="utf-8")))
    except Exception:
        pass
    return cfg


def enable_dpi_awareness():
    """Чёткое изображение на HiDPI-экранах (Windows 7: SetProcessDPIAware, 8.1+: SetProcessDpiAwareness)."""
    if os.name != "nt":
        return
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


class SingleInstance:
    """Один экземпляр: второй запуск (например, из OpenCPN) только показывает окно первого."""

    def __init__(self, port, events):
        self.srv, self.events = None, events
        try:
            s = socket.socket()
            s.bind(("127.0.0.1", port))
            s.listen(2)
            self.srv = s
            threading.Thread(target=self._serve, daemon=True).start()
        except OSError:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1.5) as c:
                    c.sendall(b"show\n")
            except OSError:
                pass

    @property
    def primary(self):
        return self.srv is not None

    def _serve(self):
        while True:
            try:
                c, _ = self.srv.accept()
                c.close()
                self.events.put(("show", None))
            except OSError:
                return


class App(tk.Tk):
    def __init__(self, args, events, instance):
        super().__init__()
        self.args, self.events, self.instance = args, events, instance
        self.title("Морская УКВ · RTL-SDR")
        self.minsize(340, 480)
        c = self.cfg = load_cfg()
        for k in ("tcp_port", "udp_port", "udp_host"):
            if getattr(args, k, None) is not None:
                c[k] = getattr(args, k)
        if args.no_udp:
            c["udp_port"] = 0
        if args.listen_all:
            c["nmea_public"] = True
        self.demo = args.demo
        self.logic = PanelLogic(c["last_channel"], c["listen_ship"])
        # --- состояние, которое читает ui.py
        self.mode, self.rx_open, self.rssi, self.blink = "VOICE", False, 0.0, True
        self.rec_secs, self.scanning, self.ais_on, self.connected = None, False, False, False
        self.ais_count, self.ais_levels = 0, (-99.0, -99.0)
        self.scan, self.scan_data, self.active_hold, self.found = None, {}, {}, []
        self.error, self.sdr_info, self.thr = "", "", float(c["scan_threshold_db"])
        self.vol, self.sql = int(c["volume"]), int(c["squelch"])
        self.pressed, self.autostop, self.flash, self.footer = None, bool(c["autostop"]), "", ""
        self._flash_until, self._scan_ver, self._sigs = 0.0, 0, {}
        self.ais_log = collections.deque(maxlen=900)
        self.log_win = self.log_text = None
        self._drag = None
        # --- холст
        self.cv = tk.Canvas(self, bg=ui.C_BG, highlightthickness=0, bd=0)
        self.cv.pack(fill="both", expand=True)
        self.g = gfx.TkGfx(self.cv)
        self.L = None
        self._resize_job = None
        self.cv.bind("<Configure>", self._on_configure)
        self.cv.bind("<ButtonPress-1>", self._press)
        self.cv.bind("<B1-Motion>", self._motion)
        self.cv.bind("<ButtonRelease-1>", self._release)
        self.cv.bind("<MouseWheel>", self._wheel)
        self.cv.bind("<Button-4>", lambda e: self._wheel(e, 1))
        self.cv.bind("<Button-5>", lambda e: self._wheel(e, -1))
        self.bind("<Key>", self._on_key)
        self.protocol("WM_DELETE_WINDOW", self._close)
        geo = c.get("geometry") or "560x900"
        try:
            self.geometry(geo)
        except tk.TclError:
            self.geometry("560x900")
        self._start_backend()
        if args.ais:
            self.ais_on = True
        self._sync_radio()
        if args.minimized:
            self.iconify()
        self.after(60, self._poll)
        self.after(500, self._blink)

    # ------------------------------------------------------------ приёмники и вывод
    def _factory(self, idx):
        if self.demo:
            from sim import SimSdr
            return lambda: SimSdr(realtime=True)

        def make():
            from rtlsdr_ctypes import RtlSdrDev
            return RtlSdrDev(idx, search_dirs=[APP_DIR, getattr(sys, "_MEIPASS", APP_DIR)])
        return make

    def _start_nmea(self):
        c = self.cfg
        self.nmea = NmeaOutput(c["tcp_port"], c["udp_port"], c["udp_host"],
                               bind="0.0.0.0" if c["nmea_public"] else "127.0.0.1")
        self.nmea.start()

    def _start_backend(self):
        c = self.cfg
        self.audio = AudioOut()
        self.audio.volume = self.vol / 10
        self.audio.start()
        rd = Path(c["record_dir"])
        self.recorder = Mp3Recorder(rd if rd.is_absolute() else DATA_DIR / rd, c["mp3_kbps"])
        self._start_nmea()
        self.rx = Receiver(self._factory(c["device_index"]), self.events, self.audio, self.recorder,
                           self.nmea, c, "voice")
        self.rx.start()
        self.ais_rx = None
        if c["ais_device_index"] is not None and not self.demo:
            self.ais_rx = Receiver(self._factory(c["ais_device_index"]), self.events, None, None,
                                   self.nmea, c, "ais")
            self.ais_rx.start()

    def _sync_radio(self):
        """Привести приёмник в соответствие с кнопками."""
        want = "SCAN" if self.scanning else ("AIS" if self.ais_on and not self.ais_rx else "VOICE")
        self.mode = want
        self.rx.tune(self.logic.freq_hz)
        self.rx.set_sql(self.sql)
        self.rx.set_mode(want)
        if self.ais_rx:
            self.ais_rx.set_mode("AIS" if self.ais_on else "IDLE")

    # ------------------------------------------------------------ действия панели
    def _after_tune(self):
        self.scanning = False
        self._sync_radio()

    def _toggle_scan(self):
        self.scanning = not self.scanning
        if self.scanning and self.ais_on and not self.ais_rx:
            self.ais_on = False
            self._note("Один приёмник: АИС выключен на время обзора")
        self._sync_radio()

    def _toggle_ais(self):
        self.ais_on = not self.ais_on
        if self.ais_on and self.scanning and not self.ais_rx:
            self.scanning = False
        if self.ais_on and not self.ais_rx:
            self._note("Один приёмник: голос приостановлен, пока включён АИС (второй ключ — ais_device_index)")
        self._sync_radio()

    def _toggle_rec(self):
        if self.recorder.active:
            self._note(f"Записано: {self.recorder.stop()}")
            return
        lg = self.logic
        p = self.recorder.start(f"ch{lg.channel}" if lg.channel else f"{lg.freq_hz / 1e6:.3f}MHz")
        if p is None:
            self._note(self.recorder.error)
        elif self.mode != "VOICE":
            self._note("Запись начнётся при приёме речи (сейчас идёт обзор/АИС)")

    def _listen(self, ch, ship):
        self.logic.goto_channel(ch, ship if ch in DUPLEX else None)
        self._after_tune()

    def _do(self, name):
        lg = self.logic
        if name in ("kDot",):
            lg.key(".")
        elif name[0] == "k" and name[1:].isdigit():
            lg.key(name[1:])
        elif name == "kClr":
            lg.clear()
        elif name == "kEnt":
            if lg.enter():
                self._after_tune()
        elif name == "b16":
            lg.ch16()
            self._after_tune()
        elif name == "bUp":
            lg.step(1)
            self._after_tune()
        elif name == "bDown":
            lg.step(-1)
            self._after_tune()
        elif name == "bMode":
            lg.toggle_input()
        elif name == "bScan":
            self._toggle_scan()
        elif name == "bAis":
            self._toggle_ais()
        elif name == "bRec":
            self._toggle_rec()
        elif name == "cSide":
            if lg.toggle_side():
                self._after_tune()
        elif name == "cSet":
            self._settings()
        elif name == "cAuto":
            self.autostop = not self.autostop
            self.cfg["autostop"] = self.autostop
        elif name == "cLog":
            self._show_log()

    # ------------------------------------------------------------ мышь и клавиатура
    def _press(self, e):
        if not self.L:
            return
        h = ui.hit(self.L, e.x, e.y)
        self.focus_set()
        if h is None:
            return
        if h[0] in ("key", "chip"):
            self.pressed = h[1]
        elif h[0] == "slider":
            self._drag = h[1]
            self._set_slider(h[1], ui.slider_value(self.L, h[1], e.y))
        elif h[0] == "chart":
            self._chart_click(h[1], h[2])

    def _motion(self, e):
        if self._drag and self.L:
            self._set_slider(self._drag, ui.slider_value(self.L, self._drag, e.y))

    def _release(self, e):
        name, self.pressed, was_drag, self._drag = self.pressed, None, self._drag, None
        if name and self.L:
            h = ui.hit(self.L, e.x, e.y)
            if h and h[0] in ("key", "chip") and h[1] == name:
                self._do(name)
        if was_drag:
            self._save_cfg()

    def _wheel(self, e, d=None):
        if not self.L:
            return
        if d is None:
            d = 1 if e.delta > 0 else -1
        h = ui.hit(self.L, e.x, e.y)
        if h and h[0] == "slider":
            cur = self.sql if h[1] == "sql" else self.vol
            self._set_slider(h[1], max(0, min(10, cur + d)))

    def _set_slider(self, name, v):
        if name == "sql":
            if v != self.sql:
                self.sql = v
                self.rx.set_sql(v)
        elif v != self.vol:
            self.vol = v
            self.audio.volume = v / 10

    def _chart_click(self, i, frac):
        ch = CHANNELS[i]
        sv, cv_ = self.scan_data.get((ch, True)), self.scan_data.get((ch, False))
        if ch in DUPLEX and cv_ is not None and (sv is None or cv_ > sv):
            ship = False
        elif ch in DUPLEX and sv is None and cv_ is None:
            ship = frac < 0.5
        else:
            ship = True
        self._listen(ch, ship)

    def _on_key(self, e):
        if isinstance(e.widget, (tk.Entry, tk.Text)):
            return
        if e.char and e.char in "0123456789.":
            self.logic.key(e.char)
        elif e.keysym in ("Return", "KP_Enter"):
            self._do("kEnt")
        elif e.keysym in ("BackSpace", "Delete", "Escape"):
            self.logic.clear()
        elif e.keysym == "Up":
            self._do("bUp")
        elif e.keysym == "Down":
            self._do("bDown")
        elif e.keysym == "Home":
            self._do("b16")
        elif e.char in ("c", "C", "с", "С"):
            self._do("bMode")
        elif e.char in ("s", "S", "ы", "Ы"):
            self._do("bScan")
        elif e.char in ("a", "A", "ф", "Ф"):
            self._do("bAis")
        elif e.char in ("r", "R", "к", "К"):
            self._do("bRec")

    # ------------------------------------------------------------ обзор
    def _on_scan_progress(self, d):
        self.scan = {k: d[k] for k in ("hop", "hops", "k", "K")}
        for r in d["res"]:
            self.scan_data[(r["ch"], r["ship"])] = r["db"]
        self._scan_ver += 1

    def _on_scan_done(self, res):
        now = time.time()
        act = [r for r in res if r["active"]]
        for r in act:
            self.active_hold[(r["ch"], r["ship"])] = (r["db"], now)
        self.active_hold = {k: v for k, v in self.active_hold.items() if now - v[1] < 30}
        items = sorted(self.active_hold.items(), key=lambda kv: -kv[1][0])[:12]
        self.found = [ui.chip_label(k[0], k[1]) for k, _ in items]
        if self.autostop and self.scanning and act:
            best = max(act, key=lambda r: r["db"])
            self._listen(best["ch"], best["ship"])

    # ------------------------------------------------------------ АИС и журнал
    def _on_ais(self, d):
        self.ais_count = d["count"]
        self._log(f"{time.strftime('%H:%M:%S')} {d['chan']}  {d['text']}\n", None)
        for s in d["sentences"]:
            self._log(s + "\n", "raw")

    def _log(self, text, tag):
        self.ais_log.append((text, tag))
        if self.log_text is not None:
            try:
                self.log_text.config(state="normal")
                self.log_text.insert("end", text, tag)
                self.log_text.see("end")
                self.log_text.config(state="disabled")
            except tk.TclError:
                self.log_text = None

    def _show_log(self):
        if self.log_win is not None and self.log_win.winfo_exists():
            self.log_win.deiconify()
            self.log_win.lift()
            return
        w = self.log_win = tk.Toplevel(self)
        w.title("Журнал АИС")
        w.geometry("760x420")
        bar = tk.Frame(w)
        bar.pack(fill="x")
        t = self.log_text = tk.Text(w, wrap="none", bg="#0e1318", fg="#cfe3f5", font=("Consolas", 10),
                                    state="disabled")
        t.tag_config("raw", foreground="#6f8299")
        sb = tk.Scrollbar(w, command=t.yview)
        t.config(yscrollcommand=sb.set)

        def copy():
            self.clipboard_clear()
            self.clipboard_append("".join(x for x, _ in self.ais_log))

        def clear():
            self.ais_log.clear()
            t.config(state="normal")
            t.delete("1.0", "end")
            t.config(state="disabled")
        tk.Button(bar, text="Копировать всё", command=copy).pack(side="left", padx=4, pady=3)
        tk.Button(bar, text="Очистить", command=clear).pack(side="left", padx=4, pady=3)
        sb.pack(side="right", fill="y")
        t.pack(fill="both", expand=True)
        t.config(state="normal")
        for text, tag in self.ais_log:
            t.insert("end", text, tag)
        t.see("end")
        t.config(state="disabled")

    # ------------------------------------------------------------ настройки
    def _settings(self):
        c = self.cfg
        w = tk.Toplevel(self)
        w.title("Настройки")
        w.resizable(False, False)
        w.transient(self)
        fields = [
            ("Ключ RTL-SDR для голоса (номер)", "device_index", int),
            ("Второй ключ только для АИС (номер, пусто — нет)", "ais_device_index", "optint"),
            ("Поправка частоты, ppm", "ppm", float),
            ("Усиление, дБ (auto — автоматически)", "gain", "gain"),
            ("TCP-порт NMEA (0 — выключить)", "tcp_port", int),
            ("UDP-порт NMEA (0 — выключить)", "udp_port", int),
            ("UDP — адрес получателя", "udp_host", str),
            ("Порог обнаружения при обзоре, дБ", "scan_threshold_db", float),
            ("MP3, кбит/с", "mp3_kbps", int),
            ("Папка записей", "record_dir", str),
        ]
        vars_ = {}
        for r, (lab, key, typ) in enumerate(fields):
            tk.Label(w, text=lab, anchor="w").grid(row=r, column=0, sticky="w", padx=10, pady=3)
            v = tk.StringVar(value="" if c[key] is None else str(c[key]))
            tk.Entry(w, textvariable=v, width=22).grid(row=r, column=1, padx=10, pady=3)
            vars_[key] = (v, typ)
        n = len(fields)
        pub = tk.BooleanVar(value=c["nmea_public"])
        only = tk.BooleanVar(value=c["record_only_when_open"])
        tk.Checkbutton(w, text="NMEA-сервер доступен по сети (OpenCPN на другом компьютере)", variable=pub
                       ).grid(row=n, column=0, columnspan=2, sticky="w", padx=8)
        tk.Checkbutton(w, text="Писать только когда шумоподавитель открыт", variable=only
                       ).grid(row=n + 1, column=0, columnspan=2, sticky="w", padx=8)
        tk.Label(w, fg="#555", justify="left", text="Номера ключей и папка записей применяются после перезапуска.\n"
                 "OpenCPN: Настройки → Подключения → Сеть → TCP, 127.0.0.1, порт NMEA, «Вход».",
                 ).grid(row=n + 2, column=0, columnspan=2, sticky="w", padx=10, pady=6)

        def apply():
            new = {}
            try:
                for key, (v, typ) in vars_.items():
                    s = v.get().strip()
                    if typ == "optint":
                        new[key] = int(s) if s else None
                    elif typ == "gain":
                        new[key] = "auto" if s.lower() in ("", "auto") else float(s.replace(",", "."))
                    elif typ is float:
                        new[key] = float(s.replace(",", "."))
                    elif typ is int:
                        new[key] = int(s)
                    else:
                        new[key] = s
            except ValueError:
                messagebox.showerror("Настройки", "Проверьте числа в полях.", parent=w)
                return
            net_changed = any(new[k] != c[k] for k in ("tcp_port", "udp_port", "udp_host")) or pub.get() != c["nmea_public"]
            c.update(new, nmea_public=pub.get(), record_only_when_open=only.get())
            self.thr = float(c["scan_threshold_db"])
            if net_changed:
                self.nmea.close()
                self._start_nmea()
                self.rx.nmea = self.nmea
                if self.ais_rx:
                    self.ais_rx.nmea = self.nmea
            self.rx.set_gain(c["gain"] if c["gain"] == "auto" else float(c["gain"]))
            self.rx.set_ppm(float(c["ppm"] or 0))
            if not self.recorder.active:
                self.recorder.bitrate = int(c["mp3_kbps"])
            self._save_cfg()
            w.destroy()
        bt = tk.Frame(w)
        bt.grid(row=n + 3, column=0, columnspan=2, pady=8)
        tk.Button(bt, text="Сохранить", width=12, command=apply).pack(side="left", padx=6)
        tk.Button(bt, text="Отмена", width=12, command=w.destroy).pack(side="left", padx=6)
        w.grab_set()

    # ------------------------------------------------------------ отрисовка
    def _on_configure(self, e):
        if self._resize_job:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(30, self._relayout)

    def _relayout(self):
        self._resize_job = None
        w, h = self.cv.winfo_width(), self.cv.winfo_height()
        if w < 50 or h < 50:
            return
        self.L = ui.make_layout(w, h)
        self._sigs.clear()
        ui.draw_chassis(self.g, self.L, self)
        self._redraw()

    def _note(self, text, secs=8):
        self.flash, self._flash_until = text, time.time() + secs

    def _footer(self):
        c, nm = self.cfg, self.nmea
        parts = [self.sdr_info] if self.sdr_info and self.connected else []
        tcp = f"TCP :{c['tcp_port']} (клиентов {nm.n_clients})" if c["tcp_port"] else "TCP выкл"
        udp = f"UDP → {c['udp_host']}:{c['udp_port']}" if c["udp_port"] else "UDP выкл"
        parts.append(f"NMEA: {tcp} · {udp} · АИС принято: {self.ais_count}")
        parts += [e for e in (nm.error, self.audio.error) if e]
        return "  |  ".join(parts)

    def _redraw(self):
        """Перерисовывает только те слои, чьи данные изменились."""
        L, g, lg = self.L, self.g, self.logic
        if L is None:
            return
        sigs = {
            "keys": (self.pressed, self.scanning, self.ais_on, self.rec_secs is not None, lg.listen_ship, self.autostop),
            "lcd": (lg.channel, lg.buf, lg.freq_hz, lg.input_mode, lg.message, self.mode, self.rx_open,
                    int(self.rssi / 2.5), None if self.rec_secs is None else (int(self.rec_secs), self.blink),
                    tuple((self.scan or {}).get(k) for k in ("hop", "k")), tuple(int(x) for x in self.ais_levels),
                    self.ais_count, self.error, self.connected, self.flash, tuple(self.found), lg.listen_ship,
                    self.ais_on, self.scanning),
            "sliders": (self.sql, self.vol),
            "chart": (self._scan_ver, lg.channel, lg.listen_ship, self.thr, self.scanning,
                      tuple((self.scan or {}).get(k) for k in ("hop", "k")), self.footer),
        }
        for name, fn in (("keys", ui.draw_keys), ("lcd", ui.draw_lcd), ("sliders", ui.draw_sliders),
                         ("chart", ui.draw_chart)):
            if self._sigs.get(name) != sigs[name]:
                self._sigs[name] = sigs[name]
                fn(g, L, self)
        self.cv.tag_lower("chassis")

    def _poll(self):
        last_voice, n = None, 0
        try:
            while n < 300:
                kind, d = self.events.get_nowait()
                n += 1
                if kind == "voice":
                    last_voice = d
                elif kind == "ready":
                    self.error, self.sdr_info, self.connected = "", d or "", True
                elif kind == "error":
                    self.connected = False
                    self.error = str(d) + ("" if self.demo else "   (python app.py --demo — проверка без ключа)")
                elif kind == "ais":
                    self._on_ais(d)
                elif kind == "ais_level":
                    self.ais_levels = d
                elif kind == "scan_progress":
                    self._on_scan_progress(d)
                elif kind == "scan_done":
                    self._on_scan_done(d)
                elif kind == "show":
                    self.deiconify()
                    self.lift()
                    self.focus_force()
        except queue.Empty:
            pass
        if last_voice:
            self.rx_open, self.rssi = last_voice["open"], last_voice["rssi"]
        self.rec_secs = self.recorder.seconds if self.recorder.active else None
        if self.flash and time.time() > self._flash_until:
            self.flash = ""
        self.footer = self._footer()
        if not self.state() == "iconic":                  # свёрнуто (режим OpenCPN) — не рисуем, экономим CPU
            self._redraw()
        self.after(60 if self.state() != "iconic" else 400, self._poll)

    def _blink(self):
        self.blink = not self.blink
        self.after(500 if self.state() != "iconic" else 2000, self._blink)

    def _save_cfg(self):
        try:
            self.cfg.update(last_channel=self.logic.channel or 16, listen_ship=self.logic.listen_ship,
                            volume=self.vol, squelch=self.sql)
            if self.state() == "normal":
                self.cfg["geometry"] = self.geometry()
            CFG_PATH.write_text(json.dumps(self.cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _close(self):
        self._save_cfg()
        for r in (self.rx, self.ais_rx):
            if r:
                r.stop()
        if self.recorder.active:
            self.recorder.stop()
        self.audio.stop()
        self.nmea.close()
        self.destroy()


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--demo", action="store_true", help="синтетический эфир вместо RTL-SDR")
    ap.add_argument("--ais", action="store_true", help="сразу включить приём АИС (для OpenCPN)")
    ap.add_argument("--minimized", action="store_true", help="запуститься свёрнутым")
    ap.add_argument("--tcp-port", dest="tcp_port", type=int, help="TCP-порт NMEA (0 — выкл.)")
    ap.add_argument("--udp-port", dest="udp_port", type=int, help="UDP-порт NMEA")
    ap.add_argument("--udp-host", dest="udp_host", help="адрес получателя UDP")
    ap.add_argument("--no-udp", action="store_true", help="не слать UDP")
    ap.add_argument("--listen-all", action="store_true", help="TCP-сервер NMEA доступен по сети (0.0.0.0)")
    return ap.parse_args()


def main():
    args = parse_args()
    enable_dpi_awareness()
    events = queue.Queue()
    port = load_cfg()["instance_port"]
    inst = SingleInstance(port, events)
    if not inst.primary:                      # программа уже запущена — показали её окно и выходим
        return
    App(args, events, inst).mainloop()


if __name__ == "__main__":
    main()
