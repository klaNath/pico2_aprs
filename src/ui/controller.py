"""画面の切り替えとボタン操作、必要な部分だけの再描画。

ボタン(Display Pack、押すと Low):
    A: 一覧で 1 つ上(新しい側)を選ぶ
    B: 一覧で 1 つ下(古い側)を選ぶ
    X: 地図画面 / メイン画面の切り替え(240x135 のとき。320x240 は地図が常に出ている)
    Y: 選択を解除して最新局の自動表示に戻す
ボタンが無い(pins が空)ときは選択も画面切り替えもしない。320x240 では小さな地図が常に出ている。
poll() を Core0 のループから 50 ms 程度ごとに呼ぶ。描画は変化のあった領域だけ。
時計・経過時間は 1 秒ごと。小さな地図は局の地図(MAP)と衛星の位置(SKY)を切り替え
(周期は views.small_map_kind)、切り替えのときと MAP_TICK_S ごとに描き直す。
右上の測位状態の丸は 0.5 秒ごとに点灯・消灯する(1 Hz の点滅、丸の部分だけ描き直す)。
"""

import time

from machine import Pin

from ui import views

MAP_TICK_S = 5


class Ui:
    def __init__(self, screen, stations, status, pins):
        """pins: (A, B, X, Y) の GPIO 番号"""
        self.scr = screen
        self.st = stations
        self.status = status
        self.lay = views.Layout(screen.width, screen.height)
        self.btn = [Pin(p, Pin.IN, Pin.PULL_UP) for p in pins]
        self._prev = [1, 1, 1, 1]
        self.map_mode = False
        self.selected = None  # 選択中の Station(None = 最新を自動表示)
        self._seen_version = -1
        self._last_sec = -1
        self._blink = True
        self._map_kind = None  # 小さな地図に最後に描いたもの
        self._dirty_all = True
        self._cleared = False  # 起動後に画面全体を一度黒で塗ったか

    # ---- ボタン
    def _pressed(self):
        out = []
        for i, b in enumerate(self.btn):
            v = b.value()
            if v == 0 and self._prev[i] == 1:
                out.append("ABXY"[i])
            self._prev[i] = v
        return out

    def _move(self, step):
        order = self.st.order
        if not order:
            return
        cur = self.selected if self.selected in order else order[0]
        i = order.index(cur) + step
        i = max(0, min(len(order) - 1, i))
        self.selected = order[i]

    def poll(self, now_ms=None):
        if now_ms is None:
            now_ms = time.ticks_ms()
        for k in self._pressed():
            if k == "A":
                self._move(-1)
            elif k == "B":
                self._move(1)
            elif k == "X":
                self.map_mode = not self.map_mode
            elif k == "Y":
                self.selected = None
            self._dirty_all = True
        if self.selected is not None and self.selected not in self.st.order:
            self.selected = None  # 表から捨てられた
        changed = self.st.version != self._seen_version
        sec = time.ticks_diff(now_ms, 0) // 1000
        tick = sec != self._last_sec  # 経過時間・時計の表示を 1 秒ごとに更新
        blink = (time.ticks_diff(now_ms, 0) // 500) % 2 == 0
        blink_changed = blink != self._blink
        self._blink = blink
        if self._dirty_all or changed:
            self.draw_all(now_ms)
        elif tick:
            self.draw_tick(now_ms)
            if self.lay.wide and (sec % MAP_TICK_S == 0
                                  or views.small_map_kind(self.st, now_ms, sec) != self._map_kind):
                self._draw_small_map(now_ms)
        elif blink_changed and not (self.map_mode and not self.lay.wide):
            self._draw_badge()
        self._seen_version = self.st.version
        self._last_sec = sec
        self._dirty_all = False

    # ---- 描画
    def draw_all(self, now_ms):
        lay = self.lay
        if not self._cleared:
            # 欄の間の 1px の隙間(枠線の脇)はどの欄も描かないので、LCD のメモリに残る
            # 電源投入直後のゴミが見える。最初に一度だけ全体を黒で塗る
            self.scr.fill(0)
            self._cleared = True
        if self.map_mode and not lay.wide:
            self.scr.region(0, 0, lay.w, lay.h, lambda fb, dy: views.draw_map(
                fb, dy, lay.w, lay.h, self.st, self.status, now_ms))
            return
        self.scr.region(0, 0, lay.list_w + 2, lay.list_h, lambda fb, dy: views.draw_list(
            fb, dy, lay, self.st, self.selected, now_ms))
        self._draw_detail(now_ms)
        self._draw_status()
        if lay.wide:
            self._draw_small_map(now_ms)

    def draw_tick(self, now_ms):
        if self.map_mode and not self.lay.wide:
            return
        self._draw_detail(now_ms)
        self._draw_status()

    def _draw_detail(self, now_ms):
        lay = self.lay
        shown = self.selected if self.selected is not None else self.st.latest()
        self.scr.region(lay.right_x, 0, lay.right_w, lay.detail_h, lambda fb, dy: views.draw_detail(
            fb, dy, lay, shown, self.status, now_ms, self._blink))

    def draw_status_now(self):
        """自局の状態の欄だけすぐ描き直す(送信を始める前に [TX] を出す)"""
        self._draw_status()

    def _draw_badge(self):
        lay = self.lay
        self.scr.region(lay.w - views.BADGE, 0, views.BADGE, views.BADGE,
                        lambda fb, dy: views.draw_badge(fb, 0, dy, self.status, self._blink))

    def _draw_status(self):
        lay = self.lay
        self.scr.region(lay.status_x, lay.status_y, lay.status_w, lay.status_h,
                        lambda fb, dy: views.draw_status(fb, dy, lay, self.status))

    def _draw_small_map(self, now_ms):
        lay = self.lay
        w = lay.map_w + 2  # 左端の 2px は区切り線
        self._map_kind = views.small_map_kind(self.st, now_ms, time.ticks_diff(now_ms, 0) // 1000)
        if self._map_kind == "sky":
            draw = lambda fb, dy: views.draw_sky(fb, dy, w, lay.map_h, self.status.signals)  # noqa: E731
        else:
            draw = lambda fb, dy: views.draw_map(fb, dy, w, lay.map_h, self.st,  # noqa: E731
                                                 self.status, now_ms, small=True)
        self.scr.region(lay.map_x - 2, lay.map_y, w, lay.map_h, draw)
