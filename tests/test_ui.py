"""ui.views の配置・自局の状態の行・詳細の行のテスト。PC と実機の両方で動く。

描画そのものは実機で目視する。
コールサインはすべて架空の例。
"""

from aprs.packet import parse_frame
from core.level import DB_MIN
from core.stations import STALE_S, Stations
from tnc.ax25 import Frame
from ui.views import (
    BLACK,
    CYAN,
    GRAY,
    GREEN,
    ORANGE,
    RED,
    YELLOW,
    Layout,
    Status,
    badge_color,
    ble_icon,
    detail_age,
    detail_lines,
    draw_signals,
    gnss_text,
    level_line,
    meter_y,
    sd_tag,
    sd_tag_pos,
    small_map_kind,
    status_lines,
)

TOKYO = (35.681236, 139.767125)


def full_status():
    st = Status()
    st.fix, st.sats, st.sats_view, st.hdop = 3, 21, 38, 0.66
    st.utc_s = 1792885713  # 2026-10-24 23:48:33 UTC
    st.lat, st.lon, st.grid = 35.5, 139.5, "PM95SM00"
    st.beacon_on, st.next_beacon_s = True, 599
    st.rx_count, st.tx_count = 123, 45
    st.callsign = "Q1TEST-7"
    return st


def test_layout_320x240():
    lay = Layout(320, 240)
    assert lay.wide and lay.list_w == 72
    # 上段: 一覧(見出し + 12 局)と詳細(30 文字 x 13 行)が同じ高さ
    assert lay.list_h == lay.detail_h == 122 and lay.list_rows == 12
    assert (lay.right_x, lay.right_w, lay.cols) == (75, 245, 30)  # 区切り線 2px + 隙間 1px
    # 下段: 左端から自局の状態(25 文字)、右下に正方形の地図
    assert (lay.status_x, lay.status_y, lay.status_cols) == (0, 123, 25)
    assert lay.map_w == lay.map_h == 117 and lay.map_x + lay.map_w == 320
    assert lay.status_x + lay.status_w + 3 == lay.map_x
    # 棒グラフは状態 5 行(最後が自局のコールサイン)の下
    assert lay.sats_y == 1 + 5 * 9 + 1 and lay.status_h - lay.sats_y >= 60


def test_layout_240x135_has_no_small_map():
    lay = Layout(240, 135)
    assert not lay.wide and lay.map_w == 0 and lay.status_w == lay.right_w
    assert lay.list_h == 135 and lay.status_rows >= 5


def test_gnss_text_aug():
    st = full_status()
    assert gnss_text(st, 25) == "3D 21/38 HDOP0.7"
    st.aug = ("QZSS",)
    assert gnss_text(st, 25) == "3D 21/38 HDOP0.7 QZSS"
    st.aug = ("QZSS", "SBAS", "SLAS")  # 収まらなければ HDOP を省く
    assert gnss_text(st, 25) == "3D 21/38 QZSS SBAS SLAS"
    assert gnss_text(st, 10) == "3D 21/38"


def test_status_lines_wide():
    st = full_status()
    lines = [s for s, _ in status_lines(st, 25, 13)]
    assert lines == ["3D 21/38 HDOP0.7",
                     "08:48:33 JST     PM95SM00",
                     "N35 30.00 E139 30.00",
                     "BCN ON 9:59  RX 123 TX 45",
                     "Q1TEST-7"]
    assert all(len(s) <= 25 for s in lines)
    assert len(lines[2]) + len(" [RX]") <= 25  # 送受信中の印と重ならない
    st = Status()
    lines = [s for s, _ in status_lines(st, 25, 13)]
    assert lines[1] == "--:--:-- JST     --------" and lines[3].startswith("BCN OFF")
    # 手動のモード: スイッチがオンなら BCN MAN(次回の秒は出さない)、オフなら BCN OFF
    st = full_status()
    st.beacon_mode = "manual"
    assert [s for s, _ in status_lines(st, 25, 13)][3] == "BCN MAN      RX 123 TX 45"
    st.beacon_on = False
    assert [s for s, _ in status_lines(st, 25, 13)][3].startswith("BCN OFF")
    # 受信数が 4 桁になっても送信数を消さない(空白を詰める)
    st = full_status()
    st.rx_count, st.tx_count = 1234, 56
    assert [s for s, _ in status_lines(st, 24, 13)][3] == "BCN ON 9:59  RX1234 TX56"
    st.next_beacon_s = 7199  # 停止中の間隔の上限(2 時間)
    assert [s for s, _ in status_lines(st, 24, 13)][3] == "BCN ON 119:59  R1234 T56"
    st.rx_count, st.tx_count = 123456, 789
    assert [s for s, _ in status_lines(st, 24, 13)][3] == "BCN ON 119:59 123456/789"


