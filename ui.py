"""Панель морской УКВ-радиостанции: раскладка (вертикальная / горизонтальная), отрисовка слоёв, поиск элемента под курсором.

Не зависит от Tk: рисует через gfx.TkGfx (программа) или gfx.PilGfx (проверка раскладки без экрана).
Состояние берётся из объекта s (см. app.py / preview.py)."""
from marine_channels import CHANNELS, DUPLEX

# ---------------------------------------------------------------- цвета
C_BG = "#0b0e12"
C_PANEL_EDGE, C_PANEL_IN, C_PANEL_HI = "#0a0d11", "#1e242c", "#323a45"
C_TITLE, C_DIM = "#8fb8ea", "#6f8299"
LCD_BG, LCD_EDGE, LCD_FG, LCD_OFF = "#ffae1a", "#7d5200", "#1a1100", "#e79d14"
KEY_STYLES = {                       # (верх, низ, тень, обводка, цвет текста)
    "key": ("#4d5560", "#2a3038", "#0c0f12", "#161a1f", "#f2f4f7"),
    "fn": ("#4a525d", "#2c323b", "#0c0f12", "#161a1f", "#f2f4f7"),
    "red": ("#e8352d", "#a91a13", "#2a0504", "#5a0b08", "#ffffff"),
    "green": ("#30a041", "#176a2a", "#05200b", "#0b3a15", "#b8f5c4"),
    "on": ("#3a8ae0", "#1d57a0", "#0a1626", "#0e2a52", "#ffffff"),
    "rec": ("#c4291f", "#7e150f", "#1f0403", "#3f0906", "#ffffff"),
}
BAR_SHIP, BAR_COAST, BAR_ACT = "#3f83d8", "#28b7b0", "#ffb81f"

KEY_LABELS = {"kDot": "·", "kEnt": "ENT", "kClr": "CLR", "b16": "16", "bUp": "▲", "bDown": "▼",
              "bScan": "СКАН", "bAis": "АИС", "bMode": "КАНАЛ /\nЧАСТОТА", "bRec": "● ЗАПИСЬ"}
for _i in range(10):
    KEY_LABELS[f"k{_i}"] = str(_i)

ORIENT_RATIO = 1.12                  # ширина/высота окна: больше — горизонтальный вид


class Rect(tuple):
    """(x0, y0, x1, y1) с удобными свойствами."""
    __slots__ = ()

    def __new__(cls, x0, y0, x1, y1):
        return tuple.__new__(cls, (x0, y0, x1, y1))

    x0 = property(lambda s: s[0])
    y0 = property(lambda s: s[1])
    x1 = property(lambda s: s[2])
    y1 = property(lambda s: s[3])
    w = property(lambda s: s[2] - s[0])
    h = property(lambda s: s[3] - s[1])
    cx = property(lambda s: (s[0] + s[2]) / 2)
    cy = property(lambda s: (s[1] + s[3]) / 2)

    def has(self, x, y):
        return self[0] <= x <= self[2] and self[1] <= y <= self[3]

    def sub(self, fx0, fy0, fx1, fy1):
        return Rect(self[0] + self.w * fx0, self[1] + self.h * fy0, self[0] + self.w * fx1, self[1] + self.h * fy1)


def grid(r, rows, cols, gap):
    """Сетка rows×cols в прямоугольнике r. Возвращает [[Rect]*cols]*rows."""
    cw, rh = (r.w - gap * (cols - 1)) / cols, (r.h - gap * (rows - 1)) / rows
    return [[Rect(r.x0 + c * (cw + gap), r.y0 + i * (rh + gap), r.x0 + c * (cw + gap) + cw, r.y0 + i * (rh + gap) + rh)
             for c in range(cols)] for i in range(rows)]


class Layout:
    pass


