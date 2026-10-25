"""受信ログと NMEA ログ。起動ごと(カードを挿し直したときも)に通し番号の 1 組のファイルを作る。

    <root>/0001_aprs.txt   受信したパケット(TNC2 の文字列)。自局の送信は 'TX ' を付ける
    <root>/0001_nmea.txt   NMEA の RMC と GGA(受信したまま)

受信ログの 1 行: '2026-10-25T01:37:37Z JA1ABC>APRS,WIDE1-1:!3539.53N/13944.70E>'
GNSS の時刻が取れる前は時刻の代わりに起動からの経過秒 'T+123.456' を書く。先頭の '#' の行は見出し。
行はメモリにためて flush_ms ごとにまとめて書く(カードの書き込み回数を減らす。電源断で失うのは
最大 flush_ms 分)。ファイルが開いていない間(カードが無い)もためるが、max_bytes を超えたら
古い行から捨てる。
ファイル操作は opener / listdir / mkdir で差し替えられる(テストでは偽物を渡す)。
"""

import os
import time

FLUSH_MS = 10_000
MAX_BYTES = 16 * 1024  # ファイルが開いていない間にためる上限(2 つのログの合計)
NMEA_KEEP = (b"RMC", b"GGA")
SEQ_MAX = 9999


def iso_utc(utc_s):
    """UTC の秒 -> '2026-10-25T01:37:37Z'"""
    t = time.gmtime(utc_s)
    return "%04d-%02d-%02dT%02d:%02d:%02dZ" % (t[0], t[1], t[2], t[3], t[4], t[5])


def next_seq(names):
    """既存のファイル名('0001_aprs.txt' など)-> 次の通し番号(9999 の次は 1)"""
    last = 0
    for n in names:
        if len(n) > 5 and n[4] == "_" and n[:4].isdigit():
            last = max(last, int(n[:4]))
    return last + 1 if last < SEQ_MAX else 1


class Logger:
    def __init__(self, header="", flush_ms=FLUSH_MS, max_bytes=MAX_BYTES,
                 opener=open, listdir=os.listdir, mkdir=os.mkdir):
        self.header = header  # 見出しに書く文字列(局名など)
        self.flush_ms = flush_ms
        self.max_bytes = max_bytes
        self.opener = opener
        self.listdir = listdir
        self.mkdir = mkdir
        self.seq = None  # 開いているファイルの通し番号 / None = 開いていない
        self.lines = 0  # 書いた行の数(見出しを除く)
        self.dropped = 0  # 上限を超えて捨てた行の数
        self._files = None  # (aprs, nmea)
        self._buf = ([], [])
        self._bytes = 0
        self._t0 = time.ticks_ms()
        self._last_flush = self._t0

    # ---- 記録(バッファにためるだけ。メモリ確保は 1 行分)
    def packet(self, text, tx=False, utc_s=None, now_ms=None):
        """受信(tx=True なら自局の送信)したパケット。text は TNC2 の str / bytes"""
        if utc_s is not None:
            stamp = iso_utc(utc_s)
        else:
            ms = time.ticks_diff(time.ticks_ms() if now_ms is None else now_ms, self._t0)
            stamp = "T+%d.%03d" % (ms // 1000, ms % 1000)
        if isinstance(text, str):
            text = text.encode()
        self._add(0, b"%s %s%s\n" % (stamp.encode(), b"TX " if tx else b"", text.rstrip(b"\r\n")))

    def nmea(self, line):
        """NMEA の 1 文(bytes、CR LF 付き)。RMC と GGA だけ残す"""
        if line[3:6] in NMEA_KEEP:
            self._add(1, line if line.endswith(b"\n") else line + b"\r\n")

    def _add(self, k, data):
        self._buf[k].append(data)
        self._bytes += len(data)
        while self._bytes > self.max_bytes:
            b = self._buf[0] if len(self._buf[0]) >= len(self._buf[1]) else self._buf[1]
            self._bytes -= len(b.pop(0))
            self.dropped += 1

    # ---- ファイル
    def start(self, root, utc_s=None):
        """root にこの起動のファイルを作って開く。通し番号を返す。失敗は OSError"""
        try:
            names = self.listdir(root)
        except OSError:
            self.mkdir(root)
            names = []
        seq = next_seq(names)
        when = iso_utc(utc_s) if utc_s is not None else "time unknown"
        files = []
        try:
            for kind in ("aprs", "nmea"):
                f = self.opener("%s/%04d_%s.txt" % (root, seq, kind), "wb")
                files.append(f)
                f.write(b"# %s %s log %04d started %s\n" % (self.header.encode(), kind.encode(),
                                                            seq, when.encode()))
        except OSError:
            for f in files:
                _close(f)
            raise
        self._files = (files[0], files[1])
        self.seq = seq
        self.flush()
        return seq

    def poll(self, now_ms=None):
        """flush_ms ごとに書く。書いたら True。書き込みの失敗は OSError(ファイルは閉じる)"""
        now = time.ticks_ms() if now_ms is None else now_ms
        if self._files is None or time.ticks_diff(now, self._last_flush) < self.flush_ms:
            return False
        self._last_flush = now
        self.flush()
        return True

    def flush(self):
        if self._files is None:
            return
        try:
            for f, buf in zip(self._files, self._buf):
                if buf:
                    for data in buf:
                        f.write(data)
                    self.lines += len(buf)
                    buf.clear()
                f.flush()
        except OSError:
            self.abandon()
            raise
        self._bytes = 0

    def close(self):
        """書き残しを書いて閉じる(カードを外す前)"""
        if self._files is not None:
            self.flush()
            self.abandon()

    def abandon(self):
        """書かずに閉じる(カードが抜かれた・書き込みに失敗した)。バッファは残す"""
        if self._files is not None:
            for f in self._files:
                _close(f)
        self._files = None
        self.seq = None


def _close(f):
    try:
        f.close()
    except OSError:
        pass
