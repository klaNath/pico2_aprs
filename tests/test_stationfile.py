"""core.stationfile(局の表の保存と復元)と Stations.restore のテスト。PC と実機の両方で動く。

ファイル操作は偽物(メモリ上)に差し替えるので、フラッシュにも SD にも書かない。
コールサインはすべて架空の例(Q で始まる、割り当ての無いプレフィックス)。
"""

from aprs.packet import parse_frame
from core.stationfile import StationFile, escape, parse_line, tnc2_of, unescape
from core.stations import STALE_S, Stations
from tnc.ax25 import Frame

UTC = 1794700000  # 2026-11-14 頃


def P(tnc2):
    return parse_frame(Frame.parse(tnc2))


class FakeFile:
    def __init__(self, fs, path, data=b""):
        self.fs = fs
        self.path = path
        self.data = data
        self.pos = 0
        self.closed = False

    def write(self, b):
        if self.fs.fail:
            raise OSError(5)
        self.data += b

    def readline(self):
        i = self.data.find(b"\n", self.pos)
        j = len(self.data) if i < 0 else i + 1
        line = self.data[self.pos : j]
        self.pos = j
        return line

    def close(self):
        self.closed = True
        self.fs.files[self.path] = self.data


class FakeFs:
    def __init__(self):
        self.files = {}
        self.fail = False
        self.opened = []

    def open(self, path, mode):
        self.opened.append((path, mode))
        if mode == "rb":
            if path not in self.files:
                raise OSError(2)
            return FakeFile(self, path, self.files[path])
        assert mode == "wb"
        return FakeFile(self, path)

    def remove(self, path):
        if path not in self.files:
            raise OSError(2)
        del self.files[path]

    def rename(self, a, b):
        self.files[b] = self.files.pop(a)


def make(fs, **kw):
    return StationFile("/sd/stations.txt", opener=fs.open, remove=fs.remove, rename=fs.rename,
                       **kw)


def test_escape_roundtrip():
    raw = b"Q1AAA>APRS:>a\\b\r\n\x00\x7f\xe3\x81\x82 end"
    e = escape(raw)
    assert b"\n" not in e and b"\r" not in e and b"\x00" not in e
    assert unescape(e) == raw
    assert escape(b"plain") == b"plain"
    try:
        unescape(b"bad\\q")
        raise AssertionError("ValueError expected")
    except ValueError:
        pass


def test_tnc2_of_rebuilds_packet():
    for text in (
        "Q1AAA-9>APRS,Q1DG-1*,WIDE2-1:!3540.12N/13945.67E>hello",
        "Q1BBB>T2SP0W,WIDE1-1:`(_fn\"Oj/]test",  # Mic-E(宛先に緯度)
        "Q1CCC>APRS:;EVENT    *092345z3540.12N/13945.67E>x",
        "Q1GW>APRS,TCPIP*:}Q1DDD-7>APRS,TCPIP,Q1GW*:>inner status",  # サードパーティ
    ):
        p = P(text)
        q = parse_frame(Frame.parse(tnc2_of(p)))
        assert (q.src, q.kind, q.lat, q.lon, q.name, q.comment, q.text, q.third_party) == (
            p.src, p.kind, p.lat, p.lon, p.name, p.comment, p.text, p.third_party), text


def _table():
    s = Stations(own_call="Q1ME-7")
    t0 = 1000
    s.update(P("Q1AAA>APRS:!3540.12N/13945.67E>a"), t0, -3.0, False)
    s.update(P("Q1BBB>APRS:>status only"), t0 + 5_000)
    # 位置は前のを残す。音量は最後のパケットのもの
    s.update(P("Q1AAA>APRS:>later status, no pos"), t0 + 10_000, -12.3, True)
    s.update(P("Q1ME-7>APZPI2,Q1DG-1*:!3541.00N/13946.00E>echo"), t0 + 20_000)
    return s, t0 + 30_000


def test_save_and_restore():
    fs = FakeFs()
    s, now = _table()
    sf = make(fs)
    sf.load(s, UTC, now)  # ファイルが無い: 何も加えず、読み出し済みになる
    assert sf.loaded and sf.restored == 0 and len(s) == 3
    s.update(P("Q1CCC>APRS:>new"), now)
    sf.save(s, UTC, now)
    assert "/sd/stations.tmp" not in fs.files
    data = fs.files["/sd/stations.txt"]
    assert data.startswith(b"# pico2_aprs stations v1 saved ")
    lines = data.split(b"\n")[1:-1]
    assert len(lines) == 4  # 新しい順: Q1CCC, Q1ME-7, Q1AAA, Q1BBB
    # Q1AAA: 受信 2 回、最後の受信は 20 秒前、位置は 30 秒前
    assert lines[2].startswith(b"%d 2 %d " % (UTC - 20, UTC - 30))
    heard, count, pos, lat, lon, lvl, clip, p = parse_line(lines[2])
    assert lat is not None and lon is not None and pos == UTC - 30
    assert p.src == "Q1AAA" and p.kind == "status" and abs(lat - 35.668667) < 1e-4
    # 次の起動: 90 秒後(電源が切れていた間を含む)に読み出す
    s2 = Stations(own_call="Q1ME-7")
    now2 = 777
    s2.update(P("Q1BBB>APRS:>heard again after boot"), now2)  # 起動後に受けた局
    sf2 = make(fs)
    sf2.poll(s2, None, now2)  # 時刻が分からない間は読まない
    assert not sf2.loaded and len(s2) == 1
    sf2.poll(s2, UTC + 90, now2)
    assert sf2.loaded and sf2.restored == 4
    assert [st.call for st in s2.order] == ["Q1BBB", "Q1CCC", "Q1ME-7", "Q1AAA"]
    b = s2.by_call["Q1BBB"]
    assert b.count == 2 and b.age_s(now2) == 0 and b.packet.text == "heard again after boot"
    a = s2.by_call["Q1AAA"]
    assert a.count == 2 and a.age_s(now2) == 110 and a.pos_age_s(now2) == 120
    assert a.lat is not None and a.packet.kind == "status"
    assert abs(a.level_db - -12.3) < 0.01 and a.clip
    assert a.age_s(now2 + 5_000) == 115  # 復元後は ticks で進む
    assert s2.by_call["Q1ME-7"].own and s2.by_call["Q1ME-7"] not in s2.for_map()
    assert s2.by_call["Q1CCC"].lat is None and s2.by_call["Q1CCC"].pos_age_s(now2) is None
    # 受け直すと経過時間は 0 から
    s2.update(P("Q1AAA>APRS:!3540.00N/13945.00E>back"), now2 + 1000)
    assert a.age_s(now2 + 1000) == 0 and a.pos_age_s(now2 + 1000) == 0 and a.count == 3


