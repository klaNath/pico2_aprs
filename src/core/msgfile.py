"""APRS メッセージ(core.messages)を microSD に保存し、次の起動で読み出す。

    /sd/messages.txt
        # pico2_aprs messages v1 next 13
        <in|out> <番号|-> <相手> <状態|-> <送った回数> <UTC の秒|-> <本文>

1 行 1 通、新しい順(最大 core.messages.KEEP 通なので、変わるたびにファイルごと書き直す)。
本文は UTF-8 で、制御文字と '\\' は '\\xHH'(core.stationfile.escape)。next は次に使う番号。
読み出しはマウント後の最初の poll() で 1 回(時刻は要らない)。保存は読み出した後だけ。
壊れた行は読み飛ばす。書き込みは messages.tmp に書いてから置き換える(core.stationfile と同じ)。
"""

import os
import time

from core.messages import Msg
from core.stationfile import escape, unescape

SAVE_MS = 5000  # 変わったら、前回の保存からこれだけたってから書く
VERSION = b"v1"
_ENOENT = 2


def _opt(v):
    return b"-" if v is None else str(v).encode()


def format_line(m):
    return b"%s %s %s %s %d %s %s\n" % (
        b"out" if m.out else b"in", _opt(m.no), m.peer.encode(), _opt(m.state), m.tries,
        _opt(m.utc_s), escape(m.text.encode()))


def parse_line(line):
    """1 行 -> Msg。不正なら ValueError"""
    f = line.rstrip(b"\r\n").split(b" ", 6)
    if len(f) != 7 or f[0] not in (b"in", b"out") or not f[2]:
        raise ValueError("bad line")

    def opt(b):
        return None if b == b"-" else b.decode()

    utc = opt(f[5])
    return Msg(f[0] == b"out", opt(f[1]), f[2].decode(), unescape(f[6]).decode(),
               None if utc is None else int(utc), opt(f[3]), int(f[4].decode()))


class MsgFile:
    def __init__(self, path, save_ms=SAVE_MS, opener=open, remove=os.remove, rename=os.rename,
                 log=None):
        self.path = path
        self.tmp = path.rsplit(".", 1)[0] + ".tmp"
        self.save_ms = save_ms
        self.opener = opener
        self.remove = remove
        self.rename = rename
        self.log = log or (lambda *a: None)
        self.loaded = False
        self._saved_version = None
        self._saved_ms = None

    def poll(self, msgs, now_ms):
        """SD をマウントしている間に呼ぶ。読み出し(1 回)と、変わったときの保存。失敗は OSError"""
        if not self.loaded:
            self.load(msgs)
            return
        if msgs.version == self._saved_version:
            return
        if self._saved_ms is not None and time.ticks_diff(now_ms, self._saved_ms) < self.save_ms:
            return
        self.save(msgs, now_ms)

    def load(self, msgs):
        f = None
        for path in (self.path, self.tmp):
            try:
                f = self.opener(path, "rb")
                break
            except OSError as e:
                if e.args[0] != _ENOENT:
                    raise
        olds = []
        next_no = 1
        bad = 0
        if f is not None:
            try:
                while True:
                    line = f.readline()
                    if not line:
                        break
                    if line.startswith(b"#"):
                        k = line.find(b" next ")
                        if k >= 0:
                            try:
                                next_no = int(line[k + 6 :].strip().decode())
                            except ValueError:
                                pass
                        continue
                    if not line.strip():
                        continue
                    try:
                        olds.append(parse_line(line))
                    except Exception:  # 壊れた行
                        bad += 1
            finally:
                f.close()
        msgs.restore(olds, next_no)
        self.loaded = True
        self._saved_version = None  # stopped にしたものを書き直す
        self._saved_ms = None
        self.log("[sd] messages restored %d%s" % (
            len(olds), " (%d bad lines)" % bad if bad else ""))

    def save(self, msgs, now_ms):
        f = self.opener(self.tmp, "wb")
        try:
            f.write(b"# pico2_aprs messages %s next %d\n" % (VERSION, msgs.next_no))
            for m in msgs.items:
                f.write(format_line(m))
        finally:
            f.close()
        try:
            self.remove(self.path)
        except OSError:
            pass
        self.rename(self.tmp, self.path)
        self._saved_version = msgs.version
        self._saved_ms = now_ms

    def close(self, msgs, now_ms):
        if self.loaded and msgs.version != self._saved_version:
            self.save(msgs, now_ms)
