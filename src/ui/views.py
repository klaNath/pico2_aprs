"""画面の描画: メイン画面(局の一覧・最新局の詳細・自局の状態・地図)と全画面の地図。

配置は画面の大きさから決める。
    320x240: 上段に一覧と詳細、下段に自局の状態(その下に衛星の強度の棒グラフ)と小さな地図(常に表示)
    240x135: 左に一覧、右上に詳細、右下に自局の状態。地図はボタンで全画面に切り替える
文字は framebuf 標準の 8x8 フォント(ASCII のみ)。描画関数は ui.screen.Screen.region に渡す
draw(fb, dy) の形で、領域の左上を (0, 0) とした座標に dy を足して描く。
"""

import math

from core import geo
from hw.rgb565 import rgb

try:
    from typing import Sequence  # noqa: F401  (型注釈用。MicroPython には無い)
except ImportError:
    pass

BLACK = rgb(0, 0, 0)
WHITE = rgb(255, 255, 255)
GRAY = rgb(110, 110, 110)
DARK = rgb(40, 40, 40)
YELLOW = rgb(255, 220, 0)
GREEN = rgb(0, 220, 80)
CYAN = rgb(0, 200, 255)
RED = rgb(255, 40, 40)
ORANGE = rgb(255, 140, 0)
MAGENTA = rgb(255, 80, 255)  # 自局のエコー
BORDER = rgb(70, 110, 190)  # 欄の区切り線(2px)。地図の円や目盛り(DARK、1px)と区別する

CH = 8  # 文字の幅・高さ
ROW = 9  # 行の高さ(1 ピクセルの行間)
STATUS_LINES_WIDE = 4  # 320x240 の自局の状態の行数(その下が衛星の棒グラフ)

# 衛星の系(UBX の gnssId)-> (RINEX の記号, 色)
SYSTEMS = {
    0: ("G", GREEN),  # GPS
    1: ("S", YELLOW),  # SBAS
    2: ("E", CYAN),  # Galileo
    3: ("C", RED),  # BeiDou
    5: ("J", MAGENTA),  # QZSS
    6: ("R", ORANGE),  # GLONASS
}
CNO_MAX = 50  # 棒グラフの上端の C/N0(dBHz)
BADGE = 2 * CH  # 右上の測位状態の丸の大きさ

KIND_LABEL = {
    "position": "Position",
    "mice": "Mic-E",
    "object": "Object",
    "item": "Item",
    "message": "Message",
    "status": "Status",
    "other": "Other",
}


class Status:
    """自局の状態(表示用)。GNSS・ビーコン・送受信の各処理が書き込む。"""

    def __init__(self):
        # 型注釈は pyright 用(関数内の注釈は実行時に評価されない)
        self.fix = 0  # 0 = 測位なし、2 = 2D、3 = 3D
        self.sats = 0  # 使用衛星数(全系の合計)
        self.sats_view = 0  # 可視衛星数(全系の合計)
        self.hdop: "float | None" = None
        self.aug: "tuple[str, ...]" = ()  # 使えている補強('QZSS', 'SBAS', 'SLAS')
        # 衛星 (gnssId, C/N0, 使用中か, 仰角 / None, 方位角)。C/N0 が 0 なら捕捉していない
        self.signals: "Sequence[tuple[int, int, bool, int | None, int]]" = []
        self.grid: "str | None" = None  # Grid Locator 8 文字
        self.lat: "float | None" = None
        self.lon: "float | None" = None
        self.utc_s: "int | None" = None  # UTC の秒(time.time() と同じ起点)/ None = 時刻不明
        self.beacon_on = False
        self.next_beacon_s: "int | None" = None
        self.rx_count = 0
        self.tx_count = 0
        self.tx_active = False
        self.rx_recent = False