def test_status_lines_compact():
    st = Status()
    lay = Layout(240, 135)
    lines = [s for s, _ in status_lines(st, lay.status_cols, lay.status_rows)]
    assert lay.status_cols == 20
    assert lines == ["NO FIX 0/0  --:--:--", "position unknown", "BCN OFF", "RX 0  TX 0",
                     "GRID --------"]
    st.callsign = "Q1TEST-15"  # 自局のコールサイン-SSID と Grid を 1 行に
    lines = [s for s, _ in status_lines(st, lay.status_cols, lay.status_rows)]
    assert lines[4] == "Q1TEST-15   --------" and len(lines[4]) <= lay.status_cols


def test_detail_distance_line():
    s = Stations()
    s.update(parse_frame(Frame.parse("JA1AAA>APRS:!3539.53N/13944.70E>test")), 1000)
    st = s.latest()
    lines = [t for t, _ in detail_lines(st, TOKYO[0], TOKYO[1], 1000, 30)]
    assert lines[1] == "DIST 3.2km  DIR SW"  # 自局からの距離と 16 方位はコールサインのすぐ下
    lines = [t for t, _ in detail_lines(st, None, None, 1000, 30)]
    assert lines[1] == "own position unknown"
    # 右上の丸を避けるとき(1・2 行目の幅を詰める)。経過時間は行に含めない(別の枠に描く)
    lines = [t for t, _ in detail_lines(st, TOKYO[0], TOKYO[1], 1000, 30, 27)]
    assert lines[0] == "JA1AAA" and lines[1] == "DIST 3.2km  DIR SW" and len(lines[2]) <= 30


def test_badge_fits_next_to_big_callsign():
    from ui.views import BIG, BIG_ROW, CH, ROW

    lay = Layout(320, 240)
    assert (lay.icon, lay.badge) == (24, 28)
    # 大きなコールサイン 9 文字 + 1px + バッジが右上の欄に収まり、2 行目はバッジの下
    assert 9 * CH * BIG + 1 + lay.badge <= lay.right_w
    assert BIG_ROW > lay.badge
    assert (lay.detail_h - 1 - BIG_ROW) // ROW == 10  # 大きな文字の下の行数は前と同じ
    small = Layout(240, 135)
    assert (small.icon, small.badge) == (20, 24) and 3 * ROW >= small.badge  # 1〜3 行目が避ける


def test_detail_head_lines_avoid_badge():
    s = Stations()
    s.update(parse_frame(Frame.parse("JA1AAA-15>APRS,JA1DIG*:!3539.53N/13944.70E>test")), 1000)
    lines = [t for t, _ in detail_lines(s.latest(), TOKYO[0], TOKYO[1], 13_000, 20, 17)]
    assert all(len(t) <= 17 for t in lines[:3]) and lines[2].startswith("Position")


def test_ble_icon_states():
    from ui import ble_icons

    assert ble_icon(None, 24) is None  # BLE を使わない: 出さない
    assert ble_icon("auth", 24) is ble_icons.CONN_24 and ble_icon("auth", 20) is ble_icons.CONN_20
    # 広告中と認証前は広告のアニメーション(コマが回る)
    adv = [ble_icon("adv", 24, f) for f in range(5)]
    assert adv[:4] == list(ble_icons.ADV_24) and adv[4] is adv[0]
    assert ble_icon("conn", 24, 2) is ble_icons.ADV_24[2]
    assert len(set(adv[:4])) == 4
    assert ble_icon("adv", 20, 3) is ble_icons.ADV_20[0]


