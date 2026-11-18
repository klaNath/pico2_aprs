"""受信した局の表。表示(一覧・詳細・地図)はここから読む。

- 局はコールサイン(SSID 込み)ごとに 1 件。新しく受信した順に並ぶ
- オブジェクト/アイテムは名前ごとに 1 件(obj が True)。局とは別に数えるので、局と同じ名前の
  オブジェクトも別の件になる。オブジェクトだけを送った送信局は表に入らない
- 最後の受信から STALE_S(60 分)を過ぎた局は「古い」扱い(一覧では色を変える。消さない)
- 表の上限 MAX_STATIONS を超えたら、いちばん古い局から捨てる(メモリを一定に保つ)
- 位置を持つパケットを受けたら位置を更新する。位置の無いパケットでは前の位置を残す
- 自局(own_call と SSID まで一致)のパケットは、デジピーターで中継されて戻ってきたもの。
  own フラグを立て、地図には出さない(一覧では色を変えて区別する)
時刻は time.ticks_ms() の値(ラップアラウンドするので差は ticks_diff で取る)。
microSD から復元した局(core.stationfile)は、復元した時刻を heard_ms に、それまでの経過秒を
age0_s に持つ(ticks_diff は約 6 日までしか正しくないので、起動前の経過時間は ticks に入れない)。
"""

import time

try:
    from typing import Any, Optional  # noqa: F401
except ImportError:
    pass

STALE_S = 60 * 60
MAX_STATIONS = 64
MAP_STATIONS = 6


def is_object(packet):
    return packet.kind in ("object", "item") and bool(packet.name)


def station_key(packet):
    """表のキー: 局は送信局(SSID 込み)の str。オブジェクト/アイテムは ("obj", 名前)
    (局と同じ名前のオブジェクトを別の件にする)"""
    return ("obj", packet.name) if is_object(packet) else packet.src


class Station:
    __slots__ = ("call", "obj", "packet", "heard_ms", "pos_ms", "count", "lat", "lon", "own",
                 "level_db", "clip", "age0_s", "pos_age0_s")

    def __init__(self, call, own=False, obj=False):
        self.call = call  # 表示名(局はコールサイン、オブジェクトは名前)
        self.obj = obj  # オブジェクト/アイテム
        self.own = own  # 自局のパケット(デジピーターからのエコー)。オブジェクトでは立てない
        self.packet = None  # 最後に受けた aprs.packet.Packet
        self.heard_ms: "Any" = 0  # time.ticks_ms() の値(スタブでは専用の型)
        self.pos_ms: "Any" = None  # 最後に位置を受けた時刻(ticks_ms)
        self.count = 0
        self.lat: "Optional[float]" = None
        self.lon: "Optional[float]" = None
        # 最後に受けたパケットの音量(ピーク、dBFS)とクリップしたか / None = 測っていない
        self.level_db: "Optional[float]" = None
        self.clip = False
        # 復元した局: heard_ms / pos_ms の時点で、すでにたっていた秒数(受信し直すと 0)
        self.age0_s = 0
        self.pos_age0_s = 0

    def key(self):
        """表のキー(station_key と同じ形)"""
        return ("obj", self.call) if self.obj else self.call

    def age_s(self, now_ms):
        return time.ticks_diff(now_ms, self.heard_ms) // 1000 + self.age0_s

    def pos_age_s(self, now_ms):
        """最後に位置を受けてからの秒数 / None = 位置を受けていない"""
        if self.pos_ms is None:
            return None
        return time.ticks_diff(now_ms, self.pos_ms) // 1000 + self.pos_age0_s

    def stale(self, now_ms):
        return self.age_s(now_ms) >= STALE_S


class Stations:
    def __init__(self, max_stations=MAX_STATIONS, own_call=None):
        """own_call: 自局 'JA1XYZ-7'(架空の例。SSID 0 なら 'JA1XYZ')。None なら自局の判定をしない"""
        self.max = max_stations
        self.own_call = own_call
        self.order = []  # 新しい順の Station
        self.by_call = {}
        self.version = 0  # 変わるたびに増える(表示側の再描画判定用)

    def update(self, packet, now_ms=None, level_db=None, clip=False):
        """aprs.packet.Packet を取り込む。オブジェクト/アイテムは名前で 1 局として扱う。
        level_db, clip: このパケットの音量(ADC 入力のとき。core.level)"""
        if now_ms is None:
            now_ms = time.ticks_ms()
        key = station_key(packet)
        st = self.by_call.get(key)
        if st is None:
            st = self._new(packet)
            self.by_call[key] = st
        else:
            self.order.remove(st)
        self.order.insert(0, st)
        st.packet = packet
        st.heard_ms = now_ms
        st.age0_s = 0
        st.count += 1
        st.level_db = level_db
        st.clip = clip
        if packet.lat is not None and packet.lon is not None:
            st.lat = packet.lat
            st.lon = packet.lon
            st.pos_ms = now_ms
            st.pos_age0_s = 0
        while len(self.order) > self.max:
            old = self.order.pop()
            del self.by_call[old.key()]
        self.version += 1
        return st

    def restore(self, packet, age_s, now_ms, count=1, lat=None, lon=None, pos_age_s=None,
                level_db=None, clip=False):
        """保存しておいた局(最後のパケット packet)を表の末尾(いちばん古い側)に加える。
        新しい順に呼ぶこと。
        age_s: 最後の受信からの秒数、pos_age_s: 最後に位置を受けてからの秒数。
        起動後にすでに受信した局は、受信数を足し、位置が無ければ保存した位置を使う(それ以外は今の値)。
        表がいっぱいなら加えない。加えた/足した Station を返す(加えなければ None)"""
        key = station_key(packet)
        st = self.by_call.get(key)
        if st is not None:
            st.count += count
            if st.lat is None and lat is not None and pos_age_s is not None:
                st.lat, st.lon = lat, lon
                st.pos_ms = now_ms
                st.pos_age0_s = pos_age_s
            self.version += 1
            return st
        if len(self.order) >= self.max:
            return None
        st = self._new(packet)
        st.packet = packet
        st.heard_ms = now_ms
        st.age0_s = age_s
        st.count = count
        st.level_db = level_db
        st.clip = clip
        if lat is not None and lon is not None and pos_age_s is not None:
            st.lat, st.lon = lat, lon
            st.pos_ms = now_ms
            st.pos_age0_s = pos_age_s
        self.by_call[key] = st
        self.order.append(st)
        self.version += 1
        return st

    def _new(self, packet):
        if is_object(packet):
            return Station(packet.name, obj=True)
        return Station(packet.src, own=packet.src == self.own_call)

    def latest(self):
        return self.order[0] if self.order else None

    def for_map(self, n=MAP_STATIONS):
        """位置を持つ局のうち新しい順に n 局(自局のエコーは除く)"""
        out = []
        for st in self.order:
            if st.lat is not None and not st.own:
                out.append(st)
                if len(out) == n:
                    break
        return out

    def __len__(self):
        return len(self.order)
