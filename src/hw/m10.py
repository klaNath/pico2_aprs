"""GNSS 受信機 u-blox MAX-M10M(UART0)。maxm10 で通信し、micropyGPS で NMEA を解析する。

begin() でボーレートを確かめ(必要なら 115200 にして BBR に保存。ハードリセットはしない)、
あとは poll(status) を Core0 のループから呼ぶ(受信済みの分を読んで解析するだけで、待たない)。
有効な RMC を受けたら RTC を UTC に合わせる(最初の 1 回と、その後 1 時間ごと)。
115200 bps・1 Hz で毎秒 1.2 KB 前後。受信バッファ 2 KB なら 1 秒程度読まなくても溢れない。
"""

import time

from machine import RTC

from core.gnssfix import RtcSync, update_status
from maxm10 import MaxM10
from micropyGPS import MicropyGPS


def _set_rtc(utc_s):
    t = time.gmtime(utc_s)  # (年, 月, 日, 時, 分, 秒, 曜日, 年内通日)
    RTC().datetime((t[0], t[1], t[2], t[6], t[3], t[4], t[5], 0))


class M10:
    def __init__(self, uart_id, tx, rx, resetn=None, pps=None, baud=115200, log=None):
        self.baud = baud
        self.gps = MicropyGPS(location_formatting="dd")
        self.rtc = RtcSync(_set_rtc)
        self.dev = MaxM10(uart_id, tx, rx, resetn=resetn, pps=pps, on_nmea=self._on_nmea, log=log)
        self.sentences = 0  # 解析できた文の数
        self._rmc = False

    def begin(self):
        """モジュールを見つけて UART を開く。MON-VER の文字列のリストを返す"""
        return self.dev.begin(self.baud)

    def _on_nmea(self, line):
        update = self.gps.update
        for b in line:
            r = update(chr(b))
            if r:
                self.sentences += 1
                if r[2:] == "RMC":
                    self._rmc = True
                    self.rtc.on_rmc(self.gps)  # 受けた直後に合わせる(遅れを小さく)

    def poll(self, status=None):
        """受信済みの分を解析し、status があれば更新する。RMC を受けたら True"""
        while self.dev.update():
            pass
        got, self._rmc = self._rmc, False
        if status is not None:
            update_status(status, self.gps)
            if self.rtc.synced:
                status.utc_s = time.time()
        return got