def test_ble_icons_data():
    from ui import ble_icons

    for n, frames, conn in ((24, ble_icons.ADV_24, ble_icons.CONN_24),
                            (20, ble_icons.ADV_20, ble_icons.CONN_20)):
        assert all(len(d) == 2 * n * n for d in frames + (conn,))
    # framebuf の並び(上位バイトが先)で読むと、広告中のアイコンは青が強い(並びが逆なら崩れる)
    d = ble_icons.ADV_24[3]
    lit = blue = 0
    for i in range(0, len(d), 2):
        v = (d[i] << 8) | d[i + 1]
        r, b = (v >> 11) << 3, (v & 31) << 3
        if v:
            lit += 1
            blue += b > r
    assert lit > 100 and blue * 10 >= lit * 9


def test_badge_color():
    st = Status()
    assert badge_color(st) == RED
    st.fix = 2
    assert badge_color(st) == ORANGE
    st.fix = 3
    assert badge_color(st) == GREEN
    st.aug = ("QZSS",)  # QZSS だけは補正ではないので緑のまま
    assert badge_color(st) == GREEN
    st.aug = ("QZSS", "SLAS")
    assert badge_color(st) == CYAN and CYAN != BLACK


def test_small_map_kind():
    s = Stations()
    kinds = lambda now: "".join(small_map_kind(s, now, t)[0] for t in range(30))  # noqa: E731
    assert kinds(0) == "s" * 30  # 局が無ければ常に SKY
    s.update(parse_frame(Frame.parse("JA1AAA>APRS:!3539.53N/13944.70E>test")), 0)
    assert kinds(1000) == ("m" * 10 + "s" * 5) * 2  # 古くない局がある: MAP 10 秒・SKY 5 秒
    old = STALE_S * 1000
    assert kinds(old) == ("m" * 5 + "s" * 5) * 3  # 古い局だけ: 5 秒ずつ
    s.update(parse_frame(Frame.parse("JA1BBB>APRS:>status")), old)
    assert kinds(old) == ("m" * 10 + "s" * 5) * 2


def test_sd_tag():
    assert sd_tag(None) == ("SD:---", GRAY) and sd_tag("no card") == ("SD:---", GRAY)
    assert sd_tag("wait") == ("SD:WAIT", YELLOW)
    assert sd_tag("log 0003") == ("SD:LOG", GREEN)
    assert sd_tag("error") == ("SD:ERR", RED)
    # 320x240: 時刻の後ろ(JST を外す)。'08:48:33 SD:WAIT PM95SM00' が 25 文字に収まる
    st = full_status()
    st.sd = "wait"
    lay = Layout(320, 240)
    line = status_lines(st, lay.status_cols, lay.status_rows)[1][0]
    pos = sd_tag_pos(lay)
    assert pos is not None
    col, row = pos
    assert row == 1 and line == "08:48:33" + " " * 9 + "PM95SM00"
    assert line[col - 1] == " " and line[col:col + 7] == " " * 7  # SD の表示を置く 7 文字は空き
    lay = Layout(240, 135)
    pos = sd_tag_pos(lay)
    assert pos is not None
    col, row = pos
    st.next_beacon_s = 59 * 60 + 59
    line = status_lines(st, lay.status_cols, lay.status_rows)[row][0]
    assert line == "BCN ON 59:59" and len(line) < col  # ビーコンの行の右の空き


def test_detail_big_callsign():
    s = Stations()
    s.update(parse_frame(Frame.parse("JA1AAA-15>APRS:!3539.53N/13944.70E>test")), 1000)
    st = s.latest()
    lines = [t for t, _ in detail_lines(st, TOKYO[0], TOKYO[1], 13_000, 30, 27, 9)]
    assert lines[0] == "JA1AAA-15"  # 1 行目はコールサインだけ(大きな文字)
    assert lines[1] == "DIST 3.2km  DIR SW"  # 経過時間は別の枠(2 行目の右端)
    lines = [t for t, _ in detail_lines(st, None, None, 13_000, 30, 27, 9)]
    assert lines[1] == "own position unknown"
    assert detail_age(st, TOKYO[0], 13_000, True) == ("12s", GREEN)  # 距離の行の色
    assert detail_age(st, None, 13_000, True) == ("12s", GRAY)
    assert detail_age(st, None, 13_000, False) == ("12s", YELLOW)  # コールサインの色
    assert detail_age(None, None, 13_000, True) is None


