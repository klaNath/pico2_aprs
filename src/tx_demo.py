"""送信の動作確認: 試験ビーコンを一定間隔で GP3 から出す。

    python tools/dev.py run src/tx_demo.py -t 75     (5 秒おきに 12 回送って止まる)

PC のライン入力につないで Dire Wolf で復号できるかを確かめる。既定では PTT(GP2)を切ったままで、
音声だけを出す。config.BENCH_TX_HZ を明示したときだけ PTT を入れる(電波が出るので、ダミーロードを
つないでオーナーが判断したときだけ使う)。
送るもの: Mic-E 位置(固定の試験座標)とステータスを交互に、5 秒おき。
"""

import time

import config
from aprs import mice
from core.txgate import AudioOnly
from hw import txctl
from hw.afsk_tx import AfskTx
from tnc.ax25 import Address, Frame

INTERVAL_S = 5
# dev.py run がタイムアウトで切れても、デバイス上のプログラムは次の接続まで動き続ける。
# 無線機につないだときに PTT を入れ続けないよう、決まった回数で必ず止める
MAX_SENDS = 12
LAT, LON = 35.681236, 139.767125  # 試験用の固定座標(東京駅付近)


def frames(k):
    # 音声だけのときは架空のコールサインでよい。PTT を入れるときはゲートが未設定を拒否する
    src = Address(config.CALLSIGN or config.TEST_CALL, config.SSID)
    path = [Address.parse(p) for p in config.TX_PATH]
    if k % 2 == 0:
        dst, info = mice.encode(LAT, LON, speed=4.0 * (k % 10), course=(k * 37) % 360 + 1,
                                table=config.SYMBOL[0], code=config.SYMBOL[1], message=1,
                                alt=40, comment=b"Pico2 APRS test %d" % k)
        f = Frame(Address(dst), src, path, info)
    else:
        f = Frame(Address(config.TOCALL), src, path, b">Pico2 APRS TX test %d" % k)
    return f, bytes(f.encode())


def main():
    gate = txctl.bench_gate()
    tx = AfskTx(config.PIN_AFSK_OUT, gate, config.FS, config.PWM_TOP,
                config.TX_AMPLITUDE, config.TX_DELAY_MS, config.TX_TAIL_MS)
    print("TX demo: GP%d audio, PTT %s, every %d s, %d times"
          % (config.PIN_AFSK_OUT, "stays off" if isinstance(gate, AudioOnly) else "ON",
             INTERVAL_S, MAX_SENDS))
    why = gate.refuse_reason()
    if why is not None:
        print("TX demo: refused:", why)
        tx.close()
        return
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
