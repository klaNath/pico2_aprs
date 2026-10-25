"""画面表示の確認: 試験用の局データで、メイン画面と地図画面を表示する(無線機・GNSS 不要)。

    python tools/dev.py run src/ui_demo.py -t 75

自局は東京駅付近に固定。0.8 / 3 / 12 / 45 / 90 / 150 km の局と、位置の無い局(ステータス・
メッセージ)、60 分以上前に受信した「古い」局を入れる。8 秒ごとに 1 局ずつ新しい受信を模擬する。
320x240 では小さな地図が常に出る。ボタン(240x135 の Display Pack のみ)A/B: 一覧の選択、
X: 地図、Y: 最新局の自動表示に戻る。60 秒で終了。
"""

import time

from machine import SPI, Pin

import config
from aprs import mice
from aprs.packet import parse, parse_frame
from core import geo
from core.stations import Stations
from hw.st7789 import ST7789
from tnc.ax25 import Frame
from ui.controller import Ui
from ui.screen import Screen
from ui.views import Status

OWN = (35.681236, 139.767125)


def offset(km, deg):
    """自局から km / deg の位置(近似)"""
    import math

    dlat = km / 111.0 * math.cos(math.radians(deg))
    dlon = km / (111.0 * math.cos(math.radians(OWN[0]))) * math.sin(math.radians(deg))
    return OWN[0] + dlat, OWN[1] + dlon


def mice_packet(call, km, deg, spd, crs, sym, comment):
    lat, lon = offset(km, deg)
    dst, info = mice.encode(lat, lon, spd, crs, "/", sym, 1, 40, comment)
    return parse(call, dst, ["JA1DG-1*", "WIDE2-1"], info)


def pos_packet(call, km, deg, sym, comment):
    lat, lon = offset(km, deg)
    s = geo.fmt_dm(lat, "N", "S", 2).replace(" ", "")[1:] + ("N" if lat >= 0 else "S")
    s2 = geo.fmt_dm(lon, "E", "W", 3).replace(" ", "")[1:] + ("E" if lon >= 0 else "W")
    return parse_frame(Frame.parse("%s>APRS,WIDE2-1:!%s/%s%s%s" % (call, s, s2, sym, comment)))


SAMPLES = [
    lambda: mice_packet("JA1XYZ-9", 0.8, 30, 4.0, 45, ">", b"near car"),
    lambda: pos_packet("JR1ABC", 3.0, 45, "-", "home QTH"),
    lambda: mice_packet("JH1DEF-7", 12.0, 180, 30.0, 200, "[", b"walking"),
    lambda: pos_packet("7K1GHI-10", 45.0, 270, "#", "digi WIDE2"),
    lambda: parse_frame(Frame.parse("JE1MNO>APRS::JA1XYZ-9 :Hello from Pico{12")),
    lambda: mice_packet("JA2STU-9", 90.0, 22, 80.0, 30, ">", b"highway"),
    lambda: pos_packet("JA0FAR", 150.0, 315, "-", "beyond 100km"),
    lambda: parse_frame(Frame.parse("JF1STA>APRS:>Status text only")),
    # 自局のビーコンがデジピーターで中継されて戻ってきたもの(地図に出ず、一覧はマゼンタ)
    lambda: mice_packet("%s-%d" % (config.CALLSIGN, config.SSID), 0.0, 0, 0.0, 0, "[",
                        b"own echo"),
]


def main():
    spi = SPI(config.LCD_SPI, baudrate=config.LCD_BAUD, sck=Pin(config.PIN_LCD_SCK),
              mosi=Pin(config.PIN_LCD_MOSI))
    lcd = ST7789(spi, config.PIN_LCD_DC, config.PIN_LCD_CS, config.PIN_LCD_BL, config.LCD_PANEL)
    scr = Screen(lcd)
    own = config.CALLSIGN if config.SSID == 0 else "%s-%d" % (config.CALLSIGN, config.SSID)
    stations = Stations(own_call=own)
    status = Status()
    status.fix, status.sats, status.sats_view, status.hdop = 3, 21, 38, 0.7
    status.lat, status.lon = OWN
    status.utc_s = time.time()
    status.beacon_on = True
    ui = Ui(scr, stations, status,
            config.BUTTONS)
    now = time.ticks_ms()
    # 古い局(61 分前と 90 分前に受信したことにする)
    stations.update(pos_packet("JA1OLD", 20.0, 120, "-", "old station"),
                    time.ticks_add(now, -90 * 60_000))
    stations.update(pos_packet("JA1OLD-2", 6.0, 200, "-", "old 2"),
                    time.ticks_add(now, -61 * 60_000))
    for k in range(5):
        stations.update(SAMPLES[k](), time.ticks_add(now, -(5 - k) * 20_000))
        status.rx_count += 1
    lcd.backlight(0.8)
    t_end = time.ticks_add(now, 60_000)
    t_next = time.ticks_add(now, 8_000)
    k = 5
    nb = 180  # 次のビーコンまでの秒(表示の確認用に 3 分から数える)
    t_sec = now
    t_draw = 0
    while time.ticks_diff(t_end, time.ticks_ms()) > 0:
        now = time.ticks_ms()
        if time.ticks_diff(now, t_next) >= 0:
            stations.update(SAMPLES[k % len(SAMPLES)](), now)
            status.rx_count += 1
            status.rx_recent = True
            k += 1
            t_next = time.ticks_add(now, 8_000)
            latest = stations.latest()
            print("new packet:", latest.call if latest else None)
        if time.ticks_diff(now, t_sec) >= 1000:
            t_sec = time.ticks_add(t_sec, 1000)
            nb = nb - 1 if nb > 0 else 180
        status.utc_s = time.time()
        status.next_beacon_s = nb
        t0 = time.ticks_us()
        ui.poll(now)
        t_draw = max(t_draw, time.ticks_diff(time.ticks_us(), t0))
        time.sleep_ms(50)
    print("done; max poll+draw %d ms" % (t_draw // 1000))


main()
