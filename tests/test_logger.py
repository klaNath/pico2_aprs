"""core.logger(受信ログと NMEA ログ)のテスト。PC と実機の両方で動く。

ファイル操作は偽物(メモリ上)に差し替えるので、フラッシュにも SD にも書かない。
"""

import time

from core.logger import Logger, iso_utc, next_seq

UTC = 1792885713  # 2026-10-24T23:48:33Z
RMC = b"$GNRMC,234833.00,A,3530.60000,N,13930.60000,E,0.119,,241026,,,A,V*00\r\n"
GSV = b"$GPGSV,1,1,01,05,41,131,08,1*00\r\n"


class FakeFile:
    def __init__(self, fs, path):
        self.fs = fs
        self.path = path
        self.data = b""
        self.flushed = b""
        self.closed = False

    def write(self, b):
        if self.fs.fail:
            raise OSError(5)
        self.data += b

    def flush(self):
        self.flushed = self.data

    def close(self):
        self.closed = True


class FakeFs:
    def __init__(self, names=None):
        self.names = names  # None = ディレクトリが無い
        self.files = {}
        self.fail = False

    def open(self, path, mode):
        assert mode == "wb"
        f = self.files[path] = FakeFile(self, path)
        return f

    def listdir(self, path):
        if self.names is None:
            raise OSError(2)
        return self.names

    def mkdir(self, path):
        self.names = []


def make(fs, **kw):
    return Logger("JK1BBJ-7", opener=fs.open, listdir=fs.listdir, mkdir=fs.mkdir, **kw)


def test_iso_and_seq():
    assert iso_utc(UTC) == "2026-10-24T23:48:33Z"
    assert next_seq([]) == 1
    assert next_seq(["0001_aprs.txt", "0001_nmea.txt", "0007_aprs.txt", "notes.txt"]) == 8
    assert next_seq(["9999_aprs.txt"]) == 1


def test_files_and_lines():
    fs = FakeFs(["0002_aprs.txt", "0002_nmea.txt"])
    lg = make(fs)
    # カードが無い間もためる
    lg.packet("JA1AAA>APRS:>before start", now_ms=time.ticks_add(lg._t0, 1234))
    assert lg.start("/sd/log", UTC) == 3
    aprs, nmea = fs.files["/sd/log/0003_aprs.txt"], fs.files["/sd/log/0003_nmea.txt"]
    assert aprs.data.startswith(b"# JK1BBJ-7 aprs log 0003 started 2026-10-24T23:48:33Z\n")
    assert b"T+1.234 JA1AAA>APRS:>before start\n" in aprs.flushed  # 開いたときに書く
    lg.packet(b"JA1BBB>APRS:!3539.53N/13944.70E>\r\n", utc_s=UTC + 1)
    lg.packet("JK1BBJ-7>APZPI2:`test", tx=True, utc_s=UTC + 2)
    lg.packet("JK1BBJ-7>APZPI2:`dry", tx=True, utc_s=UTC + 3, dry=True)
    lg.nmea(RMC)
    lg.nmea(GSV)  # RMC と GGA 以外は残さない
    assert not lg.poll(now_ms=time.ticks_add(lg._last_flush, 9_999))  # 10 秒たつまでは書かない
    assert lg.poll(now_ms=time.ticks_add(lg._last_flush, 10_000))
    assert aprs.flushed.endswith(b"2026-10-24T23:48:34Z JA1BBB>APRS:!3539.53N/13944.70E>\n"
                                 b"2026-10-24T23:48:35Z TX JK1BBJ-7>APZPI2:`test\n"
                                 b"2026-10-24T23:48:36Z TX(dry) JK1BBJ-7>APZPI2:`dry\n")
    assert nmea.flushed.endswith(RMC) and GSV not in nmea.flushed
    assert lg.lines == 5
    lg.close()
    assert aprs.closed and nmea.closed and lg.seq is None


def test_makes_directory():
    fs = FakeFs(None)
    assert make(fs).start("/sd/log") == 1
    assert b"started time unknown" in fs.files["/sd/log/0001_aprs.txt"].data


def test_buffer_limit_while_no_card():
    lg = make(FakeFs([]), max_bytes=200)
    for k in range(20):
        lg.packet("JA1AAA>APRS:>%02d" % k, utc_s=UTC)  # 1 行 40 バイト
    assert lg.dropped == 15 and lg._bytes <= 200
    assert lg._buf[0][-1].endswith(b">19\n")  # 古い行から捨てる


def test_write_error_closes_and_keeps_buffer():
    fs = FakeFs([])
    lg = make(fs)
    lg.start("/sd/log", UTC)
    lg.packet("JA1AAA>APRS:>x", utc_s=UTC)
    fs.fail = True
    try:
        lg.flush()
        raise AssertionError("OSError expected")
    except OSError:
        pass
    assert lg.seq is None and len(lg._buf[0]) == 1  # 閉じて、書けなかった行は残す
    fs.fail = False
    assert lg.start("/sd/log", UTC) == 1  # 偽の listdir は空のまま
    assert fs.files["/sd/log/0001_aprs.txt"].flushed.endswith(b"JA1AAA>APRS:>x\n")
