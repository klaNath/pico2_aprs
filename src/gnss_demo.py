"""GNSS(MAX-M10M)の動作確認: 測位状態・衛星数・位置・JST 時刻を画面の自局欄と USB に出す。

    python tools/dev.py run src/gnss_demo.py -t 60

45 秒動いて止まる。有効な RMC を受けると RTC を UTC に合わせる(その後は 1 時間ごと)。
"""

import time

from machine import SPI, Pin

import config
from core.stations import Stations
from hw.m10 import M10
from hw.st7789 import ST7789
from ui.controller import Ui
from ui.screen import Screen
from ui.views import Status, status_lines

DURATION_MS = 45_000


def main():
    gnss = M10(config.GNSS_UART, config.PIN_GNSS_TX, config.PIN_GNSS_RX,
               resetn=config.PIN_GNSS_RESETN, pps=config.PIN_GNSS_PPS, baud=config.GNSS_BAUD,
               log=print)
    gnss.begin()
    spi = SPI(config.LCD_SPI, baudrate=config.LCD_BAUD, sck=Pin(config.PIN_LCD_SCK),
              mosi=Pin(config.PIN_LCD_MOSI))
    lcd = ST7789(spi, config.PIN_LCD_DC, config.PIN_LCD_CS, config.PIN_LCD_BL, config.LCD_PANEL)
    status = Status()
    ui = Ui(Screen(lcd), Stations(), status, config.BUTTONS)
    lcd.backlight(0.8)
    print("RTC before (UTC):", time.gmtime()[:6])

    t0 = time.ticks_ms()
    t_print = t0
    synced = False
    while time.ticks_diff(time.ticks_ms(), t0) < DURATION_MS:
        gnss.poll(status)
        ui.poll()
        if gnss.rtc.synced and not synced:
            synced = True
            print("RTC set (UTC):", time.gmtime()[:6])
        now = time.ticks_ms()
        if time.ticks_diff(now, t_print) >= 5000:
            t_print = now
            lines = status_lines(status, ui.lay.status_cols, ui.lay.status_rows)
            print(" | ".join(s for s, _ in lines[:4]))
        time.sleep_ms(50)
    print("sentences %d, RTC now (UTC): %s" % (gnss.sentences, time.gmtime()[:6]))


main()
