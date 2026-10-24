"""GNSS 受信機(UART の NMEA)。core.nmea で解析し、時刻が取れたら RTC を合わせる。

poll() を Core0 のループから呼ぶ(UART の受信バッファにたまった分を読んで解析するだけで、
待たない)。115200 bps で 1 Hz なら毎秒 1 KB 前後、DR モードの 10 Hz なら 10 倍になるので、
受信バッファは 4 KB にして 100 ms 程度の間隔で読めば溢れない。
"""

import time

from machine import RTC, UART, Pin

from core.nmea import Fix, LineReader

_RTC_SYNC_MS = 10 * 60 * 1000  # RTC を合わせ直す間隔


class Gnss:
    def __init__(self, uart_id=0, tx=0, rx=1, baud=115200, rxbuf=4096):
        self.uart = UART(uart_id, baudrate=baud, tx=Pin(tx), rx=Pin(rx), rxbuf=rxbuf)
        self.reader = LineReader(maxlen=128)
        self.fix = Fix()
        self.lines = 0  # 受け取った行の総数(チェックサム不良も含む)
        self.bytes = 0
        self._rtc_ms = None
        self._buf = bytearray(512)

    def poll(self):
        """受信済みのバイトを読んで解析する。解析できた文の数を返す。"""
        n_ok = 0
        while True:
            n = self.uart.readinto(self._buf)
            if not n:
                break
            self.bytes += n
            for line in self.reader.feed(memoryview(self._buf)[:n]):
                self.lines += 1
                if self.fix.feed(line):
                    n_ok += 1
            if n < len(self._buf):
                break
        if n_ok:
            self._sync_rtc()
        return n_ok

    def _sync_rtc(self):
        f = self.fix
        if not f.valid or f.utc_s is None:
            return
        now = time.ticks_ms()
        if self._rtc_ms is not None and time.ticks_diff(now, self._rtc_ms) < _RTC_SYNC_MS:
            return
        t = time.gmtime(f.utc_s)  # (年, 月, 日, 時, 分, 秒, 曜日, 年内通日)
        RTC().datetime((t[0], t[1], t[2], t[6], t[3], t[4], t[5], 0))
        self._rtc_ms = now

    def send(self, body):
        """設定コマンド(例 'PAIR513')にチェックサムを付けて送る。"""
        c = 0
        for b in body.encode():
            c ^= b
        self.uart.write(b"$%s*%02X\r\n" % (body.encode(), c))
