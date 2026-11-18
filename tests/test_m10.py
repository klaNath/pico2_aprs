"""hw.m10.M10 の受信の処理(UART は偽物)のテスト。PC と実機の両方で動く。

UART やピンには触れない(begin() を呼ばない)。RMC の位置は架空(チェックサムはここで計算する)。
"""

import _fake_hw

_fake_hw.install()  # PC では machine を偽物にする(実機では何もしない)

from core.gnssfix import RtcSync  # noqa: E402
from hw.m10 import M10, POLL_BYTES  # noqa: E402
from ui.views import Status  # noqa: E402


def with_cs(body):
    c = 0
    for ch in body.encode():
        c ^= ch
    return "$%s*%02X\r\n" % (body, c)


RMC = with_cs("GNRMC,234833.00,A,3530.60000,N,13930.60000,E,0.119,,241026,,,A,V")
GGA = with_cs("GNGGA,234833.00,3530.60000,N,13930.60000,E,1,12,0.68,40.0,M,39.0,M,,")
GSA = with_cs("GNGSA,A,3,20,18,05,21,24,15,13,14,12,23,,,1.30,0.68,1.10,1")


class FakeUart:
    def __init__(self, data):
        self.data = data
        self.reads = []  # read() で渡した長さ

    def any(self):
        return len(self.data)

    def read(self, n):
        out, self.data = self.data[:n], self.data[n:]
        self.reads.append(len(out))
        return out


def make(data):
    """M10 と、それにつないだ偽の UART"""
    m = M10(0, 0, 1)
    m.rtc = RtcSync(lambda s: None)  # 実機の RTC を変えない
    uart = FakeUart(data)
    m.dev.uart = uart  # pyright: ignore[reportAttributeAccessIssue]  (begin() の代わり)
    return m, uart


def test_poll_reads_at_most_poll_bytes():
    data = ((GSA * 8) + GGA + RMC).encode()
    assert len(data) > 2 * POLL_BYTES
    m, uart = make(data)
    st = Status()
    got = False
    n = 0
    while uart.data:
        got = m.poll(st) or got
        n += 1
        assert n < 10
    assert max(uart.reads) <= POLL_BYTES
    assert n == (len(data) + POLL_BYTES - 1) // POLL_BYTES
    assert got  # RMC を受けた
    assert m.sentences == 10
    assert st.fix == 3
    assert st.lat is not None and st.lon is not None
    assert abs(st.lat - 35.51) < 1e-4 and abs(st.lon - 139.51) < 1e-4  # 実機は float32
    assert m.poll(st) is False  # 何も来ていない


def test_sentence_split_across_polls():
    data = (GGA + RMC).encode()
    m, uart = make(data[:30])
    st = Status()
    assert m.poll(st) is False and m.sentences == 0
    uart.data = data[30:]
    assert m.poll(st) is True
    assert m.sentences == 2
    assert m.gps.satellites_in_use == 12


def test_bad_checksum_is_ignored():
    bad = RMC[:-4] + "00\r\n"
    m, _ = make((bad + GGA).encode())
    m.poll(Status())
    assert m.sentences == 1  # GGA だけ
