"""局(station.Station)を何日も動かすための手入れ: 古い ticks_ms の片付けと、BLE の起動し直し。

station から、最初に使うときに読み込む(起動の後)。dev.py run/exec(src をマウント)では、
起動中は読み込みのたびのコンパイルでヒープに余裕がなく、station.py が大きくなるだけで
起動が MemoryError になった(2026-11-22)。起動の後なら十分に空いている。
"""

import time

from core import ticks
from hw import watchdog

BLE_RETRY_MS = 60_000  # BLE がエラーで止まったら、この時間のあとに起動し直す(失敗のたびに倍)
BLE_RETRY_MAX_MS = 600_000  # 待ちの上限。起動し直してからこれだけ動き続けたら、待ちを最初に戻す


def sweep(st, now):
    """何日も動かしたときに、古い ticks_ms の時刻が一周して新しく見えないように片付ける
    (core.ticks。局のループから REPORT_MS ごと)。片付けないと、受信だけで約 6.2 日たったあと
    送信が何日も止まる(前回の送信が「ついさっき」に見える)"""
    st._tx_ms = ticks.cap(now, st._tx_ms)
    st._air_ms = ticks.cap(now, st._air_ms)
    st.beacon.sweep(now)
    st.stations.sweep(now)
    st.channel.sweep(now)
    st.gnss.rtc.sweep(now)
    st.sd.sweep(now)
    r = st._ble_retry
    if r is not None and r.ok_ms is not None and st.ble is not None:
        if time.ticks_diff(now, r.ok_ms) >= BLE_RETRY_MAX_MS:
            r.ok_ms = None  # 起動し直してからしばらく動いた: 待ちを最初に戻す
            r.wait_ms = BLE_RETRY_MS


class BleRetry:
    """エラーで止めた BLE を起動し直す時刻と、次の待ち"""

    def __init__(self):
        self.at = None  # 起動し直す ticks_ms / None = 予定なし
        self.wait_ms = BLE_RETRY_MS  # 次にエラーで止まったときの待ち
        self.ok_ms = None  # 起動し直した ticks_ms(待ちを最初に戻す判定)


def ble_failed(st, now):
    """BLE の処理でエラーが出た: BLE を止め、待ってから起動し直す(局は止めない)"""
    r = st._ble_retry
    if r is None:
        r = st._ble_retry = BleRetry()
    wait = r.wait_ms
    r.wait_ms = min(wait * 2, BLE_RETRY_MAX_MS)
    r.ok_ms = None
    print("[ble] stopped after an error, restart in %d s" % (wait // 1000))
    st._stop_ble()
    r.at = time.ticks_add(now, wait)


def restart_due(st, now):
    r = st._ble_retry
    return r is not None and r.at is not None and time.ticks_diff(now, r.at) >= 0


def restart_ble(st):
    """エラーで止めた BLE を起動し直す。BT の初期化はループの LOOP_MS を超えうるので、
    起動中のウォッチドッグの時間に張り直す(station.run() の起動と同じ)"""
    r = st._ble_retry
    r.at = None
    print("[ble] restarting")
    st.wdt.arm(watchdog.STARTUP_MS)
    st.wdt.feed()
    try:
        st.start_ble()
    finally:
        st.wdt.arm(watchdog.LOOP_MS)
        st.wdt.feed()
    if st.ble is None:  # 起動に失敗した: また待つ
        ble_failed(st, time.ticks_ms())
    else:
        r.ok_ms = time.ticks_ms()
