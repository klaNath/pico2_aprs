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


def test_hw_level_of_a_sine():
    # 集計の関数(viper)と公開の計算を、振幅 500 のサイン波(直流 2100)で確かめる
    import math
    from array import array

    from core.level import db_peak, db_rms
    from hw.afsk_rx import Receiver, _level

    rx = Receiver(config.PIN_RX_ADC, config.FS)
    try:
        n = 1920
        buf = array("H", (int(2100 + 500 * math.sin(2 * math.pi * 1200 * i / config.FS))
                          for i in range(n)))
        _level(buf, 0, n, rx._lacc)  # type: ignore[arg-type]  (viper のポインタ型)
        rx._publish_level()
        ms, pk, clip = rx.level()
        print("level rms %.2f dBFS, peak %.2f dBFS" % (db_rms(ms), db_peak(pk)))
        assert abs(db_rms(ms) - 20 * math.log10(500 / 2048)) < 0.2
        assert abs(db_peak(pk) - 20 * math.log10(500 / 2048)) < 0.2 and not clip
        assert rx.recent_peak() == (pk, False)
        buf[5] = 4095  # ADC の端に当たったらクリップ
        _level(buf, 0, n, rx._lacc)  # type: ignore[arg-type]
        rx._publish_level()
        assert rx.level()[2] and rx.recent_peak()[1]
    finally:
        rx.close()