def make_layout(W, H):
    L = Layout()
    L.W, L.H = W, H
    L.land = W >= H * ORIENT_RATIO
    m = max(5.0, 0.016 * min(W, H) if L.land else 0.02 * W)
    top_h = (H - 2 * m) * (0.60 if L.land else 0.635)
    L.chassis = Rect(m, m, W - m, m + top_h)
    L.chart = Rect(m, m + top_h + m, W - m, H - m)
    c = L.chassis
    pd = max(8.0, 0.028 * c.w if not L.land else 0.016 * c.w)
    ix0, iy0, ix1, iy1 = c.x0 + pd, c.y0 + pd * 0.9, c.x1 - pd, c.y1 - pd
    I = Rect(ix0, iy0, ix1, iy1)
    L.inner = I
    hh = max(22.0, min(0.075 * I.h, 36.0))
    L.header = Rect(ix0, iy0, ix1, iy0 + hh)
    g = max(4.0, 0.012 * I.h)
    L.keys = {}
    L.u = max(10.0, min(I.w / (40 if not L.land else 70), I.h / (50 if not L.land else 30)))   # «условная единица» для шрифтов
    body = Rect(ix0, L.header.y1 + g, ix1, iy1)
    if not L.land:
        lcd_h = 0.265 * body.h
        L.lcd = Rect(ix0, body.y0, ix1, body.y0 + lcd_h)
        mid = Rect(ix0, L.lcd.y1 + g * 1.6, ix1, L.lcd.y1 + g * 1.6 + 0.43 * body.h)
        bot = Rect(ix0, mid.y1 + g * 1.6, ix1, iy1)
        kp = mid.sub(0, 0, 0.49, 1)
        names = [["k1", "k2", "k3"], ["k4", "k5", "k6"], ["k7", "k8", "k9"], ["kDot", "k0", "kEnt"]]
        kg = max(3.0, 0.014 * I.w)
        for row, rr in zip(names, grid(kp, 4, 3, kg)):
            for n, r in zip(row, rr):
                L.keys[n] = r
        col = mid.sub(0.515, 0, 0.695, 1)
        L.keys["b16"] = col.sub(0, 0, 1, 0.47)
        L.keys["kClr"] = col.sub(0, 0.53, 1, 0.94)
        sl = mid.sub(0.72, 0, 1, 1)
        L.sl_sql, L.sl_vol = sl.sub(0, 0, 0.5, 1), sl.sub(0.5, 0, 1, 1)
        rg = max(3.0, 0.014 * I.w)
        r_l, r_m, r_r = bot.sub(0, 0, 0.405, 1), bot.sub(0.425, 0, 0.805, 1), bot.sub(0.835, 0, 1, 1)
        for (a, b), r in ((("bRec", "bAis"), r_l), (("bScan", "bMode"), r_m), (("bUp", "bDown"), r_r)):
            ra, rb = grid(r, 2, 1, rg)
            L.keys[a], L.keys[b] = ra[0], rb[0]
        L.updown_group = bot.sub(0.82, -0.02, 1.01, 1.02)
    else:
        lcd_h = 0.50 * body.h
        left = body.sub(0, 0, 0.575, 1)
        L.lcd = Rect(left.x0, left.y0, left.x1, left.y0 + lcd_h)
        gb = Rect(left.x0, L.lcd.y1 + g * 1.4, left.x1, left.y1 - body.h * 0.02)
        rows = grid(gb, 2, 4, max(3.0, 0.011 * I.w))
        for rr, row in zip(rows, (["bRec", "bUp", "bScan", "b16"], ["bAis", "bDown", "bMode", "kClr"])):
            for n, r in zip(row, rr):
                L.keys[n] = r
        kp = body.sub(0.60, 0, 0.825, 0.80)
        names = [["k1", "k2", "k3"], ["k4", "k5", "k6"], ["k7", "k8", "k9"], ["kDot", "k0", "kEnt"]]
        for row, rr in zip(names, grid(kp, 4, 3, max(3.0, 0.009 * I.w))):
            for n, r in zip(row, rr):
                L.keys[n] = r
        sl = body.sub(0.855, 0, 1, 0.9)
        L.sl_sql, L.sl_vol = sl.sub(0, 0, 0.5, 1), sl.sub(0.5, 0, 1, 1)
        L.updown_group = None
    # --- плашки в шапке (справа налево)
    hs = L.header
    cw_side, cw_set = max(64.0, hs.h * 3.0), max(86.0, hs.h * 3.4)
    ch = hs.h * 0.86
    L.chips = {"cSet": Rect(hs.x1 - cw_set, hs.cy - ch / 2, hs.x1, hs.cy + ch / 2)}
    L.chips["cSide"] = Rect(L.chips["cSet"].x0 - 8 - cw_side, hs.cy - ch / 2, L.chips["cSet"].x0 - 8, hs.cy + ch / 2)
    # --- «Обзор каналов»
    cr = L.chart
    p = max(8.0, 0.02 * cr.w)
    L.chart_in = Rect(cr.x0 + p, cr.y0 + p * 0.8, cr.x1 - p, cr.y1 - p * 0.8)
    ci = L.chart_in
    L.fs_t = max(11.0, min(0.05 * ci.h + 6, 20.0))              # размер заголовка обзора
    title_h = L.fs_t * (3.6 if not L.land else 1.7)
    row_h = max(26.0, L.fs_t * 2.0)
    foot_h = row_h * (2 if not L.land else 1)
    L.chart_title = Rect(ci.x0, ci.y0, ci.x1, ci.y0 + title_h)
    L.chart_foot = Rect(ci.x0, ci.y1 - foot_h, ci.x1, ci.y1)
    L.plot = Rect(ci.x0 + 28, L.chart_title.y1 + 2, ci.x1 - 2, L.chart_foot.y0 - 4)
    fw = max(96.0, L.fs_t * 7)
    cy0 = L.chart_foot.y0 + 3 + (row_h if not L.land else 0)       # в вертикальном виде кнопки — второй строкой подвала
    L.foot_text = Rect(ci.x0, L.chart_foot.y0, ci.x1, L.chart_foot.y0 + row_h)
    L.chips["cLog"] = Rect(ci.x1 - fw, cy0, ci.x1, cy0 + row_h - 4)
    L.chips["cAuto"] = Rect(L.chips["cLog"].x0 - 8 - fw * 1.25, cy0, L.chips["cLog"].x0 - 8, cy0 + row_h - 4)
    if L.land:
        L.foot_text = Rect(ci.x0, L.chart_foot.y0, L.chips["cAuto"].x0 - 10, L.chart_foot.y0 + row_h)
    return L


