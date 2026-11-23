"""core.geo / core.stations のテスト。PC と実機の両方で動く。

距離・方位の期待値は geographiclib(WGS84 楕円体)の値。ここは球面近似なので
0.5% 程度の差は許す(表示は 0.1 km 単位と 16 方位なので十分)。
コールサインはすべて架空の例。
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


def test_own_echo_flagged_and_not_on_map():
    s = Stations(own_call="JA1ME-7")
    s.update(P("JA1AAA>APRS:!3540.12N/13945.67E>a"), 0)
    s.update(P("JA1ME-7>APZPI2,JA1DG-1*,WIDE2-1:!3540.00N/13945.00E>my beacon"), 10)
    s.update(P("JA1ME>APRS:!3541.00N/13945.00E>other ssid"), 20)  # SSID 違いは別局
    s.update(P("JA1ME-9>APRS:!3542.00N/13945.00E>other ssid"), 30)
    s.update(P("JA1MEX>APRS:!3543.00N/13945.00E>longer call"), 40)  # 頭が同じだけの別の局
    me = s.by_call["JA1ME-7"]
    assert me.own and not s.by_call["JA1ME"].own and not s.by_call["JA1AAA"].own
    assert s.latest() is not me  # 一覧・詳細には出る(最新ではないだけ)
    assert me in s.order and s.by_call["JA1ME-9"] in s.order  # 一覧には SSID 違いも出る
    assert me not in s.for_map()
    # 地図には自局と同じコールサインの局を SSID を問わず出さない
    assert [st.call for st in s.for_map()] == ["JA1MEX", "JA1AAA"]
    s.own_call = "JA1ME"  # SSID 0 の自局でも同じ
    assert [st.call for st in s.for_map()] == ["JA1MEX", "JA1AAA"]


def test_same_call_ignores_only_the_ssid():
    from core.stations import same_call

    assert same_call("Q1TEST", "Q1TEST") and same_call("Q1TEST-15", "Q1TEST")
    assert not same_call("Q1TESTA", "Q1TEST") and not same_call("Q1TES", "Q1TEST")
    assert not same_call("XQ1TEST-1", "Q1TEST")


def test_object_is_tracked_by_name():
    s = Stations()
    st = s.update(P("JA1AAA>APRS:;EVENT    *092345z3540.12N/13945.67E>x"), 0)
    assert st.call == "EVENT"
    assert st.obj and not st.own
    assert "JA1AAA" not in s.by_call  # オブジェクトだけを送った局は表に入らない
    a = s.update(P("JA1AAA>APRS:!3541.00N/13946.00E>a"), 10)
    assert not a.obj and a.count == 1 and st.count == 1


def test_object_named_like_a_station_is_kept_apart():
    """オブジェクト名が局のコールサインと同じでも別の件(docs/ble-protocol.md §8.4。架空の局)"""
    s = Stations(max_stations=4, own_call="Q1ME")
    me = s.update(P("Q1ME>APRS:!3540.00N/13945.00E>my beacon"), 0)
    o = s.update(P("Q1BBB>APRS:;Q1ME     *092345z3550.00N/13955.00E>x"), 10)
    i = s.update(P("Q1BBB>APRS:)Q1AAA!3530.00N/13935.00E>item"), 20)
    a = s.update(P("Q1AAA>APRS:!3541.00N/13946.00E>a"), 30)
    assert len(s) == 4 and o is not me and i is not a and "Q1BBB" not in s.by_call
    assert me.own and not me.obj and o.obj and not o.own and o.call == "Q1ME"
    assert i.obj and i.call == "Q1AAA" and i.lat is not None and i.lat < 35.6
    assert [x.call for x in s.order] == ["Q1AAA", "Q1AAA", "Q1ME", "Q1ME"]
    s.update(P("Q1CCC>APRS:>x"), 40)  # 上限で捨てるのは局 Q1ME。同じ名前のオブジェクトは残る
    assert len(s.by_call) == len(s.order) == 4
    assert me not in s.order and o in s.order and s.by_call[("obj", "Q1ME")] is o
