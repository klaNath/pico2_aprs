"""core.beacon(ビーコンの送出時機と Mic-E フレーム)のテスト。PC と実機の両方で動く。"""

from aprs import mice
from aprs.packet import parse_frame
from core.beacon import Beacon, mice_frame

LAT, LON = 35.681236, 139.767125
NEAR = (35.681236 + 5e-5, 139.767125)  # 約 5.6 m 北
FAR = (35.681236 + 2e-4, 139.767125)  # 約 22 m 北


def test_first_beacon_is_immediate():
    b = Beacon(90, 600, 10.0)
    assert b.next_s(None, None, 0) is None  # 位置が分からなければ送らない
    assert b.due(LAT, LON, 0)
    b.sent(LAT, LON, 0)
    assert b.next_s(LAT, LON, 1000) == 599


def test_still_and_moving_intervals():
    b = Beacon(90, 600, 10.0)
    b.sent(LAT, LON, 0)
    assert not b.moved(*NEAR) and b.moved(*FAR)
    assert b.next_s(NEAR[0], NEAR[1], 89_000) == 511  # 10 m 未満: 10 分ごと
    assert not b.due(NEAR[0], NEAR[1], 599_000) and b.due(NEAR[0], NEAR[1], 600_000)
    assert b.next_s(FAR[0], FAR[1], 60_000) == 30  # 10 m 以上: 1.5 分ごと
    assert not b.due(FAR[0], FAR[1], 89_000) and b.due(FAR[0], FAR[1], 90_000)
    b.reset()  # スイッチを切って入れ直したらすぐ
    assert b.due(LAT, LON, 1000)


def test_mice_frame():
    f = mice_frame("JK1BBJ-7", ("WIDE1-1", "WIDE2-1"), LAT, LON, speed_kn=10.0, course=0.2,
                   alt=40.0, symbol=("/", "["), comment=b"Pico2 APRS", message=1)
    assert f.header().startswith("JK1BBJ-7>") and f.header().endswith(",WIDE1-1,WIDE2-1")
    p = parse_frame(f)
    assert p.kind == "mice" and p.mice_msg == mice.MESSAGES[1]  # En Route
    assert abs(p.lat - LAT) < 1e-4 and abs(p.lon - LON) < 1e-4
    assert p.course == 360 and abs(p.speed - 18.52) < 1.0  # 真北は 360、速度は km/h
    assert p.alt is not None and abs(p.alt - 40) <= 1
    assert p.table + p.code == "/[" and "Pico2 APRS" in (p.comment or "")
    f = mice_frame("JK1BBJ-7", (), LAT, LON, speed_kn=0.0, course=123.0)
    assert parse_frame(f).course in (None, 0)  # 止まっているときは進行方向を送らない