# ---------------------------------------------------------------- вспомогательное
def lerp_col(c1, c2, t):
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def wrap(g, text, size, bold, maxw, maxlines=3):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if g.measure(t, size, bold) <= maxw or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > maxlines:
        lines = lines[:maxlines]
        lines[-1] = lines[-1][:max(0, len(lines[-1]) - 1)] + "…"
    return lines


def fmt_freq(hz):
    t = f"{hz / 1e6:.4f}"
    return t[:-1] if t.endswith("0") else t


def chip_label(ch, ship):
    if isinstance(ch, str):
        return ch
    return f"{ch}{'С' if ship else 'Б'}" if ch in DUPLEX else str(ch)


# ---------------------------------------------------------------- слои
def draw_chassis(g, L, s):
    g.begin("chassis")
    g.rect(0, 0, L.W, L.H, fill=C_BG)
    c = L.chassis
    g.rrect(c.x0, c.y0, c.x1, c.y1, 22, fill=C_PANEL_EDGE)
    g.rrect(c.x0 + 1, c.y0 + 1, c.x1 - 1, c.y1 - 1, 21, fill=C_PANEL_IN, outline=C_PANEL_HI, width=2)
    g.rrect(c.x0 + 3, c.y0 + 3, c.x1 - 3, c.y0 + c.h * 0.5, 19, fill="#232a33")      # блик верхней половины
    g.rrect(c.x0 + 3, c.y0 + c.h * 0.35, c.x1 - 3, c.y1 - 3, 19, fill=C_PANEL_IN)
    cr = L.chart
    g.rrect(cr.x0, cr.y0, cr.x1, cr.y1, 22, fill="#14181e", outline="#2e353f", width=2)
    if L.updown_group is not None:
        r = L.updown_group
        g.rrect(r.x0, r.y0, r.x1, r.y1, 12, fill="#181d23", outline="#12161a", width=1)
    h = L.header
    fs, room = max(11.0, h.h * 0.46), L.chips["cSide"].x0 - h.x0 - 12
    title = "MARINE VHF  •  RTL-SDR"
    if g.measure(title, fs, True) > room:
        title = "MARINE VHF"
    if g.measure(title, fs, True) > room:
        title = "VHF"
    g.text(h.x0 + 4, h.cy, title, fs, C_TITLE, True, "w")
    g.end()


