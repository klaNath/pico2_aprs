"""受信した局の表。表示(一覧・詳細・地図)はここから読む。

- 局はコールサイン(SSID 込み)ごとに 1 件。新しく受信した順に並ぶ
- 最後の受信から STALE_S(60 分)を過ぎた局は「古い」扱い(一覧では色を変える。消さない)
- 表の上限 MAX_STATIONS を超えたら、いちばん古い局から捨てる(メモリを一定に保つ)
- 位置を持つパケットを受けたら位置を更新する。位置の無いパケットでは前の位置を残す
時刻は time.ticks_ms() の値(ラップアラウンドするので差は ticks_diff で取る)。
"""

import time

try:
    from typing import Any, Optional  # noqa: F401
except ImportError:
    pass

STALE_S = 60 * 60
MAX_STATIONS = 64
MAP_STATIONS = 6


class Station:
    __slots__ = ("call", "packet", "heard_ms", "pos_ms", "count", "lat", "lon")

    def __init__(self, call):
        self.call = call
        self.packet = None  # 最後に受けた aprs.packet.Packet
        self.heard_ms: "Any" = 0  # time.ticks_ms() の値(スタブでは専用の型)
        self.pos_ms: "Any" = None  # 最後に位置を受けた時刻(ticks_ms)
        self.count = 0
        self.lat: "Optional[float]" = None
        self.lon: "Optional[float]" = None

    def age_s(self, now_ms):
        return time.ticks_diff(now_ms, self.heard_ms) // 1000

    def stale(self, now_ms):
        return self.age_s(now_ms) >= STALE_S


class Stations:
    def __init__(self, max_stations=MAX_STATIONS):
        self.max = max_stations
        self.order = []  # 新しい順の Station
        self.by_call = {}
        self.version = 0  # 変わるたびに増える(表示側の再描画判定用)

    def update(self, packet, now_ms=None):
        """aprs.packet.Packet を取り込む。オブジェクト/アイテムは名前で 1 局として扱う。"""
        if now_ms is None:
            now_ms = time.ticks_ms()
        call = packet.name if packet.kind in ("object", "item") and packet.name else packet.src
        st = self.by_call.get(call)
        if st is None:
            st = Station(call)
            self.by_call[call] = st
        else:
            self.order.remove(st)
        self.order.insert(0, st)
        st.packet = packet
        st.heard_ms = now_ms
        st.count += 1
        if packet.lat is not None and packet.lon is not None:
            st.lat = packet.lat
            st.lon = packet.lon
            st.pos_ms = now_ms
        while len(self.order) > self.max:
            old = self.order.pop()
            del self.by_call[old.call]
        self.version += 1
        return st

    def latest(self):
        return self.order[0] if self.order else None

    def for_map(self, n=MAP_STATIONS):
        """位置を持つ局のうち新しい順に n 局"""
        out = []
        for st in self.order:
            if st.lat is not None:
                out.append(st)
                if len(out) == n:
                    break
        return out

    def __len__(self):
        return len(self.order)
