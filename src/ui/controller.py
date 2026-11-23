"""画面の切り替えとボタン操作、必要な部分だけの再描画。

ボタン(Display Pack、押すと Low):
    A: 一覧で 1 つ上(新しい側)を選ぶ
    B: 一覧で 1 つ下(古い側)を選ぶ
    X: 地図画面 / メイン画面の切り替え(240x135 のとき。320x240 は地図が常に出ている)
    Y: 選択を解除して最新局の自動表示に戻す
ボタンが無い(pins が空)ときは選択も画面切り替えもしない。320x240 では小さな地図が常に出ている。
poll() を Core0 のループから 50 ms 程度ごとに呼ぶ。描画は変化のあった領域だけ。
時計・経過時間は 1 秒ごと。詳細欄はほかの行が変わっていなければ、経過時間の枠とバッジだけ描き直す
(詳細欄全体は約 55 ms、枠とバッジは数 ms)。小さな地図は局の地図(MAP)と衛星の位置(SKY)を切り替え
(周期は views.small_map_kind)、切り替えのときと MAP_TICK_S ごとに描き直す。
右上のバッジ(BLE の状態アイコンと、測位状態の色の枠)は、枠が 0.5 秒ごとに点灯・消灯し(1 Hz の点滅)、
BLE の広告中はアイコンが ADV_FRAME_MS ごとにコマ送りする。変わったときバッジの部分だけ描き直す。
"""

import time

from machine import Pin

from ui import views

MAP_TICK_S = 5
METER_MS = 200  # 受信音量の縦棒を描き直す間隔
ADV_FRAME_MS = 400  # BLE の広告中のアニメーションの 1 コマ
ADV_FRAMES = 12  # コマの番号の周期(ui.ble_icons の 4 コマと 3 コマの公倍数)