def draw_keys(g, L, s):
    g.begin("keys")
    lit = {"bScan": s.scanning, "bAis": s.ais_on, "bRec": s.rec_secs is not None}
    for name, r in L.keys.items():
        style = {"b16": "red", "kEnt": "green"}.get(name, "fn" if name in (
            "kClr", "bRec", "bAis", "bScan", "bMode", "bUp", "bDown") else "key")
        if lit.get(name):
            style = "rec" if name == "bRec" else "on"
        top, bot, shadow, edge, tc = KEY_STYLES[style]
        pressed = s.pressed == name
        depth = min(4.0, r.h * 0.07)
        dy = depth * 0.7 if pressed else 0
        if pressed:
            top, bot = bot, lerp_col(bot, "#000000", 0.25)
        rad = min(10.0, r.h * 0.2)
        g.rrect(r.x0, r.y0, r.x1, r.y1, rad, fill=shadow)
        g.rrect(r.x0, r.y0 + dy, r.x1, r.y1 - depth + dy, rad, fill=bot, outline=edge, width=1)
        g.rrect(r.x0 + 1, r.y0 + 1 + dy, r.x1 - 1, r.y0 + dy + (r.h - depth) * 0.52, rad * 0.85, fill=top)
        label = KEY_LABELS[name]
        fs = min(r.h * 0.46, r.w * 0.34) if name[0] == "k" and name not in ("kClr", "kEnt") else min(r.h * 0.40, r.w * 0.22)
        if name == "b16":
            fs = min(r.h * 0.62, r.w * 0.5)
        elif name in ("kEnt", "kClr"):
            fs = min(r.h * 0.34, r.w * 0.30)
        elif name == "bMode":
            fs = min(r.h * 0.26, r.w * 0.11)
        elif name in ("bUp", "bDown"):
            fs = min(r.h * 0.42, r.w * 0.36)
        tcol = tc
        if name == "bRec":
            label = "■ СТОП" if s.rec_secs is not None else "● ЗАПИСЬ"
            tcol = "#ffffff" if s.rec_secs is not None else "#ff4b40"
            fs = min(r.h * 0.34, r.w * 0.15)
        cy = r.y0 + (r.h - depth) / 2 + dy
        if "\n" in label:
            a, b = label.split("\n")
            g.text(r.cx, cy - fs * 0.55, a, fs, tcol)
            g.text(r.cx, cy + fs * 0.55, b, fs, tcol)
        else:
            g.text(r.cx, cy, label, fs, tcol)
    # плашки шапки
    for name, r in L.chips.items():
        if name in ("cLog",):
            continue
        txt = {"cSide": "СУДНО" if s.logic.listen_ship else "БЕРЕГ", "cSet": "НАСТРОЙКИ", "cAuto": "АВТО-СТОП"}[name]
        on = name == "cAuto" and s.autostop
        pressed = s.pressed == name
        fill = "#3a4350" if pressed else ("#7a5410" if on else "#222831")
        g.rrect(r.x0, r.y0, r.x1, r.y1, 8, fill=fill, outline="#39414c", width=1)
        g.text(r.cx, r.cy, txt, min(r.h * 0.46, 15), "#ffd37a" if on else "#9cc4f2")
    r = L.chips["cLog"]
    g.rrect(r.x0, r.y0, r.x1, r.y1, 8, fill="#3a4350" if s.pressed == "cLog" else "#222831", outline="#39414c", width=1)
    g.text(r.cx, r.cy, "ЖУРНАЛ АИС", min(r.h * 0.46, 15), "#9cc4f2")
    g.end()


