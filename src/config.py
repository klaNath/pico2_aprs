"""ピン割り当てと動作設定(配線を変えたらここだけ直す)。

Pico 2 W に載せ替えても使えるよう、無線チップが使う GP23/GP24/GP25/GP29 と ADC3 は使わない。
実際の配線は CLAUDE.md の「配線メモ」が正。未配線のものは後のマイルストーンで確定する:
    GP0  UART0 TX  -> GNSS (Teseo-LIV3FL) RX
    GP1  UART0 RX  <- GNSS TX
    GP2  PTT(トランジスタ経由で無線機の PTT を GND へ落とす)。GP3 と同じ PWM スライス 1
         だが、PTT は普通の GPIO 出力なので PWM の動作とは干渉しない
    GP3  AFSK 送信音声(PWM、slice 1 B) -> RC フィルタ -> 無線機の MIC/DATA 入力
    GP4-7  SPI0 microSD(MISO/CS/SCK/MOSI)
    GP10-15 SPI1 ST7789(SCK/MOSI/DC/CS/RST/BL)
    GP22 ビーコン送出のトグルスイッチ(GND へ落とす、内部プルアップ)
    GP20 GNSS 1PPS(予備)
    GP26 ADC0 受信音声 <- 無線機の SP/DATA 出力(バイアス + HPF 経由)
"""

from micropython import const

PIN_AFSK_OUT = const(3)
PIN_BEACON_SW = const(22)
PIN_PTT = const(2)
PIN_RX_ADC = const(26)

FS = const(19200)  # 送受信のサンプリング周波数(1200 baud x 16)
PWM_TOP = const(1023)  # PWM の分解能 10 bit、搬送波 150MHz / 1024 = 146.5 kHz
TX_AMPLITUDE = 0.4  # PWM のフルスケールに対する振幅(後段の分圧で無線機のレベルに合わせる)
TX_DELAY_MS = const(300)  # PTT を入れてからデータまでのフラグ送出時間(無線機の立ち上がり待ち)
TX_TAIL_MS = const(30)
PTT_ACTIVE_HIGH = True  # True: GP2 = 1 で送信(NPN/N-MOS で PTT 線を GND へ落とす回路)

# 自局。電波を出す前に必ず自局のコールサインに変える(N0CALL は試験用のダミー)
CALLSIGN = "N0CALL"
SSID = 7
TX_PATH = ("WIDE1-1", "WIDE2-1")
SYMBOL = ("/", "[")  # シンボル表, シンボル(/[ = 人)