def test_detail_age_cell_does_not_overlap_text():
    from ui.views import AGE_COLS, CH

    for w, h in ((320, 240), (240, 135)):
        lay = Layout(w, h)
        assert lay.age_w == AGE_COLS * CH
        if lay.wide:  # 2 行目の右端。距離の行(cols - AGE_COLS - 1 文字まで)の右
            assert lay.age_x + lay.age_w <= lay.right_w and lay.age_y >= lay.badge
            assert lay.age_x >= (lay.cols - AGE_COLS - 1) * CH + CH
        else:  # 1 行目のバッジの左。コールサイン(head_cols - AGE_COLS - 1 文字まで)の右
            assert lay.age_x + lay.age_w <= lay.right_w - lay.badge and lay.age_y == 0
            assert lay.age_x >= (lay.head_cols - AGE_COLS - 1) * CH + CH


class FakeFb:
    """描画の呼び出しのうち、文字と棒の位置だけを記録する"""

    def __init__(self):
        self.texts = []
        self.bars = []

    def text(self, s, x, y, col):
        self.texts.append((s, x))

    def hline(self, x, y, w, col):
        pass

    def fill_rect(self, x, y, w, h, col):
        self.bars.append((x, w))

    def rect(self, x, y, w, h, col):
        self.bars.append((x, w))


def _signals(counts):
    # counts: [(gnssId, 機数), ...] -> Status.signals の形
    return [(g, 30, True, 45, 0) for g, n in counts for _ in range(n)]


def test_draw_signals_labels_single_satellite_systems():
    # 1 機だけの系が続いても、全ての系の記号が重ならずに出る
    fb = FakeFb()
    draw_signals(fb, 0, 200, 60, _signals([(0, 8), (1, 1), (2, 1), (5, 1), (6, 3)]))
    assert [s for s, _ in fb.texts] == ["G", "S", "E", "J", "R"]
    xs = [x for _, x in fb.texts]
    assert all(b - a >= 9 for a, b in zip(xs, xs[1:]))
    assert len(fb.bars) == 14
    assert all(x + w <= 200 for x, w in fb.bars)


def test_draw_signals_many_satellites_fit():
    # 機数が多いときは棒を細くして幅に収め、記号は全て出す
    fb = FakeFb()
    draw_signals(fb, 0, 200, 60, _signals([(0, 12), (1, 2), (2, 10), (3, 14), (5, 1), (6, 9)]))
    assert [s for s, _ in fb.texts] == ["G", "S", "E", "C", "J", "R"]
    assert all(x + w <= 200 for x, w in fb.bars)


def test_draw_signals_ignores_untracked():
    fb = FakeFb()
    draw_signals(fb, 0, 200, 60, [(0, 0, False, None, 0)])
    assert fb.texts == [("no satellites tracked", 0)]


def test_layout_with_level_meter():
    plain = Layout(320, 240)
    lay = Layout(320, 240, meter=True)
    assert plain.meter_w == 0 and lay.meter_w == 7
    # 縦棒は状態の欄と地図の区切り線の間。状態の欄は 24 文字になる
    assert lay.status_w == plain.status_w - 8 and lay.status_cols == 24
    # 縦棒の右に 1px の隙間、その右に地図の区切り線 2px
    assert lay.meter_x == lay.status_w + 1 and lay.meter_x + lay.meter_w + 3 == lay.map_x
    st = full_status()
    st.sd = "log 0001"
    lines = [s for s, _ in status_lines(st, lay.status_cols, lay.status_rows)]
    assert all(len(s) <= 24 for s in lines)
    assert lines[3] == "BCN ON 9:59 RX 123 TX 45"
    assert not Layout(240, 135, meter=True).meter_w  # 240x135 には出さない


def test_meter_scale():
    assert meter_y(0, 117) == 0 and meter_y(DB_MIN, 117) == 116
    assert meter_y(5, 117) == 0 and meter_y(-99, 117) == 116  # 範囲の外は端に張り付く
    assert meter_y(-20, 117) == 58