def draw_lcd(g, L, s):
    g.begin("lcd")
    r, lg = L.lcd, s.logic
    W, H = r.w, r.h
    g.rrect(r.x0, r.y0, r.x1, r.y1, 14, fill="#0d1014")
    g.rrect(r.x0 + 3, r.y0 + 3, r.x1 - 3, r.y1 - 3, 12, fill=LCD_BG, outline=LCD_EDGE, width=1)
    g.rrect(r.x0 + 6, r.y0 + 5, r.x1 - 6, r.y0 + H * 0.45, 10, fill="#ffb829")           # «блик» стекла
    g.rrect(r.x0 + 6, r.y0 + H * 0.40, r.x1 - 6, r.y1 - 6, 10, fill=LCD_BG)
    X, Y = r.x0, r.y0
    fs_flag = max(10.0, H * 0.095)
    # --- верхняя строка: режим и флаги
    x = X + W * 0.04
    flags = [("FREQ" if lg.input_mode == "FREQ" else "CH", True), ("RX" if s.connected else "OFF", s.connected),
             ("SQL", s.rx_open and s.mode == "VOICE")]
    if lg.duplex:
        flags.append(("СУДНО" if lg.listen_ship else "БЕРЕГ", True))
    if s.scanning:
        flags.append(("СКАН", True))
    if s.ais_on:
        flags.append(("АИС", True))
    for t, on in flags:
        g.text(x, Y + H * 0.14, t, fs_flag, LCD_FG if on else LCD_OFF, True, "w")
        x += g.measure(t, fs_flag, True) + fs_flag * 0.9
    if s.rec_secs is not None:
        t = ("●" if s.blink else "○") + f" {int(s.rec_secs) // 60:02d}:{int(s.rec_secs) % 60:02d}"
        g.text(r.x1 - W * 0.04, Y + H * 0.14, t, fs_flag, LCD_FG, True, "e")
    # --- основное содержимое
    meter, line = 0.0, ""
    if s.error and not s.connected:
        lines = wrap(g, s.error, max(11.0, H * 0.115), True, W * 0.9, 4)
        fs = max(11.0, H * 0.115)
        y0 = Y + H * 0.50 - (len(lines) - 1) * fs * 0.6
        for i, t in enumerate(lines):
            g.text(X + W * 0.05, y0 + i * fs * 1.25, t, fs, LCD_FG, True, "w")
        g.end()
        return
    if s.mode == "SCAN":
        sc = s.scan or {}
        hops, hop, k, K = sc.get("hops", 2), sc.get("hop", 0), sc.get("k", 0), max(1, sc.get("K", 1))
        g.text(r.cx, Y + H * 0.42, "СКАН", H * 0.36, LCD_FG)
        g.text(r.cx, Y + H * 0.66, f"диапазон {hop + 1}/{hops}   " + "▮" * k + "▯" * (K - k), max(11.0, H * 0.10), LCD_FG)
        line = ("АКТИВНЫ: " + "  ".join(s.found[:7])) if s.found else "сигналов пока нет"
        meter = (hop + k / K) / hops
    elif s.mode == "AIS":
        la, lb = s.ais_levels
        g.text(r.cx, Y + H * 0.42, "AIS", H * 0.38, LCD_FG)
        g.text(r.cx, Y + H * 0.66, f"A {la:.0f} дБ    B {lb:.0f} дБ    сообщений: {s.ais_count}", max(11.0, H * 0.10), LCD_FG)
        line = f"голос приостановлен (канал {lg.channel or '--'} ждёт)"
        meter = max(0.0, min(1.0, (max(la, lb) + 80) / 50))
    else:
        entering = bool(lg.buf)
        if lg.input_mode == "CH":
            txt = lg.buf.ljust(2, "_") if entering else (f"{lg.channel:02d}" if lg.channel else "--")
            g.text(X + W * 0.05, Y + H * 0.46, txt, H * 0.50, LCD_FG, True, "w")
            g.text(r.x1 - W * 0.04, Y + H * 0.44, fmt_freq(lg.freq_hz), H * 0.17, LCD_FG, True, "e")
            g.text(r.x1 - W * 0.04, Y + H * 0.60, "МГц" + ("   DSC" if lg.channel == 70 else ""), max(10.0, H * 0.09), LCD_FG, True, "e")
            line = lg.message or lg.note
        else:
            txt = lg.buf if entering else fmt_freq(lg.freq_hz)
            g.text(r.x1 - W * 0.04, Y + H * 0.44, txt or "_", H * 0.34, LCD_FG, True, "e")
            g.text(X + W * 0.05, Y + H * 0.44, f"CH {lg.channel:02d}" if lg.channel else "", H * 0.15, LCD_FG, True, "w")
            g.text(r.x1 - W * 0.04, Y + H * 0.64, "МГц", max(10.0, H * 0.09), LCD_FG, True, "e")
            line = lg.message or (lg.note if lg.channel else "вне сетки морских каналов")
        meter = min(1.0, s.rssi / 40.0) if s.rx_open else 0.0
    if s.flash:
        line = s.flash
    fs = max(10.0, H * 0.095)
    for i, t in enumerate(wrap(g, line, fs, True, W * 0.9, 2)):
        g.text(X + W * 0.05, Y + H * (0.775 + 0.10 * i), t, fs, LCD_FG, True, "w")
    # --- шкала (S-метр / прогресс)
    n, x0, sw = 16, X + W * 0.05, W * 0.9 / 16
    base, hmax = Y + H * 0.93, H * 0.075
    for i in range(n):
        on = (i + 0.5) / n <= meter
        hh = hmax * (0.35 + 0.65 * i / (n - 1))
        g.rect(x0 + i * sw, base - hh, x0 + i * sw + sw * 0.78, base, fill=LCD_FG if on else LCD_OFF)
    g.end()


