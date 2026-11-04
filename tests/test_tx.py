"""hw.afsk_tx(PWM + DMA 送信)のテスト。実機専用(test_hw_*)。配線は不要。

dev.py check / build / release からも走るので、本物の PTT(GP2)は駆動しない。GP2 は切った状態に
保ち、PTT の動きは偽のピンで確かめる。音声(GP3)の PWM は本物が動く(無線機ボードが
つながっていても、PTT が入らなければ送信はしない)。"""

import time

import config
from core.txgate import TxGate
from tnc.ax25 import Frame


class FakePin:
    def __init__(self):
        self.v = None

    def value(self, v=None):
        if v is None:
            return self.v
        self.v = v


def _tx(max_ms=config.TX_BUFFER_MS):
    from hw import txctl
    from hw.afsk_tx import AfskTx

    txctl.audio_only()  # 本物の GP2 を切った状態にする
    pin = FakePin()
    tx = AfskTx(config.PIN_AFSK_OUT, TxGate(pin), config.FS, config.PWM_TOP,
                config.TX_AMPLITUDE, config.TX_DELAY_MS, config.TX_TAIL_MS, max_ms=max_ms)
    return tx, pin


def test_hw_tx_timing_and_ptt():
    tx, pin = _tx()
    try:
        f = bytes(Frame.parse("JA1XYZ-9>APRS,WIDE1-1:!3540.12N/13945.67E>Pico2 TX test").encode())
        t0 = time.ticks_us()
        n = tx.build([f])
        tb = time.ticks_diff(time.ticks_us(), t0)
        expect_us = n * 1_000_000 // config.FS
        assert pin.value() == 0
        from machine import mem32

        assert mem32[tx._cc] == 0
        seen = set()
        t0 = time.ticks_us()
        tx.start(n=n)
        assert pin.value() == 1
        while not tx.finish():
            seen.add(mem32[tx._cc] & 0xFFFF)
            time.sleep_us(300)
        dt = time.ticks_diff(time.ticks_us(), t0) - tx.settle_ms * 1000
        assert pin.value() == 0
        assert mem32[tx._cc] == 0  # 待機中は PWM を止めている(受信への回り込み対策)
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


def test_hw_tx_buffer_reused():
    import gc

    tx, _pin = _tx(max_ms=1000)
    try:
        assert tx.nmax == config.FS and len(tx.raw) == 2 * config.FS  # 1 秒分を作るときに確保
        f = bytes(Frame.parse("JA1XYZ-9>APRS,WIDE1-1:!3540.12N/13945.67E>Pico2 TX test").encode())
        n1 = tx.build([f])  # 1 回目でキャッシュなどが落ち着いてから測る
        gc.collect()
        free = gc.mem_free()
        n2 = tx.build([f])
        assert n1 == n2 and 0 < n2 <= tx.nmax
        assert free - gc.mem_free() < 2048  # 波形の大きさ(約 30 KB)の確保をしない
        try:
            tx.build([f] * 4)  # 1 秒に入りきらない
            raise AssertionError("ValueError expected")
        except ValueError:
            pass
    finally:
        tx.close()