def test_detail_level_line():
    assert level_line(-11.6, False) == ("LVL -12dB", GREEN)
    assert level_line(-12.4, False) == ("LVL -12dB", ORANGE)  # 適正範囲(-12 dB 以上)より低い
    assert level_line(-25.0, False) == ("LVL -25dB", ORANGE)
    assert level_line(-1.0, False) == ("LVL -1dB", YELLOW)
    assert level_line(-0.2, True) == ("LVL 0dB CLIP", RED)
    s = Stations()
    s.update(parse_frame(Frame.parse("JA1AAA>APRS,JA1DIG*:!3539.53N/13944.70E>test")), 1000,
             -9.6, False)
    lines = detail_lines(s.latest(), TOKYO[0], TOKYO[1], 1000, 30)
    assert ("LVL -10dB", GREEN) in lines
    s.update(parse_frame(Frame.parse("JA1AAA>APRS:>no level")), 2000)  # IF 入力などでは出さない
    assert not any(t.startswith("LVL") for t, _ in detail_lines(s.latest(), None, None, 2000, 30))


def test_hw_draw_passkey_fits_and_shows_digits():
    import framebuf

    from ui.views import BIG, CH, draw_passkey

    assert 6 * CH * BIG <= 240  # 6 桁が幅に収まる(320 幅も同じ)
    w, h, band = 240, 135, 27
    buf = bytearray(2 * w * band)

    def render(pk):
        """実際の画面(ui.screen)と同じく帯に分けて描き、帯ごとの合計を並べる"""
        sums = []
        for top in range(0, h, band):
            fb = framebuf.FrameBuffer(buf, w, band, framebuf.RGB565)
            fb.fill(0)
            draw_passkey(fb, -top, w, h, pk)
            sums.append(sum(buf))
        return sums

    a = render(123456)
    assert any(a)  # 何か描いた
    assert render(7) != a  # 先頭の 0 を埋めて 6 桁にしているので、別の絵になる


def test_hw_ui_switches_to_passkey_and_back():
    from ui.controller import Ui

    class Scr:
        width, height = 320, 240

        def __init__(self):
            self.regions = []

        def region(self, x, y, w, h, draw, bg=0):
            self.regions.append((x, y, w, h))

        def fill(self, color):
            self.regions.append("fill")

    scr = Scr()
    st = Status()
    ui = Ui(scr, Stations(), st, ())
    ui.poll(1000)
    n = len(scr.regions)
    assert n > 1  # 通常の画面
    st.passkey = 123456
    ui.poll(1100)
    assert scr.regions[n:] == [(0, 0, 320, 240)]  # 全画面で 1 回だけ
    ui.poll(2100)
    ui.poll(3100)
    assert len(scr.regions) == n + 1  # 出している間は描き直さない
    st.passkey = None
    ui.poll(4100)
    assert len(scr.regions) > n + 1  # 通常の画面に戻る(全体を描き直す)


