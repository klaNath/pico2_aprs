"""送信の動作確認: 試験ビーコンを一定間隔で GP20 から出す(PTT は GP21)。

    python tools/dev.py run src/tx_demo.py -t 75     (5 秒おきに 12 回送って止まる)

PC のライン入力につないで Dire Wolf で復号できるかを確かめる(電波は出さない)。
送るもの: Mic-E 位置(固定の試験座標)とステータスを交互に、5 秒おき。
"""

import time

import config
from aprs import mice
from hw.afsk_tx import AfskTx
from tnc.ax25 import Address, Frame

INTERVAL_S = 5
# dev.py run がタイムアウトで切れても、デバイス上のプログラムは次の接続まで動き続ける。
# 無線機につないだときに PTT を入れ続けないよう、決まった回数で必ず止める
MAX_SENDS = 12
LAT, LON = 35.681236, 139.767125  # 試験用の固定座標(東京駅付近)


def frames(k):
    src = Address(config.CALLSIGN, config.SSID)
    path = [Address.parse(p) for p in config.TX_PATH]
    if k % 2 == 0:
        dst, info = mice.encode(LAT, LON, speed=4.0 * (k % 10), course=(k * 37) % 360 + 1,
                                table=config.SYMBOL[0], code=config.SYMBOL[1], message=1,
                                alt=40, comment=b"Pico2 APRS test %d" % k)
        f = Frame(Address(dst), src, path, info)
    else:
        f = Frame(Address("APRS"), src, path, b">Pico2 APRS TX test %d" % k)
    return f, bytes(f.encode())


def main():
    tx = AfskTx(config.PIN_AFSK_OUT, config.PIN_PTT, config.FS, config.PWM_TOP,
                config.TX_AMPLITUDE, config.TX_DELAY_MS, config.TX_TAIL_MS,
                config.PTT_ACTIVE_HIGH)
    print("TX demo: GP%d audio, GP%d PTT, every %d s, %d times"
          % (config.PIN_AFSK_OUT, config.PIN_PTT, INTERVAL_S, MAX_SENDS))
    k = 0
    try:
        while k < MAX_SENDS:
            f, b = frames(k)
            t0 = time.ticks_ms()
            tx.send_blocking([b])
            print("[%d] sent %d ms: %s:%r" % (k, time.ticks_diff(time.ticks_ms(), t0),
                                              f.header(), f.info))
            k += 1
            time.sleep(INTERVAL_S)
    finally:
        tx.close()
        print("TX demo: done (PTT off)")


main()
