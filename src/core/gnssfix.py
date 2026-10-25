"""micropyGPS の解析結果を自局の状態(ui.views.Status)に写す。RTC を合わせる時機も決める。

ハードウェアに依存しないので PC でもテストできる(UART と RTC は hw.m10 が扱う)。
使用衛星数は GSA の集計(全系の合計)を使う。GGA の衛星数は NMEA の仕様で 12 が上限のため。
衛星ごとの強度と、QZSS・SBAS・SLAS を使えているかは UBX NAV-SAT(u-blox)から読む。
"""

import time

from core import geo

RTC_SYNC_MS = 60 * 60 * 1000  # 最初に合わせたあと、合わせ直す間隔

# UBX の gnssId
GNSS_GPS = 0
GNSS_SBAS = 1
GNSS_GALILEO = 2
GNSS_BEIDOU = 3
GNSS_QZSS = 5
GNSS_GLONASS = 6


def nav_sat(p):
    """UBX NAV-SAT のペイロード -> (signals, aug)

    signals: 捕捉中(C/N0 > 0)か、地平線より上で位置が分かっている衛星の
        [(gnssId, C/N0 dBHz, 測位に使用中か, 仰角 度 / None, 方位角 度), ...](gnssId 順、強い順)
    aug: 使えている補強の名前のタプル。'QZSS'(QZSS の衛星を測位に使用)、'SBAS'、'SLAS'(補正を使用)
    """
    signals = []
    qzss = sbas = slas = False
    n = p[5] if len(p) >= 8 else 0
    for i in range(8, min(len(p), 8 + n * 12) - 11, 12):
        gnss, cno = p[i], p[i + 2]
        elev = p[i + 3] - 256 if p[i + 3] > 127 else p[i + 3]
        if not -90 <= elev <= 90:
            elev = None  # 範囲外は「不明」(方位角も不明)
        flags = p[i + 8] | (p[i + 9] << 8) | (p[i + 10] << 16)
        used = bool(flags & 0x08)
        if cno or (elev is not None and elev >= 0):
            signals.append((gnss, cno, used, elev, p[i + 4] | (p[i + 5] << 8)))
        qzss |= used and gnss == GNSS_QZSS
        sbas |= bool(flags & 0x10000)
        slas |= bool(flags & 0x40000)
    signals.sort(key=lambda t: (t[0], -t[1]))  # 系の順、その中は強い順(sort は安定でないので明示)
    aug = tuple(name for name, on in (("QZSS", qzss), ("SBAS", sbas), ("SLAS", slas)) if on)
    return signals, aug


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
    used = gps.satellites_used_by_system
    status.sats = len(gps.satellites_used) if used else gps.satellites_in_use
    status.sats_view = gps.satellites_in_view
    hdop = gps.hdop
    status.hdop = hdop if hdop == hdop else None  # 不明は nan
    if status.fix:
        status.lat, status.lon = gps.latitude, gps.longitude
        status.grid = geo.gridlocator(status.lat, status.lon)
    else:
        status.lat = status.lon = status.grid = None


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
