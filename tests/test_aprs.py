"""aprs.packet / aprs.position のテスト。PC と実機の両方で動く。

期待値は独立実装の aprslib 0.7.2 の解析結果と一致することを確認済み
(位置あいまいさは範囲の中心、速度 km/h、高度 m)。
"""

from aprs.packet import parse, parse_frame
from tnc.ax25 import Frame


def P(tnc2):
    return parse_frame(Frame.parse(tnc2))


def near(a, b, tol=1e-4):
    return a is not None and abs(a - b) <= tol


def test_uncompressed_no_timestamp():
    p = P("N0CALL>APRS,WIDE1-1:!4903.50N/07201.75W-Test 001234")
    assert p.kind == "position" and not p.messaging
    assert near(p.lat, 49.058333) and near(p.lon, -72.029167)
    assert (p.table, p.code) == ("/", "-")
    assert p.comment == "Test 001234"


def test_course_speed_altitude():
    p = P("N0CALL>APRS:=4903.50N/07201.75W>088/036/A=001234 moving")
    assert p.messaging
    assert p.course == 88
    assert near(p.speed, 66.672, 0.01)
    assert near(p.alt, 376.1232, 0.01)
    assert p.comment == " moving"


def test_timestamp_and_south_east():
    p = P("N0CALL>APRS:/092345z4903.50S\\07201.75E#digi")
    assert p.timestamp == "092345z"
    assert near(p.lat, -49.058333) and near(p.lon, 72.029167)
    assert (p.table, p.code) == ("\\", "#")


def test_japan_position():
    p = P("JA1XYZ-9>APRS,WIDE2-1:!3540.12N/13945.67E>")
    assert p.src == "JA1XYZ-9" and p.path == ["WIDE2-1"]
    assert near(p.lat, 35.668667) and near(p.lon, 139.761167)


def test_ambiguity_uses_center():
    p = P("JA1XYZ-9>APRS:!3540.  N/13945.  E>ambig2")
    assert p.ambiguity == 2
    assert near(p.lat, 35.675) and near(p.lon, 139.758333)


def test_compressed_course_speed():
    p = P("N0CALL>APRS:=/5L!!<*e7>7P[")
    assert near(p.lat, 49.5) and near(p.lon, -72.750004)
    assert p.course == 88 and near(p.speed, 67.1017, 0.01)


def test_compressed_altitude():
    p = P("N0CALL>APRS:!/5L!!<*e7OS]S")
    assert near(p.alt, 3049.378, 0.5)
    assert p.course is None


def test_object_live_and_killed():
    p = P("N0CALL>APRS:;LEADER   *092345z4903.50N/07201.75W>088/036")
    assert p.kind == "object" and p.name == "LEADER" and p.alive
    assert p.course == 88
    q = P("N0CALL>APRS:;OBJ      _092345z4903.50N/07201.75W>")
    assert q.kind == "object" and q.name == "OBJ" and not q.alive


def test_item():
    p = P("N0CALL>APRS:)AID #2!4903.50N/07201.75WA")
    assert p.kind == "item" and p.name == "AID #2" and p.alive
    assert near(p.lat, 49.058333)


def test_message_with_number():
    p = P("N0CALL>APRS::JA1XYZ-9 :Hello there{123")
    assert p.kind == "message"
    assert (p.addressee, p.text, p.msgno, p.response) == ("JA1XYZ-9", "Hello there", "123", None)


def test_message_ack_rej():
    p = P("N0CALL>APRS::JA1XYZ   :ack123")
    assert (p.addressee, p.response, p.msgno) == ("JA1XYZ", "ack", "123")
    q = P("N0CALL>APRS::JA1XYZ   :rej7")
    assert (q.response, q.msgno) == ("rej", "7")


def test_status():
    p = P("N0CALL>APRS:>092345zNet control")
    assert (p.kind, p.timestamp, p.text) == ("status", "092345z", "Net control")
    assert P("N0CALL>APRS:>Just status").text == "Just status"


def test_third_party():
    p = P("JA1IG>APRS,WIDE1-1:}JA1XYZ-9>APRS,TCPIP,JA1IG*:!3540.12N/13945.67E>")
    assert p.kind == "position" and p.src == "JA1XYZ-9"
    assert p.third_party == "JA1IG>APRS,WIDE1-1"


def test_invalid_does_not_raise():
    for info in (b"!", b"!4903.50X/07201.75W-", b"!4903.50N]07201.75W-", b";short", b":x", b"`ab"):
        p = parse("N0CALL", "APRS", [], info)
        assert p.lat is None, info


def test_non_utf8_comment():
    p = parse("N0CALL", "APRS", [], b"!4903.50N/07201.75W-\x82\xa0ok")
    assert p.comment == "??ok"
