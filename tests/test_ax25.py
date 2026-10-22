"""tnc.ax25(AX.25 UI フレーム)のテスト。PC と実機の両方で動く。

期待値のバイト列は AX.25 仕様から組み立てたもので、上流 micro-aprs の
デコーダと CRC 実装でも同じ結果になることを確認済み。
"""

from tnc.ax25 import Address, DecodeError, Frame, fcs

# N0CALL>APRS,WIDE1-1:test
FRAME1 = bytes.fromhex("82a0a4a64040e09c608682989860ae92888a62406303f074657374b5c3")


def _raises(exc, fn, *args):
    try:
        fn(*args)
    except exc:
        return
    raise AssertionError("%s was not raised" % exc.__name__)


def test_fcs_check_value():
    # CRC-16/X.25 の標準チェック値
    assert fcs(b"123456789") == 0x906E


def test_encode_known_frame():
    f = Frame.parse("N0CALL>APRS,WIDE1-1:test")
    assert bytes(f.encode()) == FRAME1


def test_decode_known_frame():
    f = Frame.decode(FRAME1)
    assert str(f.src) == "N0CALL"
    assert str(f.dst) == "APRS"
    assert [str(d) for d in f.digis] == ["WIDE1-1"]
    assert f.info == b"test"
    assert f.header() == "N0CALL>APRS,WIDE1-1"


def test_decode_with_length_in_larger_buffer():
    buf = bytearray(64)
    buf[: len(FRAME1)] = FRAME1
    assert Frame.decode(buf, len(FRAME1)).info == b"test"


def test_ssid_all_values_roundtrip():
    # 上流の SSID デコード(マスク 0x17)/エンコード(SSID 10-15)の不具合が直っていること
    for ssid in range(16):
        f = Frame(Address("APRS"), Address("JA1XYZ", ssid), [], b"x")
        g = Frame.decode(f.encode())
        assert g.src.ssid == ssid, ssid


def test_repeated_digi_h_bit():
    digis = [Address("JA1DG", 1, True), Address("WIDE2", 1)]
    f = Frame(Address("APRS"), Address("JA1XYZ", 9), digis, b"!x")
    g = Frame.decode(f.encode())
    assert g.digis[0].h is True
    assert g.digis[1].h is False
    assert g.header() == "JA1XYZ-9>APRS,JA1DG-1*,WIDE2-1"


def test_header_marks_only_last_repeated():
    f = Frame.parse("JA1XYZ>APRS,JA1AA*,JA1BB*,WIDE2-1:x")
    assert f.header() == "JA1XYZ>APRS,JA1AA,JA1BB*,WIDE2-1"


def test_reject_bad_fcs():
    bad = bytearray(FRAME1)
    bad[-1] ^= 0x01
    _raises(DecodeError, Frame.decode, bad)


def test_reject_non_ui_frame():
    f = bytearray(Frame.parse("N0CALL>APRS:test").encode()[:-2])
    f[14] = 0x13  # control を UI 以外に
    c = fcs(f)
    _raises(DecodeError, Frame.decode, bytes(f) + bytes([c & 0xFF, c >> 8]))


def test_reject_short_frame():
    _raises(DecodeError, Frame.decode, FRAME1[:10])


def test_address_parse():
    a = Address.parse("ja1xyz-15")
    assert (a.call, a.ssid, a.h) == ("JA1XYZ", 15, False)
    assert str(Address.parse("WIDE2-1*")) == "WIDE2-1*"
    for bad in ("TOOLONGC", "JA1XYZ-16", "J@1", "", "JA1XYZ-1A"):
        _raises(ValueError, Address.parse, bad)


def test_tnc2_parse_bytes_info():
    f = Frame.parse(b"JA1XYZ>APRS:\x1c\x7fbin")
    assert f.info == b"\x1c\x7fbin"
