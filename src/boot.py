"""起動直後に PTT(GP2)を Low にする(main.py より先に走る)。

リセットの種類によっては、リセット前の出力が残ったり、ピンが浮いたりすることがあるため、
最初に送信しない側へ確定する(docs/tx-time-limit.md §4)。
ソフトリセットの時点でウォッチドッグが動いていれば(mpremote の接続のたびに起きる)、
フィードを引き継ぐ。設定を読めなくても止まらないように、
読めないときは配線メモのとおり GP2 を Low にする。
"""

from machine import Pin

try:
    import config

    Pin(config.PIN_PTT, Pin.OUT, value=0 if config.PTT_ACTIVE_HIGH else 1)
except Exception:
    Pin(2, Pin.OUT, value=0)

try:
    from hw import watchdog

    watchdog.bridge()
except Exception:
    pass