class Layout:
    def __init__(self, width, height):
        self.w = width
        self.h = height
        self.list_w = 9 * CH  # コールサイン 9 文字。その右に 2px の区切り線と 1px の隙間
        self.right_x = self.list_w + 3
        self.right_w = width - self.right_x
        self.cols = self.right_w // CH
        # 下段に自局の状態・衛星の棒グラフ・小さな地図を出すか(320x240 など)
        self.wide = width >= 320 and height >= 200
        if self.wide:
            self.detail_h = 122  # 13 行 + 区切り線。一覧も同じ高さ
            self.list_h = self.detail_h
            self.status_y = self.detail_h + 1
            self.status_h = height - self.status_y
            self.map_w = self.map_h = self.status_h  # 正方形
            self.map_x = width - self.map_w
            self.map_y = self.status_y
            self.status_x = 0
            self.status_w = self.map_x - 3  # 地図の左に 2px の区切り線と 1px の隙間
            self.sats_y = 1 + STATUS_LINES_WIDE * ROW + 1  # 状態の欄の中の棒グラフの上端
        else:
            self.detail_h = (height * 64) // 100  # 上 64% を詳細、残りを自局の状態
            self.list_h = height
            self.status_y = self.detail_h + 1
            self.status_h = height - self.status_y
            self.map_x = self.map_y = self.map_w = self.map_h = 0
            self.status_x = self.right_x
            self.status_w = self.right_w
            self.sats_y = self.status_h  # 棒グラフなし
        self.list_rows = (self.list_h - ROW) // ROW  # 見出し 1 行を除く
        self.status_cols = self.status_w // CH
        self.status_rows = self.status_h // ROW


def _clip(s, n):
    return s if len(s) <= n else s[:n]


def _right(fb, s, x_right, y, color):
    fb.text(s, x_right - len(s) * CH, y, color)


def draw_list(fb, dy, lay, stations, selected, now_ms):
    """左: コールサインの一覧(新しい順)。selected は選択中の Station(None = 最新を自動表示)"""
    fb.text("HEARD %d" % len(stations), 0, 0 + dy, CYAN)
    fb.fill_rect(lay.list_w, dy, 2, lay.list_h, BORDER)
    if lay.wide:
        fb.fill_rect(0, lay.list_h - 2 + dy, lay.list_w + 2, 2, BORDER)
    shown = selected if selected is not None else stations.latest()
    for i, st in enumerate(stations.order[: lay.list_rows]):
        y = ROW * (i + 1) + dy
        name = _clip(st.call, 9)
        if st is shown:
            fb.fill_rect(0, y - 1, lay.list_w - 1, ROW, YELLOW if selected is not None else DARK)
            fb.text(name, 0, y, BLACK if selected is not None else WHITE)
        else:
            fb.text(name, 0, y, MAGENTA if st.own else GRAY if st.stale(now_ms) else WHITE)


def _wrap(text, cols, nlines):
    out = []
    while text and len(out) < nlines:
        out.append(text[:cols])
        text = text[cols:]
    return out


