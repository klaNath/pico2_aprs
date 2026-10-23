"""画面の切り替えとボタン操作、必要な部分だけの再描画。

ボタン(Display Pack、押すと Low):
    A: 一覧で 1 つ上(新しい側)を選ぶ
    B: 一覧で 1 つ下(古い側)を選ぶ
    X: 地図画面 / メイン画面の切り替え
    Y: 選択を解除して最新局の自動表示に戻す
poll() を Core0 のループから 50 ms 程度ごとに呼ぶ。描画は変化のあった領域だけ。
"""

import time

from machine import Pin

from ui import views


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
        self._dirty_all = True

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
        if self._dirty_all or changed:
            self.draw_all(now_ms)
        elif tick:
            self.draw_tick(now_ms)
        self._seen_version = self.st.version
        self._last_sec = sec
        self._dirty_all = False

    # ---- 描画
    def draw_all(self, now_ms):
        lay = self.lay
        if self.map_mode:
            self.scr.region(0, 0, lay.w, lay.h, lambda fb, dy: views.draw_map(
                fb, dy, lay, self.st, self.status, now_ms))
            return
        self.scr.region(0, 0, lay.list_w + 1, lay.h, lambda fb, dy: views.draw_list(
            fb, dy, lay, self.st, self.selected, now_ms))
        self._draw_detail(now_ms)
        self._draw_status()

    def draw_tick(self, now_ms):
        if self.map_mode:
            return
        self._draw_detail(now_ms)
        self._draw_status()

    def _draw_detail(self, now_ms):
        lay = self.lay
        shown = self.selected if self.selected is not None else self.st.latest()
        self.scr.region(lay.right_x, 0, lay.right_w, lay.detail_h, lambda fb, dy: views.draw_detail(
            fb, dy, lay, shown, self.status, now_ms))

    def _draw_status(self):
        lay = self.lay
        self.scr.region(lay.right_x, lay.status_y, lay.right_w, lay.status_h,
                        lambda fb, dy: views.draw_status(fb, dy, lay, self.status))