def slider_geom(r):
    top, bot = r.y0 + r.h * 0.16, r.y1 - r.h * 0.17
    return top, bot


def draw_sliders(g, L, s):
    g.begin("sliders")
    for r, label, val in ((L.sl_sql, "SQL", s.sql), (L.sl_vol, "VOL", s.vol)):
        top, bot = slider_geom(r)
        cx = r.cx
        fs = max(11.0, min(r.w * 0.30, r.h * 0.085))
        g.text(cx, r.y0 + r.h * 0.07, label, fs, "#dde3ea")
        g.text(cx, r.y1 - r.h * 0.07, str(val), fs, "#dde3ea")
        tw = max(10.0, r.w * 0.28)
        for i in range(11):
            y = bot - (bot - top) * i / 10
            g.line(cx - tw * 1.5, y, cx - tw * 0.85, y, "#5a6572", 1.5)
            g.line(cx + tw * 0.85, y, cx + tw * 1.5, y, "#5a6572", 1.5)
        gw = max(5.0, r.w * 0.11)
        g.rrect(cx - gw, top - gw, cx + gw, bot + gw, gw, fill="#07090c")
        g.rrect(cx - gw * 0.5, top - gw * 0.6, cx + gw * 0.5, bot + gw * 0.6, gw * 0.5, fill="#2a313a")
        y = bot - (bot - top) * val / 10
        kw, kh = max(16.0, r.w * 0.30), max(11.0, r.w * 0.20)
        g.rrect(cx - kw, y - kh, cx + kw, y + kh, 6, fill="#8a5a00")
        g.rrect(cx - kw, y - kh, cx + kw, y + kh * 0.8, 6, fill="#ffb81f")
        g.line(cx - kw * 0.65, y - kh * 0.2, cx + kw * 0.65, y - kh * 0.2, "#b27800", 2)
        g.line(cx - kw * 0.65, y + kh * 0.3, cx + kw * 0.65, y + kh * 0.3, "#b27800", 2)
    g.end()


def chart_cols(L):
    """x-границы колонок обзора: список (x0, x1) по CHANNELS."""
    p = L.plot
    cw = p.w / len(CHANNELS)
    return [(p.x0 + i * cw, p.x0 + (i + 1) * cw) for i in range(len(CHANNELS))]


