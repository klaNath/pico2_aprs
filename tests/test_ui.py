"""ui.views の配置・自局の状態の行・詳細の行のテスト。PC と実機の両方で動く。

描画そのものは実機で目視する。
"""

from aprs.packet import parse_frame
from core.stations import Stations
from tnc.ax25 import Frame
from ui.views import Layout, Status, detail_lines, gnss_text, status_lines

TOKYO = (35.681236, 139.767125)


def full_status():
    st = Status()
    st.fix, st.sats, st.sats_view, st.hdop = 3, 21, 38, 0.66
    st.utc_s = 1792885713  # 2026-10-24 23:48:33 UTC
    st.lat, st.lon, st.grid = 35.5, 139.5, "PM95SM00"
    st.beacon_on, st.next_beacon_s = True, 599
    st.rx_count, st.tx_count = 123, 45
    return st


def test_layout_320x240():
    lay = Layout(320, 240)
    assert lay.wide and lay.list_w == 72
    # 上段: 一覧(見出し + 12 局)と詳細(30 文字 x 13 行)が同じ高さ
    assert lay.list_h == lay.detail_h == 122 and lay.list_rows == 12
    assert (lay.right_x, lay.right_w, lay.cols) == (74, 246, 30)
    # 下段: 左端から自局の状態(25 文字)、右下に正方形の地図
    assert (lay.status_x, lay.status_y, lay.status_cols) == (0, 123, 25)
    assert lay.map_w == lay.map_h == 117 and lay.map_x + lay.map_w == 320
    assert lay.status_x + lay.status_w < lay.map_x
    # 棒グラフは状態 4 行の下
    assert lay.sats_y == 1 + 4 * 9 + 1 and lay.status_h - lay.sats_y >= 70


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
                     "BCN ON 9:59  RX 123 TX 45"]
    assert all(len(s) <= 25 for s in lines)
    assert len(lines[2]) + len(" [RX]") <= 25  # 送受信中の印と重ならない
    st = Status()
    lines = [s for s, _ in status_lines(st, 25, 13)]
    assert lines[1] == "--:--:-- JST     --------" and lines[3].startswith("BCN OFF")


def test_status_lines_compact():
    st = Status()
    lay = Layout(240, 135)
    lines = [s for s, _ in status_lines(st, lay.status_cols, lay.status_rows)]
    assert lay.status_cols == 20
    assert lines == ["NO FIX 0/0  --:--:--", "position unknown", "BCN OFF", "RX 0  TX 0",
                     "GRID --------"]


def test_detail_distance_line():
    s = Stations()
    s.update(parse_frame(Frame.parse("JA1AAA>APRS:!3539.53N/13944.70E>test")), 1000)
    st = s.latest()
    lines = [t for t, _ in detail_lines(st, TOKYO[0], TOKYO[1], 1000, 30)]
    assert lines[1] == "DIST 3.2km  DIR SW"  # 自局からの距離と 16 方位はコールサインのすぐ下
    lines = [t for t, _ in detail_lines(st, None, None, 1000, 30)]
    assert lines[1] == "own position unknown"