def detail_lines(st, own_lat, own_lon, now_ms, cols, head_cols=None):
    """最新局(または選択局)の詳細 -> [(文字列, 色), ...]。テストしやすいよう描画と分けてある。

    head_cols: 1・2 行目の幅(右上の丸を避ける)。None なら cols。
    """
    if st is None:
        return [("no station heard", GRAY)]
    hc = cols if head_cols is None else head_cols
    p = st.packet
    lines = []
    age = geo.fmt_age(st.age_s(now_ms))
    head = _clip(st.call, hc - len(age) - 1)
    lines.append((head + " " * (hc - len(head) - len(age)) + age,
                  MAGENTA if st.own else GRAY if st.stale(now_ms) else YELLOW))
    kind = KIND_LABEL.get(p.kind, p.kind)
    if p.kind == "mice" and p.mice_msg:
        kind += " " + p.mice_msg
    if p.table and p.code:
        kind += " " + p.table + p.code
    if st.lat is not None and own_lat is not None:
        d = geo.fmt_distance(geo.distance_km(own_lat, own_lon, st.lat, st.lon))
        b = geo.compass16(geo.bearing_deg(own_lat, own_lon, st.lat, st.lon))
        s = "DIST %s  DIR %s" % (d, b)
        lines.append((s if len(s) <= hc else _clip("DIST %s DIR %s" % (d, b), hc), GREEN))
    elif st.lat is not None:
        lines.append(("own position unknown", GRAY))
    else:
        lines.append(("no position", GRAY))
    lines.append((_clip(kind, cols), CYAN))
    if st.lat is not None:
        lines.append((geo.fmt_latlon(st.lat, st.lon), WHITE))
    mv = []
    if p.speed is not None:
        mv.append("%dkm/h" % int(p.speed + 0.5))
    if p.course is not None:
        mv.append(geo.compass16(p.course))
    if p.alt is not None:
        mv.append("%dm" % int(p.alt))
    if mv:
        lines.append((" ".join(mv), WHITE))
    if p.path:
        digi = [d for d in p.path if d.endswith("*")]
        lines.append((_clip("via " + (digi[-1] if digi else "direct"), cols), GRAY))
    text = p.comment
    if p.kind == "message":
        text = "to %s: %s" % (p.addressee, p.text or "")
    elif p.kind == "status":
        text = p.text or ""
    for t in _wrap(text or "", cols, 2):
        lines.append((t, WHITE))
    return lines


