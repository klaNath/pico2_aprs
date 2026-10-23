"""hw.afsk_rx(ADC + DMA リング + Core1 復調)のテスト。実機専用(test_hw_*)。配線は不要。"""

import time

import config


def test_hw_rx_keeps_up():
    from hw.afsk_rx import Receiver

    rx = Receiver(config.PIN_RX_ADC, config.FS)
    try:
        rx.start()
        t0 = time.ticks_ms()
        time.sleep_ms(2000)
        rx.stop()
        dt = time.ticks_diff(time.ticks_ms(), t0)
        rate = rx.samples * 1000 / dt
        print("rx %.0f Hz, max lag %d / %d, overruns %d" % (rate, rx.lag_max, rx.ring.n,
                                                           rx.overruns))
        assert abs(rate - config.FS) < config.FS * 0.02
        assert rx.overruns == 0
        assert rx.lag_max < rx.ring.n // 2
        assert not rx.ring.overflowed()
        a = rx.ring.addr
        assert a % 8192 == 0  # DMA リングは境界に揃っていること(揃っていないと配列外を壊す)
    finally:
        rx.close()
