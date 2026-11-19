"""hw.afsk_tx(PWM + DMA 送信)のテスト。実機専用(test_hw_*)。配線は不要。

dev.py check / build / release からも走るので、本物の PTT(GP2)は駆動しない。GP2 は切った状態に
保ち、PTT の動きは偽のピンで確かめる。音声(GP3)の PWM は本物が動く(無線機ボードが
つながっていても、PTT が入らなければ送信はしない)。コールサインは架空の例。"""

import time

import config
from core.txgate import FixedFreq, TxGate
from tnc.ax25 import Frame


class FakePin:
    def __init__(self):
        self.v = None

    def value(self, v=None):
        if v is None:
            return self.v
        self.v = v


def _tx(max_ms=config.TX_BUFFER_MS, fx25=False):
    from hw import txctl
    from hw.afsk_tx import AfskTx

    txctl.audio_only()  # 本物の GP2 を切った状態にする
    pin = FakePin()
    # 偽のピンなので、架空のコールサインと周波数で条件をそろえる
    gate = TxGate(pin, callsign="Q1TEST", allowed=True, freq=FixedFreq(145_000_000))
    tx = AfskTx(config.PIN_AFSK_OUT, gate, config.FS, config.PWM_TOP,
                config.TX_AMPLITUDE, config.TX_DELAY_MS, config.TX_TAIL_MS, max_ms=max_ms,
                fx25=fx25)
    return tx, pin


def test_hw_tx_fx25_build():
    import gc

    tx, _pin = _tx(fx25=True)
    try:
        f = bytes(Frame.parse("JA1XYZ-9>APRS,WIDE1-1:!3540.12N/13945.67E>Pico2 TX test").encode())
        t0 = time.ticks_us()
        n = tx.build([f])  # 内側が 128 バイト以下: FX.25
        tb = time.ticks_diff(time.ticks_us(), t0)
        assert tx.fx_used == 1
        gc.collect()
        free = gc.mem_free()
        tx.build([f])
        assert free - gc.mem_free() < 2048  # 2 回目以降は大きな確保をしない
        fx = tx.fx
        tx.fx = None  # 同じバッファで従来の AX.25 の長さを測る(2 つ目の AfskTx は確保しない)
        try:
            n_plain = tx.build([f])
        finally:
            tx.fx = fx
        assert tx.fx_used == 0
        print("fx25 %d ms of audio (plain %d ms), build %d ms"
              % (n * 1000 // config.FS, n_plain * 1000 // config.FS, tb // 1000))
        assert n > n_plain
        assert n <= tx.nmax
        # 128 バイトを超える内側は従来の AX.25 のまま
        big = bytes(Frame.parse("JA1XYZ-9>APRS,WIDE1-1:>" + "x" * 104).encode())  # 130 バイト
        nb = tx.build([big])
        assert tx.fx_used == 0 and nb > 0
    finally:
        tx.close()



def test_hw_tx_longest_beacon_fits():
    # スマホから設定できる範囲で最長のビーコン(コメント 40 文字、パス 2 段)が TX_BUFFER_MS に
    # 収まる(docs/ble-protocol.md §7.5: 今の設定の範囲では frame_too_long は起きない)。
    # "~"(0x7E)はビット詰めが最も多い印字可能文字。作る時間と送出の長さを出す(§7.5 の数値)
    from core import settings
    from core.beacon import mice_frame

    comment = b"~" * settings.MAX_COMMENT
    path = ("WIDE1-1", "WIDE2-1")[:settings.MAX_PATH]
    f = mice_frame("Q1TEST-15", path, -35.12345, -139.12345, 99.0, 359.0, 9999.0, ("/", "["),
                   comment, settings.MAX_MESSAGE)
    import gc

    gc.collect()
    tx, _pin = _tx()  # バッファは 1 回だけ確保する(57.6 KB。2 回取ると後のテストが断片化で落ちる)
    try:
        for fx25 in (False, True):
            tx.set_fx25(fx25)
            t0 = time.ticks_us()
            n = tx.build([bytes(f.encode())])  # 入りきらなければ ValueError
            build_ms = time.ticks_diff(time.ticks_us(), t0) // 1000
            air_ms = n * 1000 // config.FS
            print("longest beacon fx25=%s: build %d ms, air %d ms (buffer %d ms)" % (
                fx25, build_ms, air_ms, config.TX_BUFFER_MS))
            assert air_ms <= config.TX_BUFFER_MS
            assert tx.fx_used == (1 if fx25 else 0)
    finally:
        tx.close()

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


def test_hw_tx_set_fx25_switches_without_reallocating():
    tx, _pin = _tx(fx25=False)
    try:
        f = bytes(Frame.parse("JA1XYZ-9>APRS,WIDE1-1:!3540.12N/13945.67E>Pico2 TX test").encode())
        assert tx.fx is None
        n_plain = tx.build([f])
        tx.set_fx25(True)  # 設定の変更(core.settings)で実行中に切り替える
        enc = tx.fx
        assert enc is not None
        assert tx.build([f]) > n_plain and tx.fx_used == 1
        tx.set_fx25(False)
        assert tx.fx is None and tx.build([f]) == n_plain and tx.fx_used == 0
        tx.set_fx25(True)
        assert tx.fx is enc  # 作り直さない
    finally:
        tx.close()
