"""boot.py 用: ソフトリセットの時点でウォッチドッグが動いていれば、タイマーでフィードを引き継ぐ。

mpremote は接続のたびにソフトリセットする。そのとき hold() のタイマーは消えるので、これがないと
書き込み済みの基板は、dev.py の操作から約 16 秒でリセットされる。局が起動して hw.watchdog.arm() を
呼ぶと release() で止める(状態をこのモジュールに置くのは、boot.py が import した同じモジュールを
局が参照できるようにするため。boot.py は hw パッケージを import しない: マウント中の dev.py が
PC の src/hw ではなく ROMFS の hw を読んでしまうため)。
"""

HOLD_MS = 16_000  # RP2350 の上限(約 16.7 秒)に近い。hw.watchdog.HOLD_MS と同じ
PERIOD_MS = 1_000
_CTRL = 0x400D8000  # WATCHDOG CTRL(bit30 = 有効)
_CTRL_ENABLE = 1 << 30
_timer = None


def start(running=None, wdt_class=None, timer_class=None):
    """動いていなければ何もしない(電源投入、ウォッチドッグのリセットの直後は止まっている)"""
    global _timer
    if running is None:
        import machine

        running = bool(machine.mem32[_CTRL] & _CTRL_ENABLE)
    if not running or _timer is not None:
        return None
    if wdt_class is None or timer_class is None:
        import machine

        wdt_class, timer_class = wdt_class or machine.WDT, timer_class or machine.Timer
    wdt = wdt_class(timeout=HOLD_MS)
    _timer = timer_class(-1)
    _timer.init(period=PERIOD_MS, mode=_timer.PERIODIC, callback=lambda _t: wdt.feed())
    return _timer


def release():
    global _timer
    if _timer is not None:
        _timer.deinit()
        _timer = None
