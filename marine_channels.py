"""Международная сетка морских УКВ-каналов (ITU-R M.1084 / RR Appendix 18).

Каналы 1-28 и 60-88, шаг 25 кГц.
  судовая частота передачи:  1-28 -> 156.000 + 0.050*n МГц
                             60-88 -> 156.025 + 0.050*(n-60) МГц
  береговая (дуплекс):       судовая + 4.600 МГц
Каналы 87B / 88B (161.975 / 162.025 МГц) — АИС 1 / АИС 2.
"""

DUPLEX = {1, 2, 3, 4, 5, 7, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28,
          60, 61, 62, 63, 64, 65, 66, 78, 79, 80, 81, 82, 83, 84, 85, 86}

CHANNELS = list(range(1, 29)) + list(range(60, 89))   # порядок «по кругу» для ▲/▼
AIS1_HZ = 161_975_000
AIS2_HZ = 162_025_000
DUPLEX_OFFSET_HZ = 4_600_000

NOTES = {
    16: "Вызов/бедствие",
    70: "DSC (цифровой вызов)",
    6: "Связь с воздушными судами (SAR)",
    13: "Навигационная безопасность мост-мост",
    9: "Вызывной (яхты/порты)",
    67: "Малые суда / SAR",
    72: "Межсудовой",
    75: "Защитная полоса", 76: "Защитная полоса",
}


def ship_hz(ch: int) -> int:
    """Частота передачи судна (кГц-точность, Гц)."""
    if 1 <= ch <= 28:
        return 156_000_000 + 50_000 * ch
    if 60 <= ch <= 88:
        return 156_025_000 + 50_000 * (ch - 60)
    raise ValueError(f"нет такого канала: {ch}")


def coast_hz(ch: int) -> int:
    return ship_hz(ch) + (DUPLEX_OFFSET_HZ if ch in DUPLEX else 0)


def rx_hz(ch: int, listen_ship: bool = False) -> int:
    """Частота приёма: как у судовой рации — береговая сторона дуплекса.
    listen_ship=True слушает судовую сторону (передачи судов)."""
    return ship_hz(ch) if listen_ship else coast_hz(ch)


def is_valid(ch: int) -> bool:
    return 1 <= ch <= 28 or 60 <= ch <= 88


def next_channel(ch: int, step: int) -> int:
    i = CHANNELS.index(ch) if ch in CHANNELS else 0
    return CHANNELS[(i + step) % len(CHANNELS)]


def freq_to_channel(hz: float, tol_hz: float = 7_000):
    """(номер канала, listen_ship) если частота совпадает с каналом, иначе None."""
    for ch in CHANNELS:
        if abs(hz - ship_hz(ch)) <= tol_hz:
            return ch, True
        if ch in DUPLEX and abs(hz - coast_hz(ch)) <= tol_hz:
            return ch, False
    return None


def scan_targets():
    """Все частоты для обзора: [(label, hz, ch)], без повторов."""
    out = {}
    for ch in CHANNELS:
        out[ship_hz(ch)] = (str(ch), ship_hz(ch), ch)
        if ch in DUPLEX:
            out[coast_hz(ch)] = (f"{ch}", coast_hz(ch), ch)
    return sorted(out.values(), key=lambda t: t[1])


# Две «ступени» обзора при fs = 1.92 МГц (центры сдвинуты на 12.5 кГц от сетки,
# чтобы пик постоянной составляющей RTL-SDR не попал на канал)
SCAN_HOPS = [156_712_500, 161_312_500]
