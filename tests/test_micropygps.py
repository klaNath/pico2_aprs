"""micropyGPS(生成物 src/micropyGPS.py)の複数 GNSS 対応のテスト。PC と実機の両方で動く。

NMEA は実機の MAX-M10M(SPG 5.10、GPS+GLONASS+Galileo+QZSS、既定の設定)から取った 1 エポック分。
GSV は系(talker)と信号 ID ごと、GSA は系 ID 付きで系ごとに出る。
"""

from micropyGPS import MicropyGPS

GSV = [
    "$GPGSV,3,1,11,05,41,131,08,12,08,167,15,13,10,171,20,14,13,049,09,1*67",
    "$GPGSV,3,2,11,15,66,010,31,18,43,259,33,20,43,058,23,21,09,130,13,1*61",
    "$GPGSV,3,3,11,22,21,067,12,23,34,317,08,24,70,224,07,1*5C",
    "$GPGSV,1,1,03,10,03,314,,42,21,244,,50,21,244,,0*51",
    "$GLGSV,2,1,05,65,49,041,31,66,40,323,21,81,30,284,37,82,13,329,17,1*74",
    "$GLGSV,2,2,05,88,20,225,32,1*4B",
    "$GLGSV,1,1,01,77,02,183,,0*40",
    "$GAGSV,3,1,09,02,16,138,10,03,49,045,16,05,04,069,09,08,59,299,22,7*7F",
    "$GAGSV,3,2,09,13,48,290,11,15,47,202,32,25,20,083,11,32,68,001,21,7*7B",
    "$GAGSV,3,3,09,34,07,167,19,7*42",
    "$GAGSV,1,1,03,07,15,263,,16,05,040,,26,08,326,,0*4E",
    "$GQGSV,1,1,03,02,08,172,,04,85,297,,08,24,244,,0*50",
]
GSA = [
    "$GNGSA,A,3,20,18,05,21,24,15,13,14,12,23,,,1.30,0.68,1.10,1*07",
    "$GNGSA,A,3,66,88,81,,,,,,,,,,1.30,0.68,1.10,2*07",
    "$GNGSA,A,3,08,15,13,03,25,32,02,,,,,,1.30,0.68,1.10,3*06",
    "$GNGSA,A,3,,,,,,,,,,,,,1.30,0.68,1.10,5*09",
]


def feed(gps, lines):
    got = []
    for line in lines:
        for ch in line + "\r\n":
            r = gps.update(ch)
            if r:
                got.append(r)
    return got


def test_gsv_all_talkers():
    gps = MicropyGPS()
    assert feed(gps, GSV) == [line[1:6] for line in GSV]
    assert gps.satellites_in_view == 11 + 3 + 5 + 1 + 9 + 3 + 3
    by = gps.satellite_data_by_system
    assert sorted(by) == ["GA", "GL", "GP", "GQ"]
    assert (len(by["GP"]), len(by["GL"]), len(by["GA"]), len(by["GQ"])) == (14, 6, 12, 3)
    assert by["GA"][34] == (7, 167, 19)
    assert by["GQ"][4] == (85, 297, None)
    # satellite_data は従来どおり最後のグループだけ
    assert sorted(gps.satellite_data) == [2, 4, 8]


def test_gsv_count_while_receiving():
    gps = MicropyGPS()
    feed(gps, GSV)
    feed(gps, GSV[:1])  # 次のエポックの最初の 1 文: GP の組は受信中なので宣言値で数える
    assert gps.satellites_in_view == 35
    feed(gps, GSV[1:3])
    assert gps.satellites_in_view == 35


def test_gsv_drops_stale_group():
    gps = MicropyGPS()
    feed(gps, GSV)
    feed(gps, GSV[:-1])  # QZSS が見えなくなった
    assert "GQ" in gps.satellite_data_by_system  # 1 周期が終わるまでは残る
    feed(gps, GSV[:1])
    assert "GQ" not in gps.satellite_data_by_system
    assert gps.satellites_in_view == 35 - 3


def test_gsa_per_system():
    gps = MicropyGPS()
    assert feed(gps, GSA) == ["GNGSA"] * 4
    used = gps.satellites_used_by_system
    assert used["GP"] == [20, 18, 5, 21, 24, 15, 13, 14, 12, 23]
    assert used["GL"] == [66, 88, 81]
    assert used["GA"] == [8, 15, 13, 3, 25, 32, 2]
    assert used["GQ"] == []
    assert len(gps.satellites_used) == 20
    assert gps.fix_type == 3
    assert (gps.pdop, gps.hdop, gps.vdop) == (1.30, 0.68, 1.10)


def test_class_constants():
    # クラス内の const() のタプルが MicroPython で属性として残らず、どちらも AttributeError だった
    gps = MicropyGPS()
    gps.course = 100.0
    assert gps.compass_direction() == "E"
    gps.date = (24, 10, 26)
    assert gps.date_string("long").startswith("October")
