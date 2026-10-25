"""ui.views の配置と自局の状態の行のテスト(描画そのものは実機で目視)。PC と実機の両方で動く。"""

from ui.views import Layout, Status, status_lines


def test_layout_320x240():
    lay = Layout(320, 240)
    assert lay.wide and lay.list_w == 72 and lay.list_rows == 25
    assert (lay.right_x, lay.right_w, lay.cols, lay.detail_h) == (74, 246, 30, 122)
    assert (lay.status_w, lay.status_cols, lay.status_rows) == (128, 16, 13)
    # 地図は状態欄の右、画面の右下の角まで。ほぼ正方形
    assert lay.map_x == lay.right_x + lay.status_w + 2
    assert lay.map_x + lay.map_w == 320 and lay.map_y + lay.map_h == 240
    assert abs(lay.map_w - lay.map_h) <= 2


def test_layout_240x135_has_no_small_map():
    lay = Layout(240, 135)
    assert not lay.wide and lay.map_w == 0 and lay.status_w == lay.right_w
    assert lay.status_rows >= 4


def test_status_lines_tall():
    st = Status()
    st.fix, st.sats, st.sats_view, st.hdop = 3, 21, 38, 0.66
    st.utc_s = 1792885713
    st.lat, st.lon = 35.5, 139.5
    st.beacon_on, st.next_beacon_s = True, 599
    st.rx_count, st.tx_count = 123, 45
    lines = [s for s, _ in status_lines(st, 16, 13)]
    assert lines == ["3D 21/38 HDOP0.7", "08:48:33 JST", "N35 30.00", "E139 30.00",
                     "BCN ON next 9:59", "RX 123 TX 45"]
    assert all(len(s) <= 16 for s in lines)
    # 右端の [RX]/[TX] と重ならない
    assert len(lines[-1]) + len("[RX]") <= 16


def test_status_lines_compact():
    st = Status()
    lay = Layout(240, 135)
    lines = [s for s, _ in status_lines(st, lay.status_cols, lay.status_rows)]
    assert lay.status_cols == 20
    assert lines[0] == "NO FIX 0/0  --:--:--"
    assert lines[1:] == ["position unknown", "BCN OFF", "RX 0  TX 0"]
