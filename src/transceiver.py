"""Si5351B を使うフル機能のトランシーバーモード(config.MODE = "si5351")。未完成。

実際には何も動かさない: ディスプレイに未完成と表示して待つだけ。
    受信・送信・GNSS・ビーコン・SD の記録は動かさない(Si5351 にも I2C にも触れない)
    PTT(GP2)は切った状態にする
    ウォッチドッグは(有効なら)フィードして、再起動を繰り返さないようにする
外付け TNC として使うには config.MODE = "tnc"(既定)にする。
実装するときは、ここに Si5351 のドライバ(hw.si5351)と周波数のゲート(hw.txctl.si5351_gate)を
組み込む。455 kHz FM IF の受信(hw.fm_if_rx)も、このモードで使う予定。
"""

import sys
import time

from hw import txctl, watchdog
from ui import notice
from ui.views import ORANGE

LOOP_MS = 100
LINES = (
    "Si5351 MODE",
    "",
    "NOT COMPLETE",
    "",
    "This mode is under development.",
    "It does not operate: no RX, no TX,",
    "no GNSS, no beacon. PTT is off.",
    "",
    'Set config.MODE = "tnc" to use',
    "the external TNC mode.",
)


def run(duration_ms=None, wdt=None):
    """未完成の表示を出して待つ。duration_ms を渡すとその時間で戻る(試験用)。
    wdt: station.main が起動中の設定で有効にした hw.watchdog.Watchdog(station.run と同じ引き継ぎ)"""
    txctl.ptt_off()
    print("[station] MODE si5351 started, NOT COMPLETE: nothing runs, PTT off")
    try:
        notice.show(LINES, ORANGE)
    except Exception as e:  # 表示できなくても、何も動かさないことは変わらない
        sys.print_exception(e)
    if wdt is None:
        wdt = watchdog.Watchdog(enabled=False)
    wdt.arm(watchdog.LOOP_MS)
    t0 = time.ticks_ms()
    while duration_ms is None or time.ticks_diff(time.ticks_ms(), t0) < duration_ms:
        wdt.feed()
        time.sleep_ms(LOOP_MS)
    print("[station] stopped")
