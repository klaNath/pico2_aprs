"""起動直後に PTT(GP2)を Low にする(main.py より先に走る)。

リセットの種類によっては、リセット前の出力が残ったり、ピンが浮いたりすることがあるため、
最初に送信しない側へ確定する(docs/tx-time-limit.md §4)。設定を読めなくても止まらないように、
読めないときは配線メモのとおり GP2 を Low にする。
ソフトリセットの時点でウォッチドッグが動いていれば(mpremote の接続のたびに起きる)、
フィードを引き継ぐ(wdt_bridge)。
ここで import したモジュールは(wdt_bridge 以外)キャッシュから消す。残すと、dev.py の run/exec
(PC の src をマウント)が、PC の config.py ではなく ROMFS の古い config を使ってしまう。
"""

import sys

from machine import Pin

_before = set(sys.modules)
try:
    import config

    Pin(config.PIN_PTT, Pin.OUT, value=0 if config.PTT_ACTIVE_HIGH else 1)
except Exception:
    Pin(2, Pin.OUT, value=0)
for _name in [_n for _n in sys.modules if _n not in _before]:
    del sys.modules[_name]

try:
    import wdt_bridge

    wdt_bridge.start()
except Exception:
    pass
