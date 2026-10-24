"""MAX-M10M の通信確認(手順 3)。dev.py run src/m10_check.py -t 60

1. ボーレート検出と 115200 への設定(begin。BBR 保存済みなら書かない。ハードリセットはしない)
2. MON-VER(ファームウェア版)
3. NMEA の受信(文の種類ごとの数)
4. micropyGPS での解析結果
"""

import gc
import time

import config
from maxm10 import MaxM10
from micropyGPS import MicropyGPS

SECONDS = 40

counts = {}
gps = MicropyGPS(location_formatting="dd")
parsed = {}
bad = [0]


def on_nmea(line):
    key = line[1:6].decode()
    counts[key] = counts.get(key, 0) + 1
    for b in line:
        r = gps.update(chr(b))
        if r:
            parsed[r] = parsed.get(r, 0) + 1


gc.collect()
free0 = gc.mem_free()
m10 = MaxM10(config.GNSS_UART, config.PIN_GNSS_TX, config.PIN_GNSS_RX,
             resetn=config.PIN_GNSS_RESETN, pps=config.PIN_GNSS_PPS,
             on_nmea=on_nmea, log=print)
t0 = time.ticks_ms()
ver = m10.begin(config.GNSS_BAUD)
print("begin: %d ms, baud=%d" % (time.ticks_diff(time.ticks_ms(), t0), m10.baud))
for s in ver or ():
    print("  ver:", s)

t0 = time.ticks_ms()
t_print = t0
while time.ticks_diff(time.ticks_ms(), t0) < SECONDS * 1000:
    if not m10.update():
        time.sleep_ms(5)
    if time.ticks_diff(time.ticks_ms(), t_print) >= 5000:
        t_print = time.ticks_ms()
        print("[%2ds] fix=%s valid=%s utc=%s %s lat=%s lon=%s alt=%s sats=%d/%d hdop=%s" % (
            time.ticks_diff(t_print, t0) // 1000, gps.fix_stat, gps.valid, gps.date,
            gps.timestamp, gps.latitude, gps.longitude, gps.altitude,
            gps.satellites_in_use, gps.satellites_in_view, gps.hdop))
        print("      view=%s used=%s fix_type=%d pdop=%s" % (
            sorted((k, len(v)) for k, v in gps.satellite_data_by_system.items()),
            sorted((k, len(v)) for k, v in gps.satellites_used_by_system.items()),
            gps.fix_type, gps.pdop))

print("NMEA 受信数:", sorted(counts.items()))
print("micropyGPS 解析数:", sorted(parsed.items()))
print("clean=%d crc_fail=%d parsed=%d" % (gps.clean_sentences, gps.crc_fails, gps.parsed_sentences))
gc.collect()
print("RAM 使用: %d bytes" % (free0 - gc.mem_free()))
