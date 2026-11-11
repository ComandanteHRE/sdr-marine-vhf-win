"""Логика панели (без Tk): ввод канала/частоты, ▲▼, канал 16, сторона дуплекса."""
from marine_channels import (CHANNELS, DUPLEX, is_valid, next_channel, rx_hz, ship_hz, freq_to_channel,
                             NOTES)

MIN_HZ, MAX_HZ = 24_000_000, 1_766_000_000     # диапазон RTL-SDR с тюнером R820T/R820T2


def parse_freq(text):
    """'156.8' -> МГц; только цифры -> кГц ('156800', '88000')."""
    if not text:
        raise ValueError
    hz = float(text) * 1e6 if "." in text else int(text) * 1e3
    if not MIN_HZ <= hz <= MAX_HZ:
        raise ValueError
    return int(round(hz))


class PanelLogic:
    def __init__(self, channel=16, listen_ship=False):
        self.input_mode = "CH"           # "CH" — вводим канал, "FREQ" — частоту (то же показывает LCD)
        self.channel = channel if is_valid(channel) else 16
        self.listen_ship = listen_ship
        self.freq_hz = rx_hz(self.channel, listen_ship)
        self.buf = ""
        self.message = ""

    # ---- состояние
    @property
    def note(self):
        return NOTES.get(self.channel, "") if self.channel else ""

    @property
    def duplex(self):
        return bool(self.channel in DUPLEX)

    def _set_channel(self, ch, listen_ship=None):
        self.channel = ch
        if listen_ship is not None:
            self.listen_ship = listen_ship
        self.freq_hz = rx_hz(ch, self.listen_ship)
        self.buf, self.message = "", ""
        return True

    # ---- кнопки
    def toggle_input(self):
        self.input_mode = "FREQ" if self.input_mode == "CH" else "CH"
        self.buf, self.message = "", ""

    def key(self, k):
        limit = 2 if self.input_mode == "CH" else 9
        if k == "." and (self.input_mode == "CH" or "." in self.buf):
            return
        if len(self.buf) < limit:
            self.buf += k
        self.message = ""

    def clear(self):
        self.buf = self.buf[:-1]
        self.message = ""

    def enter(self):
        """True, если нужно перестроить приёмник."""
        if not self.buf:
            return False
        try:
            if self.input_mode == "CH":
                ch = int(self.buf)
                if not is_valid(ch):
                    raise ValueError
                return self._set_channel(ch)
            return self.goto_freq(parse_freq(self.buf))
        except ValueError:
            self.buf, self.message = "", "ОШИБКА"
            return False

    def goto_freq(self, hz):
        found = freq_to_channel(hz)
        if found:
            return self._set_channel(found[0], found[1])
        self.channel, self.freq_hz, self.buf, self.message = None, int(hz), "", ""
        return True

    def goto_channel(self, ch, listen_ship=None):
        return self._set_channel(ch, listen_ship)

    def ch16(self):
        return self._set_channel(16)

    def step(self, d):
        ch = self.channel
        if ch is None:                              # вне сетки: ближайший канал и шаг от него
            ch = min(CHANNELS, key=lambda c: abs(rx_hz(c, self.listen_ship) - self.freq_hz))
        return self._set_channel(next_channel(ch, d))

    def toggle_side(self):
        """Дуплекс: слушать береговую (как судовая рация) или судовую сторону."""
        self.listen_ship = not self.listen_ship
        if self.channel:
            self._set_channel(self.channel)
            return self.duplex
        return False
