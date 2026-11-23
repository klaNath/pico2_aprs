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


def test_hw_rx_detects_a_stall_longer_than_the_ring():
    # Core1 がリング 1 周(約 213 ms)より長く止まったら、取りこぼしに数えて現在位置から読み直す。
    # 差の剰余だけでは、1 周以上書かれたことに気づけない(上書きされた中身を読む)
    from hw.afsk_rx import Receiver

    rx = Receiver(config.PIN_RX_ADC, config.FS)
    ring = rx.ring
    real = ring.position
    calls = [0]

    def slow_once():
        calls[0] += 1
        if calls[0] == 20:
            time.sleep_ms(300)  # Core1 の中で 1 回だけ止まる(GC などの模擬)
        return real()

    ring.position = slow_once
    try:
        rx.start()
        time.sleep_ms(1000)
        rx.stop()
        print("calls %d, overruns %d" % (calls[0], rx.overruns))
        assert calls[0] > 20 and rx.overruns == 1
    finally:
        rx.close()


def test_hw_rx_mute_while_sending():
    # 送信中(mute)は復調に回さず読み捨て、解除後は余韻(tail_ms)の分を捨ててから復調に戻る
    from hw.afsk_rx import Receiver

    rx = Receiver(config.PIN_RX_ADC, config.FS)
    try:
        rx.start()
        time.sleep_ms(200)
        rx.mute()
        time.sleep_ms(50)  # Core1 が今の 1 ブロックを終えるまで
        s0, m0 = rx.samples, rx.muted_samples
        time.sleep_ms(500)
        s1, m1 = rx.samples, rx.muted_samples
        assert s1 == s0, "demodulated while muted"
        assert abs((m1 - m0) - config.FS // 2) < config.FS // 10
        rx.unmute(100)
        time.sleep_ms(500)
        s2, m2 = rx.samples, rx.muted_samples
        print("muted %d, tail %d, then demod %d samples" % (m1 - m0, m2 - m1, s2 - s1))
        # 余韻の 100 ms 分(+ 解除前に捨てた少し)を捨て、残りは復調した
        assert config.FS // 10 <= m2 - m1 < config.FS // 10 + config.FS // 20
        assert s2 - s1 > config.FS * 3 // 10
        assert not rx.muted and rx.overruns == 0
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
