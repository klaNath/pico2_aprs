"""core.geo / core.stations のテスト。PC と実機の両方で動く。

距離・方位の期待値は geographiclib(WGS84 楕円体)の値。ここは球面近似なので
0.5% 程度の差は許す(表示は 0.1 km 単位と 16 方位なので十分)。
"""

from aprs.packet import parse_frame
from core import geo
from core.stations import STALE_S, Stations
from tnc.ax25 import Frame

TOKYO = (35.681236, 139.767125)


def near(a, b, tol):
    return abs(a - b) <= tol


def test_distance_and_bearing():
    cases = (
        ((35.658581, 139.745433), 3.190, 218.01),  # 東京タワー
        ((35.444, 139.638), 28.807, 204.01),  # 横浜付近
        ((35.170915, 136.881537), 268.083, 258.65),  # 名古屋
    )
    for (lat, lon), km, az in cases:
        d = geo.distance_km(TOKYO[0], TOKYO[1], lat, lon)
        b = geo.bearing_deg(TOKYO[0], TOKYO[1], lat, lon)
        assert near(d, km, km * 0.005 + 0.01), (lat, d, km)
        assert near(b, az, 0.5), (lat, b, az)


def test_compass16():
    assert geo.compass16(0) == "N"
    assert geo.compass16(11.24) == "N"
    assert geo.compass16(11.26) == "NNE"
    assert geo.compass16(218.0) == "SW"
    assert geo.compass16(204.0) == "SSW"
    assert geo.compass16(359.0) == "N"
    assert geo.compass16(-90) == "W"


def test_fmt_latlon():
    assert geo.fmt_latlon(35.681236, 139.767125) == "N35 40.87 E139 46.03"
    assert geo.fmt_latlon(-33.8568, -0.5) == "S33 51.41 W000 30.00"
    assert len(geo.fmt_latlon(89.99, 179.99)) == 20


def test_log_radius():
    r = 50
    assert geo.log_radius(0.1, r) == 0
    assert geo.log_radius(100, r) == r
    assert geo.log_radius(500, r) == r
    r1 = geo.log_radius(1, r)
    r10 = geo.log_radius(10, r)
    assert 0 < r1 < r10 < r
    # 対数スケール: 1->10 km と 10->100 km の間隔は等しい
    assert near(r10 - r1, r - r10, 1e-3)
    x, y = geo.map_xy(10, 90, 100, 60, r)
    assert y == 60 and x > 100  # 東は右
    x, y = geo.map_xy(10, 0, 100, 60, r)
    assert x == 100 and y < 60  # 北は上


def test_fmt_jst_and_age():
    assert geo.fmt_jst(0) == "09:00:00"
    assert geo.fmt_jst(15 * 3600 + 1) == "00:00:01"
    assert geo.fmt_age(5) == "5s"
    assert geo.fmt_age(125) == "2m"
    assert geo.fmt_age(7300) == "2h"


def P(tnc2):
    return parse_frame(Frame.parse(tnc2))


def test_stations_order_stale_and_map():
    s = Stations(max_stations=4)
    t = 1000
    s.update(P("JA1AAA>APRS:!3540.12N/13945.67E>a"), t)
    s.update(P("JA1BBB>APRS:>status only"), t + 1000)
    s.update(P("JA1AAA>APRS:>now status"), t + 2000)
    assert [st.call for st in s.order] == ["JA1AAA", "JA1BBB"]
    a = s.by_call["JA1AAA"]
    assert a.count == 2 and a.lat is not None  # 位置の無いパケットでも前の位置を残す
    assert a.packet.kind == "status"
    now = t + 2000 + STALE_S * 1000
    assert a.stale(now) and not a.stale(now - 1000)
    for k in range(5):
        s.update(P("JR1C%02d>APRS:!35%02d.00N/13945.00E>x" % (k, 30 + k)), now + k)
    assert len(s) == 4  # 上限を超えたら古い局から捨てる
    assert "JA1AAA" not in s.by_call
    assert [st.call for st in s.for_map(3)] == ["JR1C04", "JR1C03", "JR1C02"]


def test_object_is_tracked_by_name():
    s = Stations()
    st = s.update(P("JA1AAA>APRS:;EVENT    *092345z3540.12N/13945.67E>x"), 0)
    assert st.call == "EVENT"
