"""core.gnssfix(micropyGPS -> Status、RTC 合わせ)と状態表示の衛星行のテスト。PC と実機の両方で動く。

GSA は実機の MAX-M10M から取ったもの。RMC の位置は架空(チェックサムはここで計算する)。
"""

import time

from core import geo
from core.gnssfix import RtcSync, update_status, utc_s
from micropyGPS import MicropyGPS
from ui.views import Status, gnss_text, status_lines

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


RMC = with_cs("GNRMC,234833.00,A,3530.00000,N,13930.00000,E,0.119,,241026,,,A,V")
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
    assert near(st.lat, 35.5) and near(st.lon, 139.5)
    assert utc_s(gps) == 1792885713
    feed(gps, [RMC_VOID])
    update_status(st, gps)
    assert st.fix == 0 and st.lat is None


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


def test_gnss_text():
    st = Status()
    st.fix, st.sats, st.sats_view, st.hdop = 3, 21, 38, 0.66
    assert gnss_text(st, 30) == "3D 21/38 HDOP0.7"
    assert gnss_text(st, 10) == "3D 21/38"  # 狭い画面では HDOP を省く
    st.fix, st.sats = 0, 0
    assert gnss_text(st, 30) == "NO FIX 0/38"
    st.fix, st.sats, st.utc_s = 3, 21, 1792885713
    line = status_lines(st, 27)[0][0]
    assert line == "3D 21/38 HDOP0.7   08:48:33" and len(line) <= 27
