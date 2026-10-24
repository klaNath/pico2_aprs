"""GT-505GGBL5-DR-N(旧 GNSS モジュール)の動作確認: NMEA を読み、測位状態・位置・JST 時刻を表示する(画面と USB の両方)。

    python tools/dev.py run src/gt505_demo.py -t 130

2 分間動いて止まる。屋内では測位できないことが多いので窓際か屋外で。
最初の数秒の生の行も表示するので、配線(115200 bps、TXD -> GP1)が合っているか確認できる。
"""

import time

from machine import SPI, Pin

import config
from core import geo
from core.stations import Stations
from hw.gnss import Gnss
from hw.st7789 import ST7789
from ui.controller import Ui
from ui.screen import Screen
from ui.views import Status

DURATION_MS = 120_000


def main():
    gnss = Gnss(config.GNSS_UART, config.PIN_GNSS_TX, config.PIN_GNSS_RX, config.GNSS_BAUD)
    spi = SPI(config.LCD_SPI, baudrate=config.LCD_BAUD, sck=Pin(config.PIN_LCD_SCK),
              mosi=Pin(config.PIN_LCD_MOSI))
    lcd = ST7789(spi, config.PIN_LCD_DC, config.PIN_LCD_CS, config.PIN_LCD_BL, config.LCD_PANEL)
    status = Status()
    ui = Ui(Screen(lcd), Stations(), status,
            config.BUTTONS)
    lcd.backlight(0.8)

    # 最初の 3 秒は生の行をそのまま出す(配線とボーレートの確認用)
    t0 = time.ticks_ms()
    raw = 0
    while time.ticks_diff(time.ticks_ms(), t0) < 3000 and raw < 12:
        data = gnss.uart.read()
        if data:
            for line in gnss.reader.feed(data):
                print("raw:", line)
                gnss.fix.feed(line)
                raw += 1
        time.sleep_ms(50)
    if raw == 0:
        print("no data from GNSS: check wiring (module TXD orange -> GP1), power, 115200 bps")

    t_print = t0
    while time.ticks_diff(time.ticks_ms(), t0) < DURATION_MS:
        gnss.poll()
        f = gnss.fix
        status.fix = f.fix if f.valid or f.fix else 0
        status.sats = f.sats
        status.lat, status.lon = (f.lat, f.lon) if f.valid else (None, None)
        status.utc_s = f.utc_s
        ui.poll()
        now = time.ticks_ms()
        if time.ticks_diff(now, t_print) >= 5000:
            t_print = now
            pos = geo.fmt_latlon(f.lat, f.lon) if f.lat is not None else "no position"
            print("fix=%d valid=%s sats=%d hdop=%s alt=%s %s JST %s | lines %d bytes %d"
                  % (f.fix, f.valid, f.sats, f.hdop, f.alt, pos,
                     geo.fmt_jst(f.utc_s) if f.utc_s is not None else "--", gnss.lines,
                     gnss.bytes))
        time.sleep_ms(100)
    print("RTC now (UTC):", time.gmtime())


main()
