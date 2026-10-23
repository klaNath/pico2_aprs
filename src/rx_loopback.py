"""受信の動作確認(ループバック): Core1 で受信しながら、Core0 から試験フレームを送る。

    python tools/dev.py run src/rx_loopback.py -t 60

送信音声(GP3 → RC フィルタ)を受信側(GP26)に戻した状態で、送ったフレームが
復号できるかを数える。何もつないでいなくても動く(その場合は 0 件になり、
処理の遅れ・取りこぼしの統計だけを確認できる)。
"""

import gc
import time

import config
from aprs.packet import parse_frame
from hw.afsk_rx import Receiver
from hw.afsk_tx import AfskTx
from tnc.ax25 import Address, DecodeError, Frame

N_FRAMES = 10
GAP_MS = 1500


def drain(rx, got, bad):
    while True:
        b = rx.get()
        if b is None:
            return
        try:
            f = Frame.decode(b)
        except DecodeError:
            bad[0] += 1
            continue
        got.append(b)
        p = parse_frame(f)
        print("  RX %s>%s: %s %r" % (p.src, p.dst, p.kind, f.info))


def main():
    rx = Receiver(config.PIN_RX_ADC, config.FS)
    tx = AfskTx(config.PIN_AFSK_OUT, config.PIN_PTT, config.FS, config.PWM_TOP,
                config.TX_AMPLITUDE, config.TX_DELAY_MS, config.TX_TAIL_MS,
                config.PTT_ACTIVE_HIGH)
    src = Address(config.CALLSIGN, config.SSID)
    sent = []
    got = []
    bad = [0]
    gc.collect()
    rx.start()
    t0 = time.ticks_ms()
    try:
        for k in range(N_FRAMES):
            f = Frame(Address(config.TOCALL), src, [Address("WIDE1", 1)],
                      b">loopback %d %s" % (k, "x" * (k * 7)))
            b = bytes(f.encode())
            sent.append(b)
            tx.send_blocking([b])
            print("TX %d (%d bytes)" % (k, len(b)))
            t = time.ticks_ms()
            while time.ticks_diff(time.ticks_ms(), t) < GAP_MS:
                drain(rx, got, bad)
                time.sleep_ms(20)
        drain(rx, got, bad)
    finally:
        dt = time.ticks_diff(time.ticks_ms(), t0)
        rx.stop()
        tx.close()
        rx.close()
    ok = sum(1 for b in sent if b in got)
    print("decoded %d / %d sent, other good frames %d, bad FCS %d"
          % (ok, len(sent), len(got) - ok, bad[0]))
    print("RX: %d samples in %d ms (%.1f Hz), max lag %d / %d samples, overruns %d, adc overflow %s"
          % (rx.samples, dt, rx.samples * 1000 / dt, rx.lag_max, rx.ring.n, rx.overruns,
             rx.ring.overflowed()))


main()
