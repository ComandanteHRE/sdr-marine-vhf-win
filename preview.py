"""Рендер панели в PNG без Tk (проверка раскладки): python preview.py 560 860 out.png"""
import sys
from types import SimpleNamespace as NS

import gfx
import ui
from panel_logic import PanelLogic


def demo_state(**kw):
    lg = PanelLogic(16, False)
    sd = {(ch, True): 0.0 for ch in ui.CHANNELS}
    sd.update({(16, True): 31.0, (12, True): 14.0, (6, True): 22.0, (22, False): 17.0, (9, True): 3.0, (67, True): 6.0, (73, False): 5.0})
    s = NS(logic=lg, mode="VOICE", rx_open=True, rssi=24, blink=True, rec_secs=75, scanning=False, ais_on=False,
           ais_count=12, ais_levels=(-60, -62), scan=None, scan_data=sd, found=["16", "12", "6"], error="",
           connected=True, thr=8.0, vol=6, sql=3, pressed=None, autostop=False, flash="",
           footer="NMEA: TCP 127.0.0.1:10110 (клиентов 1) · UDP → 127.0.0.1:10110 · АИС принято: 12")
    s.__dict__.update(kw)
    return s


if __name__ == "__main__":
    W, H, out = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
    s = demo_state()
    if len(sys.argv) > 4 and sys.argv[4] == "err":
        s.error, s.connected, s.rx_open, s.rec_secs = "RTL-SDR: не найден ключ (подключите приёмник)", False, False, None
    L = ui.make_layout(W, H)
    g = gfx.PilGfx(W, H)
    ui.draw_all(g, L, s)
    g.save(out)
    print(out, "landscape" if L.land else "portrait")