def draw_detail(fb, dy, lay, st, status, now_ms, blink_on=True):
    lines = detail_lines(st, status.lat, status.lon, now_ms, lay.cols,
                         lay.cols - (BADGE + CH - 1) // CH)
    max_lines = (lay.detail_h - 1) // ROW
    for i, (s, c) in enumerate(lines[:max_lines]):
        fb.text(s, 0, i * ROW + dy, c)
    fb.fill_rect(0, lay.detail_h - 2 + dy, lay.right_w, 2, BORDER)
    draw_badge(fb, lay.right_w - BADGE, dy, status, blink_on)


def badge_color(status):
    """測位状態の丸の色: 赤 = 測位なし、橙 = 2D、緑 = 3D、水色 = 3D で SBAS / SLAS の補正を使用中"""
    if status.fix < 2:
        return RED
    if status.fix == 2:
        return ORANGE
    return CYAN if "SBAS" in status.aug or "SLAS" in status.aug else GREEN


def draw_badge(fb, x, y, status, on):
    """右上の測位状態の丸(BADGE x BADGE)。on=False は消灯(1 Hz で点滅させる)"""
    r = BADGE // 2 - 1
    if on:
        fb.ellipse(x + BADGE // 2, y + BADGE // 2, r, r, badge_color(status), True)


def gnss_text(status, cols):
    """'3D 21/38 HDOP0.7 QZSS SBAS'(測位の種類 使用/可視 HDOP 使えている補強)。

    cols に収まらなければ HDOP、次に補強の名前を省く。
    """
    head = ("%dD" % status.fix) if status.fix >= 2 else "NO FIX"
    s = "%s %d/%d" % (head, status.sats, status.sats_view)
    h = " HDOP%.1f" % status.hdop if status.fix >= 2 and status.hdop is not None else ""
    a = "".join(" " + name for name in status.aug)
    for tail in (h + a, a, h):
        if len(s) + len(tail) <= cols:
            return s + tail
    return s


def _two(left, right, cols):
    """left を左寄せ、right を右寄せにした 1 行(収まらなければ間を 1 文字)"""
    return left + " " * max(1, cols - len(left) - len(right)) + right


def status_lines(status, cols, rows=4):
    """自局の状態 -> [(文字列, 色), ...]

    cols が 24 以上(320x240 の下段): 4 行。
        測位・衛星・補強 / 時刻と Grid / 位置 / ビーコンと送受信数
    それより狭い(240x135): 測位と時刻 / 位置 / ビーコン / 送受信数(rows が 5 以上なら Grid も)
    """
    lines = []
    t = geo.fmt_jst(status.utc_s) if status.utc_s is not None else "--:--:--"
    gps_color = GREEN if status.fix >= 2 else ORANGE
    grid = status.grid or "--------"
    pos = geo.fmt_latlon(status.lat, status.lon) if status.lat is not None else "position unknown"
    pos_color = WHITE if status.lat is not None else GRAY
    if status.beacon_on:
        nb = status.next_beacon_s
        bcn = "BCN ON" if nb is None else "BCN ON %d:%02d" % (nb // 60, nb % 60)
    else:
        bcn = "BCN OFF"
    bcn_color = GREEN if status.beacon_on else GRAY
    if cols >= 24:
        lines.append((gnss_text(status, cols), gps_color))
        lines.append((_two(t + " JST", grid, cols), WHITE if status.utc_s is not None else GRAY))
        lines.append((pos, pos_color))
        lines.append((_two(bcn, "RX %d TX %d" % (status.rx_count, status.tx_count), cols),
                      bcn_color))
        return lines
    gps = gnss_text(status, cols - len(t) - 1)
    lines.append((_two(gps, t, cols), gps_color))
    lines.append((pos, pos_color))
    lines.append((bcn, bcn_color))
    lines.append(("RX %d  TX %d" % (status.rx_count, status.tx_count), WHITE))
    if rows >= 5:
        lines.append(("GRID " + grid, WHITE))
    return lines


def draw_status(fb, dy, lay, status):
    lines = status_lines(status, lay.status_cols, lay.status_rows)
    for i, (s, c) in enumerate(lines):
        fb.text(s, 0, 1 + i * ROW + dy, c)
    # 送受信中の印: 320x240 は位置の行(20 文字)の右、240x135 は送受信数の行の右
    y = 1 + (2 if lay.wide else 3) * ROW + dy
    if status.tx_active:
        _right(fb, "[TX]", lay.status_w, y, RED)
    elif status.rx_recent:
        _right(fb, "[RX]", lay.status_w, y, GREEN)
    if lay.wide:
        draw_signals(fb, lay.sats_y + dy, lay.status_w, lay.status_h - lay.sats_y, status.signals)


def draw_signals(fb, y0, w, h, signals):
    """捕捉中の衛星の C/N0 の棒グラフ(系ごとに並べ、下に系の記号)。

    使用中は塗りつぶし、未使用は枠だけ。
    """
    signals = [s for s in signals if s[1]]  # 捕捉中だけ
    if not signals:
        fb.text("no satellites tracked", 0, y0 + 2, GRAY)
        return
    bar_h = h - ROW - 1
    base = y0 + bar_h  # 棒の下端
    for cno in (20, 40):  # 目盛り
        fb.hline(0, base - cno * bar_h // CNO_MAX, w, DARK)
    pitch = max(2, min(7, w // len(signals)))
    bw = max(1, pitch - 1)
    x = 0
    prev = None
    label_end = 0
    for gnss, cno, used, _, _ in signals:
        sym, col = SYSTEMS.get(gnss, ("?", GRAY))
        if gnss != prev:
            if x >= label_end:
                fb.text(sym, x, base + 2, col)
                label_end = x + CH + 1
            prev = gnss
        bh = max(1, min(cno, CNO_MAX) * bar_h // CNO_MAX)
        if used:
            fb.fill_rect(x, base - bh, bw, bh, col)
        else:
            fb.rect(x, base - bh, bw, bh, col)
        x += pitch


def _north(fb, dy, w):
    """右上の隅に北の矢印"""
    ax = w - 8
    fb.text("N", ax - 3, 0 + dy, CYAN)
    fb.vline(ax, ROW + dy, 14, CYAN)
    fb.line(ax, ROW + dy, ax - 4, ROW + 5 + dy, CYAN)
    fb.line(ax, ROW + dy, ax + 4, ROW + 5 + dy, CYAN)


def _small_frame(fb, dy, h, title):
    """小さな地図の左端の区切り線(2px)と左上の見出し"""
    fb.fill_rect(0, dy, 2, h, BORDER)
    fb.text(title, 4, 0 + dy, CYAN)


def draw_map(fb, dy, w, h, stations, status, now_ms, small=False):
    """自局を中心に、直近 6 局を 0.5-100 km の対数スケールで描く(北が上)。

    w, h: 描く領域の大きさ。small=True は常時表示の小さな地図(見出しは 'MAP' だけ、名前は新しい
    3 局だけ、左端に区切り線)。
    """
    cx = w // 2 + (1 if small else 0)
    cy = h // 2 + (1 if small else 4)
    r_max = h // 2 - (4 if small else 8)
    for km, label in ((1, "1k"), (10, "10k"), (100, "100k")):
        r = int(geo.log_radius(km, r_max) + 0.5)
        fb.ellipse(cx, cy + dy, r, r, DARK)
        if not small or km > 1:
            fb.text(label, cx + 2, cy - r - 8 + dy if km < 100 else cy - r + 1 + dy, GRAY)
    # 見出し(左上)と北の矢印(右上の隅)。円と重ならないよう隅に置く
    if small:
        _small_frame(fb, dy, h, "MAP")
    else:
        fb.text("MAP log", 0, 0 + dy, CYAN)
        fb.text("0.5-100km", 0, ROW + dy, CYAN)
    _north(fb, dy, w)
    fb.hline(cx - 4, cy + dy, 9, RED)
    fb.vline(cx, cy - 4 + dy, 9, RED)
    if status.lat is None:
        fb.text("no own pos" if small else "own position unknown", 2, h - CH + dy, ORANGE)
        return
    n_names = 3 if small else len(stations.order)
    for i, st in enumerate(stations.for_map()):
        km = geo.distance_km(status.lat, status.lon, st.lat, st.lon)
        b = geo.bearing_deg(status.lat, status.lon, st.lat, st.lon)
        x, y = geo.map_xy(km, b, cx, cy, r_max)
        col = GRAY if st.stale(now_ms) else (YELLOW if i == 0 else GREEN)
        if km > 100:
            fb.ellipse(x, y + dy, 3, 3, col)  # 範囲外は外周に白抜きの印
        else:
            fb.ellipse(x, y + dy, 2, 2, col, True)
        if i >= n_names:
            continue
        name = _clip(st.call, 6)
        tx = x + 4 if x + 4 + len(name) * CH <= w else x - 4 - len(name) * CH
        fb.text(name, tx, y - 3 + dy, col)


def draw_sky(fb, dy, w, h, signals):
    """衛星の位置(スカイプロット)。中心が天頂、外周が地平線、円は仰角 30 度と 60 度(北が上)。

    系ごとの色で、使用中は塗りつぶし、捕捉中で未使用は枠、捕捉していない衛星は灰色の点。
    """
    cx = w // 2 + 1
    cy = h // 2 + 1
    r_max = h // 2 - 4
    for el in (0, 30, 60):
        r = r_max * (90 - el) // 90
        fb.ellipse(cx, cy + dy, r, r, DARK)
    _small_frame(fb, dy, h, "SKY")
    _north(fb, dy, w)
    n_used = 0
    for gnss, cno, used, elev, azim in signals:
        if elev is None or elev < 0:
            continue
        r = r_max * (90 - elev) / 90
        a = math.radians(azim)
        x = cx + int(r * math.sin(a) + 0.5)
        y = cy - int(r * math.cos(a) + 0.5) + dy
        col = SYSTEMS.get(gnss, ("?", GRAY))[1]
        if not cno:
            fb.fill_rect(x - 1, y - 1, 2, 2, GRAY)
        else:
            fb.ellipse(x, y, 2, 2, col, used)
            n_used += used
    fb.text("%d" % n_used, 4, h - CH + dy, GREEN)
