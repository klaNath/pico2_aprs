"""core.nmea のテスト。PC と実機の両方で動く。

RMC/GGA の例文は NMEA 0183 の解説で広く使われているもの(チェックサムも含めて正しい)。
u-blox M10 風の行(GN、NMEA 4.1 の追加欄付き)は、このテストの中でチェックサムを計算して作る。
"""

from core.nmea import Fix, LineReader, checksum_ok

RMC = "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A"
GGA = "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47"


def with_cs(body):
    c = 0
    for ch in body.encode():
        c ^= ch
    return "$%s*%02X" % (body, c)


def near(a, b, tol=1e-4):
    return a is not None and abs(a - b) <= tol


def test_checksum():
    assert checksum_ok(RMC) and checksum_ok(GGA)
    assert not checksum_ok(RMC.replace("A,4807", "V,4807"))
    assert not checksum_ok("$GPRMC,1*")
    assert not checksum_ok("GPRMC*00")


def test_rmc_gga():
    f = Fix()
    assert f.feed(RMC) == "RMC"
    assert f.valid
    assert near(f.lat, 48 + 7.038 / 60) and near(f.lon, 11 + 31.0 / 60)
    assert near(f.speed, 22.4 * 1.852, 0.01) and near(f.course, 84.4, 0.01)
    # 1994-03-23 12:35:19 UTC(2000 年基準の 2 桁年なので 2094 年として計算される点に注意)
    assert f.utc_s is not None and f.utc_s % 86400 == 12 * 3600 + 35 * 60 + 19
    assert f.feed(GGA) == "GGA"
    assert f.sats == 8 and near(f.hdop, 0.9) and near(f.alt, 545.4)
    assert f.fix == 3


def test_ublox_m10_style_lines():
    f = Fix()
    rmc = with_cs("GNRMC,023412.00,A,3540.87418,N,13946.02750,E,0.012,,231026,,,A,V")
    gga = with_cs("GNGGA,023412.00,3540.87418,N,13946.02750,E,1,09,1.02,40.3,M,39.1,M,,")
    gsa = with_cs("GNGSA,A,3,05,13,15,18,23,24,,,,,,,1.86,1.02,1.55,1")
    assert f.feed(rmc) == "RMC" and f.feed(gga) == "GGA" and f.feed(gsa) == "GSA"
    assert near(f.lat, 35 + 40.87418 / 60) and near(f.lon, 139 + 46.0275 / 60)
    assert f.course is None and f.fix == 3 and f.sats == 9
    # 2026-10-23 02:34:12 UTC
    assert f.utc_s == 1792722852


def test_no_fix():
    f = Fix()
    f.feed(with_cs("GNRMC,,V,,,,,,,,,,N,V"))
    f.feed(with_cs("GNGGA,,,,,,0,00,99.99,,,,,,"))
    assert not f.valid and f.fix == 0 and f.lat is None and f.utc_s is None


def test_line_reader():
    r = LineReader(maxlen=16)
    assert r.feed(b"$AB*00\r\n$CD") == [b"$AB*00"]
    assert r.feed(b"*11\n") == [b"$CD*11"]
    assert r.feed(b"x" * 40 + b"\nok\n") == [b"ok"]  # 長すぎる行は捨てる
