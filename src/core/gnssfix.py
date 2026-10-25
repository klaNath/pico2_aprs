"""micropyGPS の解析結果を自局の状態(ui.views.Status)に写す。RTC を合わせる時機も決める。

ハードウェアに依存しないので PC でもテストできる(UART と RTC は hw.m10 が扱う)。
使用衛星数は GSA の集計(全系の合計)を使う。GGA の衛星数は NMEA の仕様で 12 が上限のため。
"""

import time

from core import geo

RTC_SYNC_MS = 60 * 60 * 1000  # 最初に合わせたあと、合わせ直す間隔


def utc_s(gps):
    """micropyGPS の日付と時刻 -> UTC の秒 / 日付がまだ無ければ None"""
    d, mo, y = gps.date
    if not mo:
        return None
    h, mi, s = gps.timestamp
    return geo.utc_seconds(2000 + y, mo, d, h, mi, int(s))


def update_status(status, gps):
    """測位・衛星・位置を status に写す(時刻は RtcSync 側で扱う)"""
    fix = gps.fix_type if gps.valid else 0
    status.fix = fix if fix >= 2 else 0
    status.sats = len(gps.satellites_used) if gps.satellites_used_by_system else gps.satellites_in_use
    status.sats_view = gps.satellites_in_view
    hdop = gps.hdop
    status.hdop = hdop if hdop == hdop else None  # 不明は nan
    if status.fix:
        status.lat, status.lon = gps.latitude, gps.longitude
    else:
        status.lat = status.lon = None


class RtcSync:
    """有効な RMC の時刻で RTC を合わせる。最初の 1 回のあとは interval_ms ごと。

    set_rtc(utc_s) は RTC を合わせる関数(テストでは偽物を渡す)。
    """

    def __init__(self, set_rtc, interval_ms=RTC_SYNC_MS):
        self.set_rtc = set_rtc
        self.interval_ms = interval_ms
        self.last_ms = None  # 最後に合わせた ticks_ms / None = まだ

    @property
    def synced(self):
        return self.last_ms is not None

    def on_rmc(self, gps, now_ms=None):
        """RMC を解析した直後に呼ぶ。合わせたら True"""
        if not gps.valid:
            return False
        if now_ms is None:
            now_ms = time.ticks_ms()
        if self.last_ms is not None and time.ticks_diff(now_ms, self.last_ms) < self.interval_ms:
            return False
        t = utc_s(gps)
        if t is None:
            return False
        self.set_rtc(t)
        self.last_ms = now_ms
        return True