def test_old_stations_restored_as_stale():
    fs = FakeFs()
    s, now = _table()
    make(fs).save(s, UTC, now)
    s2 = Stations()
    week = 7 * 86400  # ticks_diff の範囲(約 6 日)を超えても経過時間は正しい
    make(fs).load(s2, UTC + week, 0)
    a = s2.by_call["Q1AAA"]
    assert a.age_s(0) == week + 20 and a.stale(0)
    s3 = Stations()
    make(fs).load(s3, UTC + 60, 0)  # 1 分後なら古くない
    assert not s3.by_call["Q1AAA"].stale(0) and s3.by_call["Q1AAA"].age_s(0) < STALE_S


def test_restore_respects_max_and_bad_lines():
    fs = FakeFs()
    fs.files["/sd/stations.txt"] = (
        b"# header\n"
        b"%d 1 - - - - 0 Q1AAA>APRS:>one\n"
        b"garbage line\n"
        b"%d 1 - - - - 0 bad>>>:x\n"
        b"%d 3 - - - -40.0 0 Q1BBB>APRS:>two\\x5c\n"
        b"%d 1 - - - - 0 Q1CCC>APRS:>three\n" % (UTC - 10, UTC - 20, UTC - 30, UTC - 40)
    )
    s = Stations(max_stations=2)
    sf = make(fs)
    assert sf.load(s, UTC, 0) == 2
    assert [st.call for st in s.order] == ["Q1AAA", "Q1BBB"]
    b = s.by_call["Q1BBB"]
    assert b.count == 3 and b.packet.text == "two\\" and b.age_s(0) == 30


def test_reads_tmp_when_main_file_missing():
    fs = FakeFs()
    fs.files["/sd/stations.tmp"] = b"%d 1 - - - - 0 Q1AAA>APRS:>x\n" % (UTC - 5)
    s = Stations()
    assert make(fs).load(s, UTC, 0) == 1


def test_save_only_after_load_and_interval():
    fs = FakeFs()
    s, now = _table()
    sf = make(fs, save_ms=60_000)
    sf.poll(s, UTC, now)  # 1 回目は読み出し(ファイル無し)
    assert sf.loaded and "/sd/stations.txt" not in fs.files
    sf.poll(s, UTC, now + 1000)  # 読み出し後は変わっていないので書かない
    assert "/sd/stations.txt" not in fs.files
    s.update(P("Q1CCC>APRS:>new"), now + 2000)
    sf.poll(s, UTC + 2, now + 2000)  # 前回(読み出し)から 1 分たっていない
    assert "/sd/stations.txt" not in fs.files
    sf.poll(s, UTC + 60, now + 60_000)
    assert fs.files["/sd/stations.txt"].count(b"\n") == 5
    sf.poll(s, UTC + 200, now + 200_000)  # 変わっていなければ書かない
    assert len([o for o in fs.opened if o[1] == "wb"]) == 1
    s.update(P("Q1DDD>APRS:>x"), now + 201_000)
    sf.close(s, None, now + 202_000)  # 時刻が分からなければ書かない
    assert len([o for o in fs.opened if o[1] == "wb"]) == 1
    sf.close(s, UTC + 202, now + 202_000)
    assert fs.files["/sd/stations.txt"].count(b"\n") == 6


def test_close_before_load_does_not_overwrite():
    fs = FakeFs()
    fs.files["/sd/stations.txt"] = b"%d 1 - - - - 0 Q1AAA>APRS:>x\n" % (UTC - 5)
    s = Stations()
    s.update(P("Q1BBB>APRS:>x"), 0)
    make(fs).close(s, UTC, 0)
    assert fs.files["/sd/stations.txt"].startswith(b"%d 1 - - - - 0 Q1AAA" % (UTC - 5))


def test_write_error_keeps_old_file():
    fs = FakeFs()
    fs.files["/sd/stations.txt"] = b"old\n"
    s, now = _table()
    sf = make(fs)
    sf.loaded = True
    fs.fail = True
    try:
        sf.save(s, UTC, now)
        raise AssertionError("OSError expected")
    except OSError:
        pass
    assert fs.files["/sd/stations.txt"] == b"old\n"
