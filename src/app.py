"""アプリ本体のサンプル: オンボード LED の点滅パターン。

ハードウェアへの依存(Pin)は外から渡す形にしてあるので、
テストでは偽の Pin を渡してロジックだけを検証できる。
"""

import time

from micropython import const

DEFAULT_PERIOD_MS = const(500)


class Blinker:
    """pin.value(0/1) を一定周期で切り替える。"""

    def __init__(self, pin, period_ms: int = DEFAULT_PERIOD_MS):
        if period_ms <= 0:
            raise ValueError("period_ms must be positive")
        self.pin = pin
        self.period_ms = period_ms
        self.state = 0
        self.toggles = 0
        self._last = time.ticks_ms()

    def update(self, now=None) -> bool:
        """周期が来ていれば LED を切り替えて True を返す。ループから頻繁に呼ぶ。"""
        if now is None:
            now = time.ticks_ms()
        if time.ticks_diff(now, self._last) >= self.period_ms:
            self._last = now
            self.state ^= 1
            self.pin.value(self.state)
            self.toggles += 1
            return True
        return False


def run(period_ms: int = DEFAULT_PERIOD_MS, duration_ms: int = 0) -> None:
    """LED を点滅させる。duration_ms=0 なら無限に続ける。"""
    from machine import Pin

    led = Pin("LED", Pin.OUT)
    blinker = Blinker(led, period_ms)
    start = time.ticks_ms()
    print("blink start: period", period_ms, "ms")
    try:
        while duration_ms == 0 or time.ticks_diff(time.ticks_ms(), start) < duration_ms:
            if blinker.update():
                print("led", blinker.state)
            time.sleep_ms(10)
    finally:
        led.value(0)
        print("blink stop: toggles", blinker.toggles)
