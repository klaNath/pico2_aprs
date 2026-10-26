"""IF 受信器(hw.fm_if_rx.IfReceiver)のリアルタイム試験: Core1 で受信しながら Core0 から送り、
復号数と Core1 の負荷率を測る。

    python tools/dev.py run src/fm_if_rt_test.py -t 60

配線は fm_if_afsk_test.py と同じ(GP3 -> LPF -> 信号発生器の外部変調入力、発生器の 455 kHz FM
出力 -> 1 kohm -> GP8)。3 フレームを ROUNDS 周送る。受信器の重複除去(3 秒以内の同じ内容)に
かからないよう、本文に周回の番号を入れる。
"""

import gc
import time

import config
from hw.afsk_tx import AfskTx
from hw.fm_if_rx import IfReceiver
from tnc.ax25 import Frame

ROUNDS = 5
GAP_MS = 300
TEXTS = (
    "JK1BBJ-7>APZPI2,WIDE1-1:>FM IF realtime %d-1",
    "JK1BBJ-7>APZPI2,WIDE1-1:!3546.06N/14001.91E[FM IF realtime %d-2 0123456789",
    "JK1BBJ-7>APZPI2:>FM IF realtime %d-3 ABCDEFGHIJKLMNOPQRSTUVWXYZ",
)


def drain(rx, got):
    while True:
        b = rx.get()
        if b is None:
            return
        got.append(b)


def wait(rx, got, ms):
    t = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t) < ms:
        drain(rx, got)
        time.sleep_ms(10)


def main(rounds=ROUNDS):
    rx = IfReceiver(8)
    tx = AfskTx(config.PIN_AFSK_OUT, config.PIN_PTT, config.FS, config.PWM_TOP,
                config.TX_AMPLITUDE, config.TX_DELAY_MS, config.TX_TAIL_MS,
                config.PTT_ACTIVE_HIGH, max_ms=1000)
    sent = []
    got = []
    gc.collect()
    print("free %d, %d rounds x %d frames" % (gc.mem_free(), rounds, len(TEXTS)))
    rx.start()
    t0 = time.ticks_ms()
    try:
        wait(rx, got, 500)  # 平均(搬送波)が落ち着くまで
        for r in range(rounds):
            for text in TEXTS:
                f = bytes(Frame.parse(text % r).encode())
                sent.append(f)
                tx.start(n=tx.build([f]))
                while not tx.finish():
                    drain(rx, got)
                    time.sleep_ms(5)
                wait(rx, got, GAP_MS)
        wait(rx, got, 500)
    finally:
        dt = time.ticks_diff(time.ticks_ms(), t0)
        rx.stop()
        tx.close()
        rx.close()
    ok = sum(1 for b in sent if b in got)
    lost = [Frame.decode(b).info[-12:] for b in sent if b not in got]
    print("RESULT decoded %d / %d sent, other frames %d" % (ok, len(sent), len(got) - ok))
    if lost:
        print("  lost:", lost)
    print("core1 load %.1f%% (busy %d ms in %d ms), audio %d samples = %.1f Hz" % (
        100 * rx.busy_us / (dt * 1000), rx.busy_us // 1000, dt, rx.samples,
        rx.samples * 1000 / dt))
    print("lag max %d / %d windows (%.0f ms), overruns %d" % (
        rx.lag_max, rx.ring.n, rx.lag_max * 1000 / 18958, rx.overruns))


if __name__ == "__main__":
    main()
