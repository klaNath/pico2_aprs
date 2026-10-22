"""aprs.mice(Mic-E デコード/エンコード)のテスト。PC と実機の両方で動く。

デコードの期待値とエンコード出力は、独立実装の aprslib 0.7.2 で確認済み
(エンコード → aprslib でデコードのランダム往復 3000 件も一致)。
"""

from aprs import mice
from aprs.packet import parse_frame
from tnc.ax25 import Frame


def near(a, b, tol=1e-4):
    return a is not None and abs(a - b) <= tol


def test_decode_spec_example():
    p = parse_frame(Frame.parse('N0CALL>S32U6T,WIDE1-1:`(_fn"Oj/]comment'))
    assert p.kind == "mice"
    assert near(p.lat, 33.427333) and near(p.lon, -12.129)
    assert near(p.speed, 37.04, 0.01) and p.course == 251
    assert p.mice_msg == "Returning"
    assert (p.table, p.code) == ("/", "j")
    assert p.comment == "comment"  # 先頭の機種文字 ']' は取り除く


def test_decode_altitude():
    p = parse_frame(Frame.parse('N0CALL>T2SP0W:`c_Vm6hk/`"49}Hello'))
    assert near(p.lat, 42.501167) and near(p.lon, -71.126333)
    assert p.alt == 34 and p.course == 276 and p.mice_msg == "In Service"
    assert p.comment == "Hello"


def test_decode_ambiguity():
    p = parse_frame(Frame.parse('N0CALL>S32ULZ:`(_fn"Oj/]amb2'))
    assert p.ambiguity == 2


def test_encode_tokyo():
    dst, info = mice.encode(35.681236, 139.767125, 40.0, 90, "/", ">", 1, 40, b"Pico2")
    assert dst == "SU4PX7"
    assert info == b'`CJ\x1fn4v>/"4?}Pico2'


def test_encode_south_east_no_alt():
    dst, info = mice.encode(-33.856784, 151.215297, 0.0, 0, "/", "[", 0)
    assert dst == "SSU1T1"
    assert info == b"`O(xl \x1c[/"


def test_encode_emergency_and_type_char_comment():
    dst, info = mice.encode(43.064, 141.347, 120.0, 359, "\\", "k", mice.MSG_EMERGENCY, 3776, b">x")
    assert dst == "430SX4"
    assert info == b'`E0nrUWk\\"]D}>x'


def test_roundtrip_grid():
    # エンコード → 自前のデコードで元に戻る(1/100 分、1 ノットに丸め)
    for lat in (-89.5, -35.0, 0.001, 9.99, 35.681236, 89.99):
        for lon in (-179.99, -100.0, -9.5, 0.01, 10.0, 99.99, 105.5, 139.767, 179.99):
            for spd, crs in ((0.0, 0), (18.52, 1), (370.4, 360), (1480.0, 180)):
                dst, info = mice.encode(lat, lon, spd, crs, "/", ">", 2, None, b"c")
                r = mice.decode(dst, info)
                assert r is not None, (lat, lon)
                assert near(r["lat"], lat, 1 / 6000), (lat, r["lat"])
                assert near(r["lon"], lon, 1 / 6000), (lon, r["lon"])
                assert near(r["speed"], spd, 1.852 / 2 + 1e-3), (spd, r["speed"])
                assert r["course"] == (crs or None)
                assert r["message"] == "In Service"
                assert r["comment"] == b"c"


def test_encode_rejects_out_of_range():
    for args in ((91.0, 0.0), (0.0, 181.0)):
        try:
            mice.encode(*args)
        except ValueError:
            continue
        raise AssertionError(args)
