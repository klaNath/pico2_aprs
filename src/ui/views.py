"""画面の描画: メイン画面(局の一覧・最新局の詳細・自局の状態)と地図画面。

配置は画面の大きさから決める(240x135 と、将来の 320x240 の両方で使える)。
文字は framebuf 標準の 8x8 フォント(ASCII のみ)。描画関数は ui.screen.Screen.region に渡す
draw(fb, dy) の形で、領域の左上を (0, 0) とした座標に dy を足して描く。
"""

from core import geo
from hw.st7789 import rgb

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

CH = 8  # 文字の幅・高さ
ROW = 9  # 行の高さ(1 ピクセルの行間)

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
        self.sats = 0
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
        self.list_w = (9 if width <= 240 else 12) * CH  # コールサイン 9 文字 + 余白
        self.right_x = self.list_w + 2
        self.right_w = width - self.right_x
        self.detail_h = (height * 64) // 100  # 上 64% を詳細、残りを自局の状態
        self.status_y = self.detail_h + 1
        self.status_h = height - self.status_y
        self.list_rows = (height - ROW) // ROW  # 見出し 1 行を除く
        self.cols = self.right_w // CH


def _clip(s, n):
    return s if len(s) <= n else s[:n]


def _right(fb, s, x_right, y, color):
    fb.text(s, x_right - len(s) * CH, y, color)


def draw_list(fb, dy, lay, stations, selected, now_ms):
    """左: コールサインの一覧(新しい順)。selected は選択中の Station(None = 最新を自動表示)"""
    fb.text("HEARD %d" % len(stations), 0, 0 + dy, CYAN)
    fb.vline(lay.list_w, dy, lay.h, DARK)
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


def detail_lines(st, own_lat, own_lon, now_ms, cols):
    """最新局(または選択局)の詳細 -> [(文字列, 色), ...]。テストしやすいよう描画と分けてある。"""
    if st is None:
        return [("no station heard", GRAY)]
    p = st.packet
    lines = []
    age = geo.fmt_age(st.age_s(now_ms))
    head = _clip(st.call, cols - len(age) - 1)
    lines.append((head + " " * (cols - len(head) - len(age)) + age,
                  MAGENTA if st.own else GRAY if st.stale(now_ms) else YELLOW))
    kind = KIND_LABEL.get(p.kind, p.kind)
    if p.kind == "mice" and p.mice_msg:
        kind += " " + p.mice_msg
    if p.table and p.code:
        kind += " " + p.table + p.code
    lines.append((_clip(kind, cols), CYAN))
    if st.lat is not None and own_lat is not None:
        d = geo.distance_km(own_lat, own_lon, st.lat, st.lon)
        b = geo.compass16(geo.bearing_deg(own_lat, own_lon, st.lat, st.lon))
        lines.append((geo.fmt_distance(d) + " " + b, GREEN))
    elif st.lat is not None:
        lines.append(("own position unknown", GRAY))
    else:
        lines.append(("no position", GRAY))
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


def draw_detail(fb, dy, lay, st, status, now_ms):
    lines = detail_lines(st, status.lat, status.lon, now_ms, lay.cols)
    max_lines = (lay.detail_h + 1) // ROW
    for i, (s, c) in enumerate(lines[:max_lines]):
        fb.text(s, 0, i * ROW + dy, c)
    fb.hline(0, lay.detail_h - 1 + dy, lay.right_w, DARK)


def status_lines(status, cols):
    lines = []
    gps = "GPS NO FIX" if status.fix < 2 else "GPS %dD %dsat" % (status.fix, status.sats)
    t = geo.fmt_jst(status.utc_s) if status.utc_s is not None else "--:--:--"
    lines.append((gps + " " * max(1, cols - len(gps) - len(t)) + t,
                  GREEN if status.fix >= 2 else ORANGE))
    if status.lat is not None:
        lines.append((geo.fmt_latlon(status.lat, status.lon), WHITE))
    else:
        lines.append(("position unknown", GRAY))
    if status.beacon_on:
        nb = status.next_beacon_s
        nxt = "" if nb is None else "  next %d:%02d" % (nb // 60, nb % 60)
        lines.append(("BCN ON" + nxt, GREEN))
    else:
        lines.append(("BCN OFF", GRAY))
    lines.append(("RX %d  TX %d" % (status.rx_count, status.tx_count), WHITE))
    return lines


def draw_status(fb, dy, lay, status):
    lines = status_lines(status, lay.cols)
    for i, (s, c) in enumerate(lines):
        fb.text(s, 0, 1 + i * ROW + dy, c)
    y = 1 + (len(lines) - 1) * ROW + dy
    if status.tx_active:
        _right(fb, "[TX]", lay.right_w, y, RED)
    elif status.rx_recent:
        _right(fb, "[RX]", lay.right_w, y, GREEN)


def draw_map(fb, dy, lay, stations, status, now_ms):
    """自局を中心に、直近 6 局を 0.5-100 km の対数スケールで描く(北が上)。"""
    cx = lay.w // 2
    cy = lay.h // 2 + 4
    r_max = lay.h // 2 - 8
    for km, label in ((1, "1k"), (10, "10k"), (100, "100k")):
        r = int(geo.log_radius(km, r_max) + 0.5)
        fb.ellipse(cx, cy + dy, r, r, DARK)
        fb.text(label, cx + 2, cy - r - 8 + dy if km < 100 else cy - r + 1 + dy, GRAY)
    # 見出し(左上 2 行)と北の矢印(右上の隅)。円と重ならないよう隅に置く
    fb.text("MAP log", 0, 0 + dy, CYAN)
    fb.text("0.5-100km", 0, ROW + dy, CYAN)
    ax = lay.w - 8
    fb.text("N", ax - 3, 0 + dy, CYAN)
    fb.vline(ax, ROW + dy, 14, CYAN)
    fb.line(ax, ROW + dy, ax - 4, ROW + 5 + dy, CYAN)
    fb.line(ax, ROW + dy, ax + 4, ROW + 5 + dy, CYAN)
    fb.hline(cx - 4, cy + dy, 9, RED)
    fb.vline(cx, cy - 4 + dy, 9, RED)
    if status.lat is None:
        fb.text("own position unknown", 0, lay.h - CH + dy, ORANGE)
        return
    for i, st in enumerate(stations.for_map()):
        km = geo.distance_km(status.lat, status.lon, st.lat, st.lon)
        b = geo.bearing_deg(status.lat, status.lon, st.lat, st.lon)
        x, y = geo.map_xy(km, b, cx, cy, r_max)
        col = GRAY if st.stale(now_ms) else (YELLOW if i == 0 else GREEN)
        if km > 100:
            fb.ellipse(x, y + dy, 3, 3, col)  # 範囲外は外周に白抜きの印
        else:
            fb.ellipse(x, y + dy, 2, 2, col, True)
        name = _clip(st.call, 6)
        tx = x + 4 if x + 4 + len(name) * CH <= lay.w else x - 4 - len(name) * CH
        fb.text(name, tx, y - 3 + dy, col)
