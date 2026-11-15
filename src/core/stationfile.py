"""受信した局の表(core.stations)を microSD に保存し、次の起動で読み出して表に加える。

フラッシュ(内蔵ファイルシステム・ROMFS)は書き込み直しで消えることがあるので、SD に置く。

    /sd/stations.txt
        # pico2_aprs stations v1 saved 2026-11-15T01:02:03Z
        <受信 UTC> <受信数> <位置の UTC|-> <緯度|-> <経度|-> <音量 dBFS|-> <クリップ 0/1> <TNC2>

1 行 1 局、新しい順。TNC2 は最後に受けたパケット('SRC>DST,PATH:info')で、読み出すときに
aprs.packet で解析し直す。info の制御文字(0x00-0x1F, 0x7F)と '\\' は '\\xHH' に置き換える。
時刻は UTC の秒(time.time())。GNSS の時刻が分かるまでは、読み出しも保存もしない(経過時間が
計算できないため。前回のファイルはそのまま残る)。保存は読み出した後だけ(先に保存すると、
読み出す前のファイルを起動後の局だけで上書きしてしまう)。
壊れた行は読み飛ばす。書き込みは stations.tmp に書いてから置き換える。置き換えの途中で電源が
切れて stations.txt が無いときは stations.tmp を読む。
ファイル操作は opener / remove / rename で差し替えられる(テストでは偽物を渡す)。
"""

import os
import time

from aprs.packet import parse_frame
from core.logger import iso_utc
from tnc.ax25 import Frame

SAVE_MS = 60_000  # 表が変わったら、前回の保存からこれだけたってから書く
VERSION = b"v1"
_ENOENT = 2


def escape(b):
    """bytes -> 1 行に書ける bytes(制御文字と '\\' を '\\xHH' に)"""
    if not any(c < 0x20 or c == 0x7F or c == 0x5C for c in b):
        return bytes(b)
    out = bytearray()
    for c in b:
        if c < 0x20 or c == 0x7F or c == 0x5C:
            out.extend(b"\\x%02x" % c)
        else:
            out.append(c)
    return bytes(out)


def unescape(b):
    """escape の逆。不正な '\\' は ValueError"""
    if 0x5C not in b:
        return bytes(b)
    out = bytearray()
    i = 0
    n = len(b)
    while i < n:
        c = b[i]
        if c == 0x5C:
            if i + 4 > n or b[i + 1] != 0x78:  # 'x'
                raise ValueError("bad escape")
            out.append(int(bytes(b[i + 2 : i + 4]).decode(), 16))
            i += 4
        else:
            out.append(c)
            i += 1
    return bytes(out)


def tnc2_of(p):
    """aprs.packet.Packet -> TNC2 の bytes(サードパーティなら外側のヘッダ付き)"""
    head = p.src + ">" + p.dst + "".join("," + d for d in p.path)
    text = head.encode() + b":" + bytes(p.raw)
    if p.third_party:
        text = p.third_party.encode() + b":}" + text
    return text


def _opt(fmt, v):
    return b"-" if v is None else (fmt % v).encode()


def _num(b, conv):
    return None if b == b"-" else conv(b.decode())


def format_line(st, utc_s, now_ms):
    """Station -> 1 行(改行付き)"""
    heard = utc_s - st.age_s(now_ms)
    pa = st.pos_age_s(now_ms) if st.lat is not None else None
    return b"%d %d %s %s %s %s %d %s\n" % (
        heard, st.count, _opt("%d", None if pa is None else utc_s - pa),
        _opt("%.6f", st.lat if pa is not None else None),
        _opt("%.6f", st.lon if pa is not None else None),
        _opt("%.1f", st.level_db), 1 if st.clip else 0, escape(tnc2_of(st.packet)))


def parse_line(line):
    """1 行 -> (heard_utc, count, pos_utc, lat, lon, level_db, clip, Packet)。不正なら ValueError"""
    f = line.rstrip(b"\r\n").split(b" ", 7)
    if len(f) != 8:
        raise ValueError("bad line")
    heard = int(f[0].decode())
    count = int(f[1].decode())
    pos = _num(f[2], int)
    lat = _num(f[3], float)
    lon = _num(f[4], float)
    lvl = _num(f[5], float)
    clip = f[6] == b"1"
    p = parse_frame(Frame.parse(unescape(f[7])))
    return heard, count, pos, lat, lon, lvl, clip, p


class StationFile:
    def __init__(self, path, save_ms=SAVE_MS, opener=open, remove=os.remove, rename=os.rename,
                 log=None):
        self.path = path
        self.tmp = path.rsplit(".", 1)[0] + ".tmp"
        self.save_ms = save_ms
        self.opener = opener
        self.remove = remove
        self.rename = rename
        self.log = log or (lambda *a: None)
        self.loaded = False  # 読み出した(またはファイルが無かった)。これより前は保存しない
        self.restored = 0  # 読み出して表に加えた局の数
        self._saved_version = None
        self._saved_ms = None

    def poll(self, stations, utc_s, now_ms):
        """SD をマウントしている間に呼ぶ。読み出し(1 回)と定期の保存。失敗は OSError"""
        if utc_s is None:
            return
        if not self.loaded:
            self.load(stations, utc_s, now_ms)
            return
        if stations.version == self._saved_version:
            return
        if self._saved_ms is not None and time.ticks_diff(now_ms, self._saved_ms) < self.save_ms:
            return
        self.save(stations, utc_s, now_ms)

    def load(self, stations, utc_s, now_ms):
        """ファイルの局を表に加える。加えた数を返す。ファイルが無ければ 0"""
        f = None
        for path in (self.path, self.tmp):
            try:
                f = self.opener(path, "rb")
                break
            except OSError as e:
                if e.args[0] != _ENOENT:
                    raise
        n = bad = 0
        if f is not None:
            try:
                while True:
                    line = f.readline()
                    if not line:
                        break
                    if line.startswith(b"#") or not line.strip():
                        continue
                    try:
                        heard, count, pos, lat, lon, lvl, clip, p = parse_line(line)
                    except Exception:  # 壊れた行・解析できないパケット
                        bad += 1
                        continue
                    pos_age = None if pos is None else max(0, utc_s - pos)
                    st = stations.restore(p, max(0, utc_s - heard), now_ms, count, lat, lon,
                                          pos_age, lvl, clip)
                    if st is not None:
                        n += 1
            finally:
                f.close()
        self.loaded = True
        self.restored = n
        self._saved_version = stations.version
        self._saved_ms = now_ms
        self.log("[sd] stations restored %d%s" % (n, " (%d bad lines)" % bad if bad else ""))
        return n

    def save(self, stations, utc_s, now_ms):
        """表を書く(stations.tmp に書いてから置き換える)"""
        f = self.opener(self.tmp, "wb")
        try:
            f.write(b"# pico2_aprs stations %s saved %s\n" % (VERSION, iso_utc(utc_s).encode()))
            for st in stations.order:
                if st.packet is not None:
                    f.write(format_line(st, utc_s, now_ms))
        finally:
            f.close()
        try:
            self.remove(self.path)
        except OSError:
            pass
        self.rename(self.tmp, self.path)
        self._saved_version = stations.version
        self._saved_ms = now_ms

    def close(self, stations, utc_s, now_ms):
        """終了の前: 読み出し済みで、表が変わっていれば書く(時刻が分からなければ書かない)"""
        if self.loaded and utc_s is not None and stations.version != self._saved_version:
            self.save(stations, utc_s, now_ms)
