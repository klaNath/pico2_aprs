"""自局ビーコン(Mic-E)の送出時機とフレーム。ハードウェアに依存しないので PC でもテストできる。

送出の規則(SmartBeaconing はしない):
    スイッチがオンで 2D 以上の測位があれば、最初の 1 回はすぐ送る
    前回送った位置から MOVE_M 以上離れていれば、前回から MOVING_S たったら送る
    離れていなければ STILL_S ごとに送る
スイッチをオフにすると前回の記録を消す(次にオンにしたらすぐ送る)。
"""

import time

from aprs import mice
from aprs.position import KNOT_KMH
from core import geo
from tnc.ax25 import Address, Frame

MOVING_S = 90
STILL_S = 600
MOVE_M = 10.0


class Beacon:
    def __init__(self, moving_s=MOVING_S, still_s=STILL_S, move_m=MOVE_M):
        self.moving_s = moving_s
        self.still_s = still_s
        self.move_m = move_m
        self.last_ms = None  # 前回送った ticks_ms / None = まだ
        self.last_pos = None  # 前回送った (lat, lon)

    def reset(self):
        self.last_ms = None
        self.last_pos = None

    def moved(self, lat, lon):
        if self.last_pos is None:
            return True
        return geo.distance_km(self.last_pos[0], self.last_pos[1], lat, lon) * 1000 >= self.move_m

    def next_s(self, lat, lon, now_ms=None):
        """次に送るまでの秒(0 = 今送る)。位置が分からなければ None"""
        if lat is None:
            return None
        if self.last_ms is None:
            return 0
        now = time.ticks_ms() if now_ms is None else now_ms
        wait = self.moving_s if self.moved(lat, lon) else self.still_s
        return max(0, wait - time.ticks_diff(now, self.last_ms) // 1000)

    def due(self, lat, lon, now_ms=None):
        return self.next_s(lat, lon, now_ms) == 0

    def sent(self, lat, lon, now_ms=None):
        self.last_ms = time.ticks_ms() if now_ms is None else now_ms
        self.last_pos = (lat, lon)


def mice_frame(src, path, lat, lon, speed_kn=0.0, course=0.0, alt=None, symbol=("/", ">"),
               comment=b"", message=0):
    """Mic-E の位置フレーム。src: 'JK1BBJ-7'、path: ('WIDE1-1', ...)、speed_kn: ノット、
    course: 度(速度が 0 なら不明として送る)、alt: m / None、
    message: mice.MESSAGES の番号(0 = Off Duty、1 = En Route)"""
    c = int(course + 0.5) % 360 if speed_kn > 0 else 0
    if speed_kn > 0 and c == 0:
        c = 360  # Mic-E の 0 は「不明」なので真北は 360
    dst, info = mice.encode(lat, lon, speed=speed_kn * KNOT_KMH, course=c, table=symbol[0],
                            code=symbol[1], message=message, alt=alt, comment=comment)
    return Frame(Address(dst), Address.parse(src), [Address.parse(p) for p in path], info)
