"""app.Blinker のテスト(実機上で実行。偽の Pin を使うので配線は不要)。"""

import time

import app


class FakePin:
    def __init__(self):
        self.history = []

    def value(self, v=None):
        if v is not None:
            self.history.append(v)
        return self.history[-1] if self.history else 0


def test_toggles_after_period():
    pin = FakePin()
    b = app.Blinker(pin, period_ms=100)
    t0 = b._last
    assert b.update(time.ticks_add(t0, 50)) is False
    assert b.update(time.ticks_add(t0, 100)) is True
    assert pin.history == [1]
    assert b.update(time.ticks_add(t0, 200)) is True
    assert pin.history == [1, 0]


def test_rejects_invalid_period():
    try:
        app.Blinker(FakePin(), period_ms=0)
    except ValueError:
        return
    raise AssertionError("ValueError was not raised")


def test_real_led_smoke():
    # 実機の LED を 0.3 秒だけ点滅させる(目視確認用。失敗しないことだけ検証)
    app.run(period_ms=100, duration_ms=300)
