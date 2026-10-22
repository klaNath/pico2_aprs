"""hw.afsk_tx(PWM + DMA 送信)のテスト。実機専用(test_hw_*)。配線は不要
(GP3/GP21 に何もつないでいなければ、出力を出すだけで害はない)。"""

import time

import config
from tnc.ax25 import Frame


def _tx():
    from hw.afsk_tx import AfskTx

    return AfskTx(config.PIN_AFSK_OUT, config.PIN_PTT, config.FS, config.PWM_TOP,
                  config.TX_AMPLITUDE, config.TX_DELAY_MS, config.TX_TAIL_MS,
                  config.PTT_ACTIVE_HIGH)


def test_hw_tx_timing_and_ptt():
    tx = _tx()
    try:
        f = bytes(Frame.parse("JA1XYZ-9>APRS,WIDE1-1:!3540.12N/13945.67E>Pico2 TX test").encode())
        t0 = time.ticks_us()
        samples = tx.build([f])
        tb = time.ticks_diff(time.ticks_us(), t0)
        n = len(samples)
        expect_us = n * 1_000_000 // config.FS
        assert tx.ptt.value() != tx._ptt_on
        seen = set()
        t0 = time.ticks_us()
        tx.start(samples)
        assert tx.ptt.value() == tx._ptt_on
        from machine import mem32

        while not tx.finish():
            seen.add(mem32[tx._cc] & 0xFFFF)
            time.sleep_us(300)
        dt = time.ticks_diff(time.ticks_us(), t0)
        assert tx.ptt.value() != tx._ptt_on
        print("samples %d, build %d ms, sent in %d ms (expected %d ms), %d distinct duty values"
              % (n, tb // 1000, dt // 1000, expect_us // 1000, len(seen)))
        assert abs(dt - expect_us) < expect_us // 50 + 15000  # 2% + ポーリング間隔
        assert len(seen) > 20  # DMA が CC を書き換えている
        lo = min(seen)
        hi = max(seen)
        mid = (config.PWM_TOP + 1) // 2
        amp = int(mid * config.TX_AMPLITUDE)
        assert mid - amp - 2 <= lo and hi <= mid + amp + 2
    finally:
        tx.close()