def test_hw_draw_badge_frame_and_icon():
    import framebuf

    from ui.views import draw_badge

    lay = Layout(320, 240)
    n = lay.badge
    buf = bytearray(2 * n * n)
    fb = framebuf.FrameBuffer(buf, n, n, framebuf.RGB565)

    def render(ble, on, frame=0):
        fb.fill(0)
        st = Status()
        st.fix, st.ble = 3, ble
        draw_badge(fb, 0, 0, lay, st, on, frame)
        return bytes(buf)

    assert not any(render(None, False))  # BLE なしで消灯: 何も描かない
    frame_only = render(None, True)
    assert fb.pixel(2, 0) == GREEN and fb.pixel(0, 0) == 0  # 枠は測位状態の色、角は欠く
    assert fb.pixel(n // 2, n // 2) == 0  # 中は空
    icon_only = render("auth", False)
    assert any(icon_only) and fb.pixel(2, 0) == 0  # 消灯中もアイコンは出す
    assert render("adv", True, 0) != render("adv", True, 1) != frame_only  # コマ送り


def test_hw_ui_redraws_badge_for_ble():
    from ui.controller import ADV_FRAME_MS, Ui

    class Scr:
        width, height = 320, 240

        def __init__(self):
            self.regions = []

        def region(self, x, y, w, h, draw, bg=0):
            self.regions.append((x, y, w, h))

        def fill(self, color):
            pass

    scr = Scr()
    st = Status()
    ui = Ui(scr, Stations(), st, ())
    ui.poll(1000)
    badge = (320 - 28, 0, 28, 28)
    del scr.regions[:]
    ui.poll(1010)
    assert scr.regions == []  # 変化なし
    st.ble = "adv"
    ui.poll(1020)
    assert scr.regions == [badge]  # 状態が変わった: バッジだけ
    del scr.regions[:]
    t = (1020 // ADV_FRAME_MS + 1) * ADV_FRAME_MS  # 次のコマ(点滅の切り替えとは別の時刻)
    if t % 500 == 0:
        t += ADV_FRAME_MS
    ui.poll(t)
    assert badge in scr.regions


def test_hw_ui_tick_redraws_only_age_and_badge():
    from ui.controller import Ui

    class Scr:
        width, height = 320, 240

        def __init__(self):
            self.regions = []

        def region(self, x, y, w, h, draw, bg=0):
            self.regions.append((x, y, w, h))

        def fill(self, color):
            pass

    scr = Scr()
    st = Status()
    st.lat, st.lon = TOKYO
    s = Stations()
    s.update(parse_frame(Frame.parse("JA1AAA>APRS:!3539.53N/13944.70E>test")), 1000)
    ui = Ui(scr, s, st, ())
    lay = ui.lay
    detail = (lay.right_x, 0, lay.right_w, lay.detail_h)
    age = (lay.right_x + lay.age_x, lay.age_y, lay.age_w, 8)
    badge = (320 - 28, 0, 28, 28)
    ui.poll(1000)
    assert detail in scr.regions
    del scr.regions[:]
    ui.poll(2000)  # 1 秒たった: 経過時間とバッジだけ(と自局の状態)
    assert age in scr.regions and badge in scr.regions and detail not in scr.regions
    del scr.regions[:]
    st.lat = TOKYO[0] + 0.1  # 自局が動いて距離が変わった: 詳細欄全体
    ui.poll(3000)
    assert detail in scr.regions and age not in scr.regions


# ---- APRS メッセージ(未読の印と全画面の通知)
def test_msg_tag_in_status_lines():
    from ui.views import message_lines, msg_tag

    st = full_status()
    assert msg_tag(st) == ""
    st.msg_pending = 1
    assert msg_tag(st) == "MSG>"
    st.msg_unread = 3
    lines = [s for s, _ in status_lines(st, 25, 13)]
    assert lines[4] == "Q1TEST-7            MSG 3" and len(lines[4]) <= 25
    lay = Layout(240, 135)
    lines = [s for s, _ in status_lines(st, lay.status_cols, lay.status_rows)]
    assert lines[4] == "Q1TEST-7       MSG 3" and len(lines[4]) <= lay.status_cols
    ml = message_lines("Q1AAA-9", "x" * 67, 40, 25)
    assert ml[0] == ("MSG from Q1AAA-9", CYAN) and [s for s, _ in ml[2:]] == ["x" * 40, "x" * 27]


def test_hw_ui_shows_message_then_returns_and_passkey_wins():
    from ui.controller import Ui

    class Scr:
        width, height = 320, 240

        def __init__(self):
            self.regions = []

        def region(self, x, y, w, h, draw, bg=0):
            self.regions.append((x, y, w, h))

        def fill(self, color):
            self.regions.append("fill")

    scr = Scr()
    st = Status()
    ui = Ui(scr, Stations(), st, ())
    ui.poll(1000)
    n = len(scr.regions)
    st.message, st.message_until = ("Q1AAA", "hello"), 31_000
    ui.poll(1100)
    assert scr.regions[n:] == [(0, 0, 320, 240)]
    ui.draw_status_now()  # 送信の [TX] も、通知の上には描かない
    ui.poll(2100)
    assert len(scr.regions) == n + 1
    st.passkey = 1  # パスキーが優先
    ui.poll(3000)
    st.passkey = None
    ui.poll(4000)  # パスキーのあとにメッセージを出し直す
    assert scr.regions[-1] == (0, 0, 320, 240) and st.message is not None
    k = len(scr.regions)
    ui.poll(31_000)  # 時間切れ: 通常の画面
    assert st.message is None and len(scr.regions) > k + 1