class Ui:
    def __init__(self, screen, stations, status, pins, meter=False):
        """pins: (A, B, X, Y) の GPIO 番号"""
        self.scr = screen
        self.st = stations
        self.status = status
        self.lay = views.Layout(screen.width, screen.height, meter)
        self._meter_ms = 0  # 受信音量の縦棒を最後に描いた時刻
        self.btn = [Pin(p, Pin.IN, Pin.PULL_UP) for p in pins]
        self._prev = [1, 1, 1, 1]
        self.map_mode = False
        self.selected = None  # 選択中の Station(None = 最新を自動表示)
        self._seen_version = -1
        self._last_sec = -1
        self._blink = True
        self._ble = None  # バッジに最後に描いた BLE の状態
        self._frame = 0  # 広告中のアニメーションのコマ
        self._map_kind = None  # 小さな地図に最後に描いたもの
        self._dirty_all = True
        self._cleared = False  # 画面全体を黒で塗ったか(起動後と、全画面の表示から戻るとき False)
        self._pk_drawn = None  # 全画面で出しているパスキー(None = 通常の画面)
        self._msg_drawn = None  # 全画面で出している自局宛のメッセージ(None = 通常の画面)
        self._detail_drawn = None  # 詳細欄に最後に描いた行(views.detail_body)

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
        pk = self.status.passkey
        if pk != self._pk_drawn:  # BLE のペアリング中は、パスキーを全画面で出す
            self._pk_drawn = pk
            self._msg_drawn = None  # パスキーのあとにメッセージを出し直す
            if pk is None:
                self._back_to_normal()  # 通常の画面に戻す
            else:
                self._draw_passkey(pk)
        if pk is not None:
            return
        # 自局宛のメッセージ: status.message_until まで全画面で出す(パスキーが優先)
        msg = self.status.message
        if msg is not None and time.ticks_diff(now_ms, self.status.message_until) >= 0:
            self.status.message = msg = None
        if msg is not self._msg_drawn:
            self._msg_drawn = msg
            if msg is None:
                self._back_to_normal()
            else:
                self._draw_message(msg)
        if msg is not None:
            return
        for k in self._pressed():
            if k == "A":
                self._move(-1)
            elif k == "B":
                self._move(1)
            elif k == "X":
                self.map_mode = not self.map_mode
                self._cleared = False  # 全画面の地図と欄の画面を切り替える
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
        ble = self.status.ble
        frame = 0
        if ble == "adv" or ble == "conn":
            frame = (time.ticks_diff(now_ms, 0) // ADV_FRAME_MS) % ADV_FRAMES
        badge_changed = blink_changed or ble != self._ble or frame != self._frame
        self._ble = ble
        self._frame = frame
        if self._dirty_all or changed:
            self.draw_all(now_ms)
        elif tick:
            self.draw_tick(now_ms)
            if self.lay.wide and (sec % MAP_TICK_S == 0
                                  or views.small_map_kind(self.st, now_ms, sec) != self._map_kind):
                self._draw_small_map(now_ms)
        elif badge_changed and not (self.map_mode and not self.lay.wide):
            self._draw_badge()
        if self.lay.meter_w and time.ticks_diff(now_ms, self._meter_ms) >= METER_MS:
            self._draw_meter(now_ms)
        self._seen_version = self.st.version
        self._last_sec = sec
        self._dirty_all = False

    def _back_to_normal(self):
        """全画面の表示(パスキー・メッセージ)から通常の画面に戻す。欄の間の隙間に
        前の画面の文字が残らないよう、全体を塗ってから描く"""
        self._dirty_all = True
        self._cleared = False

    # ---- 描画
    def draw_all(self, now_ms):
        lay = self.lay
        if not self._cleared:
            # 欄の間の 1px の隙間(枠線の脇)はどの欄も描かないので、LCD のメモリに残る
            # 電源投入直後のゴミや、全画面の表示(パスキーなど)の文字が見える。全体を黒で塗る
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

    def _draw_passkey(self, pk):
        lay = self.lay
        self.scr.region(0, 0, lay.w, lay.h, lambda fb, dy: views.draw_passkey(
            fb, dy, lay.w, lay.h, pk))

    def _draw_message(self, msg):
        lay = self.lay
        self.scr.region(0, 0, lay.w, lay.h, lambda fb, dy: views.draw_message(
            fb, dy, lay.w, lay.h, msg[0], msg[1]))

    def draw_tick(self, now_ms):
        if self.map_mode and not self.lay.wide:
            return
        shown, lines = self._detail(now_ms)
        if lines != self._detail_drawn:
            self._draw_detail(now_ms, shown, lines)
        else:  # 毎秒変わるのは経過時間とバッジだけ(詳細欄全体の 1/30 ほどの面積)
            self._draw_age(now_ms, shown)
            self._draw_badge()
        self._draw_status()

    def _detail(self, now_ms):
        shown = self.selected if self.selected is not None else self.st.latest()
        return shown, views.detail_body(self.lay, shown, self.status, now_ms)

    def _draw_detail(self, now_ms, shown=None, lines=None):
        if lines is None:
            shown, lines = self._detail(now_ms)
        lay = self.lay
        self._detail_drawn = lines
        self.scr.region(lay.right_x, 0, lay.right_w, lay.detail_h, lambda fb, dy: views.draw_detail(
            fb, dy, lay, lines, shown, self.status, now_ms, self._blink, self._frame))

    def _draw_age(self, now_ms, shown):
        """詳細欄の経過時間の枠だけ描き直す"""
        lay = self.lay
        age = views.detail_age(shown, self.status.lat, now_ms, lay.wide)
        self.scr.region(lay.right_x + lay.age_x, lay.age_y, lay.age_w, views.CH,
                        lambda fb, dy: views.draw_age(fb, 0, dy, lay, age))

    def draw_status_now(self):
        """自局の状態の欄だけすぐ描き直す(送信を始める前に [TX] を出す)。
        全画面の表示(パスキー・メッセージ)の間は描かない"""
        if self._pk_drawn is None and self._msg_drawn is None:
            self._draw_status()

    def _draw_meter(self, now_ms):
        """受信音量の縦棒だけ描き直す(小さいので METER_MS ごとに描いても軽い)"""
        lay = self.lay
        self._meter_ms = now_ms
        self.scr.region(lay.meter_x, lay.status_y, lay.meter_w, lay.status_h,
                        lambda fb, dy: views.draw_meter(fb, dy, lay.meter_w, lay.status_h,
                                                        self.status.level))

    def _draw_badge(self):
        lay = self.lay
        self.scr.region(lay.w - lay.badge, 0, lay.badge, lay.badge,
                        lambda fb, dy: views.draw_badge(fb, 0, dy, lay, self.status, self._blink,
                                                        self._frame))

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
