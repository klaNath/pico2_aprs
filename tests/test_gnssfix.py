"""core.gnssfix(micropyGPS -> Status、RTC 合わせ)と状態表示の衛星行のテスト。PC と実機の両方で動く。

GSA は実機の MAX-M10M から取ったもの。RMC の位置は架空(チェックサムはここで計算する)。
"""

import time

from core import geo
from core.gnssfix import GNSS_GPS, GNSS_QZSS, RtcSync, nav_sat, update_status, utc_s
from micropyGPS import MicropyGPS
from ui.views import Status

GSA = [
    "$GNGSA,A,3,20,18,05,21,24,15,13,14,12,23,,,1.30,0.68,1.10,1*07",
    "$GNGSA,A,3,66,88,81,,,,,,,,,,1.30,0.68,1.10,2*07",
    "$GNGSA,A,3,08,15,13,03,25,32,02,,,,,,1.30,0.68,1.10,3*06",
    "$GNGSA,A,3,,,,,,,,,,,,,1.30,0.68,1.10,5*09",
]


def with_cs(body):
    c = 0
    for ch in body.encode():
        c ^= ch
    return "$%s*%02X" % (body, c)


RMC = with_cs("GNRMC,234833.00,A,3530.60000,N,13930.60000,E,0.119,,241026,,,A,V")
RMC_VOID = with_cs("GNRMC,234833.00,V,,,,,,,241026,,,N,V")


def near(a, b, tol=1e-6):
    return a is not None and abs(a - b) <= tol


def feed(gps, lines):
    for line in lines:
        for ch in line + "\r\n":
            gps.update(ch)


def test_utc_seconds():
    assert geo.utc_seconds(1970, 1, 1, 0, 0, 0) == 0
    assert geo.utc_seconds(2026, 10, 24, 23, 48, 33) == 1792885713
    if hasattr(time, "gmtime"):
        assert time.gmtime(1792885713)[:6] == (2026, 10, 24, 23, 48, 33)


def test_update_status_fix():
    gps = MicropyGPS(location_formatting="dd")
    st = Status()
    update_status(st, gps)
    assert st.fix == 0 and st.lat is None and utc_s(gps) is None
    feed(gps, GSA + [RMC])
    gps.satellites_in_view = 38
    update_status(st, gps)
    assert st.fix == 3 and st.sats == 20 and st.sats_view == 38
    assert near(st.hdop, 0.68)
    assert near(st.lat, 35.51) and near(st.lon, 139.51)
    assert st.grid == "PM95SM12"
    assert utc_s(gps) == 1792885713
    feed(gps, [RMC_VOID])
    update_status(st, gps)
    assert st.fix == 0 and st.lat is None and st.grid is None


def test_rtc_sync_first_then_hourly():
    calls = []
    sync = RtcSync(calls.append, interval_ms=3600_000)
    gps = MicropyGPS(location_formatting="dd")
    feed(gps, [RMC_VOID])
    assert not sync.on_rmc(gps, now_ms=0) and not sync.synced
    feed(gps, [RMC])
    assert sync.on_rmc(gps, now_ms=1000) and calls == [1792885713]
    assert not sync.on_rmc(gps, now_ms=1000 + 3599_000)
    assert sync.on_rmc(gps, now_ms=1000 + 3600_000) and len(calls) == 2


def test_gridlocator():
    assert geo.gridlocator(41.714775, -72.727260)[:6] == "FN31PR"  # W1AW
    assert geo.gridlocator(-33.8568, 151.2153)[:6] == "QF56OD"  # シドニー(南緯・東経)
    assert geo.gridlocator(35.681236, 139.767125) == "PM95VQ23"  # 東京駅
    assert geo.gridlocator(90.0, 180.0) == "RR99XX99"
    assert geo.gridlocator(-90.0, -180.0) == "AA00AA00"


def sat_block(gnss, sv, cno, flags):
    return bytes((gnss, sv, cno, 30, 0, 0, 0, 0)) + flags.to_bytes(4, "little")


def test_nav_sat():
    used = 0x08
    blocks = [sat_block(GNSS_QZSS, 2, 32, used | 0x40000),  # QZSS 使用中、SLAS 補正を使用
              sat_block(GNSS_GPS, 5, 17, used),
              sat_block(GNSS_GPS, 20, 10, 0),
              sat_block(GNSS_GPS, 30, 0, 0)]  # 捕捉していない
    p = bytes((0, 0, 0, 0, 1, len(blocks), 0, 0)) + b"".join(blocks)
    signals, aug = nav_sat(p)
    assert signals == [(GNSS_GPS, 17, True), (GNSS_GPS, 10, False), (GNSS_QZSS, 32, True)]
    assert aug == ("QZSS", "SLAS")
    blocks[1] = sat_block(GNSS_GPS, 5, 17, used | 0x10000)  # SBAS 補正を使用
    p = bytes((0, 0, 0, 0, 1, 2, 0, 0)) + b"".join(blocks[1:3])
    assert nav_sat(p) == ([(GNSS_GPS, 17, True), (GNSS_GPS, 10, False)], ("SBAS",))
    assert nav_sat(b"") == ([], ())