def draw_chart(g, L, s):
    g.begin("chart")
    t, p, f = L.chart_title, L.plot, L.chart_foot
    fs = L.fs_t
    g.text(t.x0 + 2, t.y0 + fs * 0.75, "ОБЗОР КАНАЛОВ", fs, C_TITLE, True, "w")
    head = "синий — судовая сторона, бирюзовый — береговая; клик — слушать канал"
    if s.scanning and s.scan:
        sc = s.scan
        head = f"идёт обзор: ступень {sc['hop'] + 1}/{sc['hops']}, замер {sc['k']}/{sc['K']}"
    x_leg = t.x0 + 2 + g.measure("ОБЗОР КАНАЛОВ", fs, True) + fs * 0.7
    fl = max(9.0, fs * 0.72)
    if L.land:
        g.text(x_leg, t.y0 + fs * 0.78, head, fl, C_DIM, False, "w")
    else:
        for i, line in enumerate(wrap(g, head, fl, False, t.w - 4, 2)):
            g.text(t.x0 + 2, t.y0 + fs * (1.85 + 1.05 * i), line, fl, C_DIM, False, "w")
    top, bot = p.y0, p.y1 - max(14.0, fs * 1.1)                                  # внизу — подписи каналов
    ycoord = lambda db: bot - min(max(db, 0), 40.0) / 40.0 * (bot - top)
    for db in (1, 10, 20, 30, 40):
        y = ycoord(0 if db == 1 else db)
        g.line(p.x0, y, p.x1, y, "#5a6572" if db == 1 else "#262d36", 1)
        g.text(p.x0 - 4, y, str(db), max(9.0, fs * 0.72), "#c9d3de", True, "e")
    g.line(p.x0, ycoord(s.thr), p.x1, ycoord(s.thr), "#f2c21a", 1.6, dash=(8, 5))
    cols = chart_cols(L)
    lab = max(7.0, min(fs * 0.7, (cols[0][1] - cols[0][0]) * 1.25))
    step = 1
    while g.measure("88", lab, True) * 1.15 > (cols[0][1] - cols[0][0]) * step and step < 6:
        step += 1
    forced = [i for i, c in enumerate(CHANNELS) if c in (16, 70)]
    show = set(forced)
    for i in range(0, len(CHANNELS), step):
        if all(abs(i - j) >= step for j in forced):
            show.add(i)
    for i, ch in enumerate(CHANNELS):
        x0, x1 = cols[i]
        sv = s.scan_data.get((ch, True))
        cv_ = s.scan_data.get((ch, False)) if ch in DUPLEX else None
        act = any(v is not None and v >= s.thr for v in (sv, cv_))
        if act:
            g.rect(x0, top, x1, bot, fill="#2f2711")
        mid = (x0 + x1) / 2 if ch in DUPLEX else x1 - 1
        for v, a, b, base_c in ((sv, x0 + 1, mid, BAR_SHIP), (cv_, mid, x1 - 1, BAR_COAST)):
            if v is not None and b > a and v > 0:
                g.rect(a, ycoord(v), b, bot, fill=BAR_ACT if v >= s.thr else base_c)
        if i in show:
            g.text((x0 + x1) / 2, bot + lab * 0.95, str(ch), lab, "#ffcf66" if ch == 16 else ("#8fd0ff" if ch == 70 else "#a9b8c9"),
                   ch in (16, 70))
        if ch == s.logic.channel:
            g.rect(x0 - 0.5, top, x1 + 0.5, bot + lab * 1.7, outline="#ffb81f", width=2)
    if s.scanning and s.scan:
        prog = (s.scan["hop"] + s.scan["k"] / max(1, s.scan["K"])) / s.scan["hops"]
        g.rect(p.x0, p.y0 - 2, p.x0 + p.w * prog, p.y0 + 1, fill="#4caf50")
    # подвал: состояние NMEA / АИС
    ft, ffs = L.foot_text, max(9.0, fs * 0.72)
    txt = s.footer
    while len(txt) > 4 and g.measure(txt, ffs, False) > ft.w - 4:
        txt = txt[:-2]
    if txt != s.footer:
        txt = txt[:-1] + "…"
    g.text(ft.x0 + 2, ft.cy, txt, ffs, "#4fc3f7", False, "w")
    g.end()


def draw_all(g, L, s):
    draw_chassis(g, L, s)
    draw_keys(g, L, s)
    draw_lcd(g, L, s)
    draw_sliders(g, L, s)
    draw_chart(g, L, s)


# ---------------------------------------------------------------- мышь
def hit(L, x, y):
    """Что под курсором: ('key', имя) | ('chip', имя) | ('slider', 'sql'|'vol') | ('chart', индекс колонки, half) | None."""
    for n, r in L.chips.items():
        if r.has(x, y):
            return ("chip", n)
    for n, r in L.keys.items():
        if r.has(x, y):
            return ("key", n)
    for n, r in (("sql", L.sl_sql), ("vol", L.sl_vol)):
        if r.has(x, y):
            return ("slider", n)
    if L.plot.has(x, y):
        for i, (x0, x1) in enumerate(chart_cols(L)):
            if x0 <= x < x1:
                return ("chart", i, (x - x0) / max(1e-6, x1 - x0))
    return None


def slider_value(L, name, y):
    r = L.sl_sql if name == "sql" else L.sl_vol
    top, bot = slider_geom(r)
    return max(0, min(10, int(round((bot - y) / (bot - top) * 10))))
