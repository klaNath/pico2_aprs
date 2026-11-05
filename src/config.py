"""ピン割り当てと動作設定(配線を変えたらここだけ直す)。

基板 hw/Pico_MicroSD_M10_Board_rev1.net(開発途中の rev1)の結線に合わせてある。
Pico 2 W に載せ替えても使えるよう、無線チップが使う GP23/GP24/GP25/GP29 と ADC3 は使わない。
    GP0  UART0 TX  -> GNSS (u-blox MAX-M10M) RXD
    GP1  UART0 RX  <- GNSS TXD
    GP2  PTT(無線機ボード J5-1 RF_TX)。GP3 と同じ PWM スライス 1 だが普通の GPIO 出力
    GP3  AFSK 送信音声(PWM、slice 1 B)-> 0 Ω -> 無線機ボード J5-2
    GP4/5 I2C0 SDA/SCL(無線機ボード J5。将来の Si5351 用)
    GP6  GNSS RESET_N(BBR を消すので通常は駆動しない)
    GP7  状態表示 LED(赤、D2)
    GP8  (未接続)455 kHz FM IF の入力(RX_SOURCE = "if" のとき。リミッタの 0-3.3 V の方形波)
    GP9  microSD カード検出
    GP10-15 microSD(SPI: SCK GP10 / MOSI GP11 / MISO GP12 / CS GP15。DAT1/2 = GP13/14)
    GP16-20 LCD ST7789(SPI0: DC GP16 / CS GP17 / SCK GP18 / MOSI GP19、BL GP20)。
         LCD の RESET は Pico の RUN と共通(ソフトからは SWRESET で代用)
    GP21 外部スイッチ(J1)。内部プルアップ、GND に落とす = ビーコン送出オン。
         High(開放・断線・未接続を含む)= オフ(断線で送信しないように)。
         rev1 の J1 は 1 = 3.3V、2 = GP21、3 = GND(ネットリストは変更前のまま)。
         スイッチの向きが以前と同じになるよう、実物の配線をつなぎ替えて使う
    GP22 GNSS TIMEPULSE(PPS)。黄 LED(D2)も点く。SAFEBOOT_N と兼用なので駆動しない
    GP26 ADC0 受信音声(rev1 では周辺光センサの位置。次の改版で音声入力になる予定)
    GP27 ADC1 電源電圧の監視(rev1 では分圧抵抗が未実装)
"""

from micropython import const

PIN_AFSK_OUT = const(3)
PIN_PTT = const(2)
PIN_RX_ADC = const(26)
# 受信の入力: "adc" = GP26 の音声を ADC で取る(hw.afsk_rx)、
#             "if"  = GP8 の 455 kHz FM IF(リミッタの方形波)を PIO でエッジの時刻から
#                     復調する(hw.fm_if_rx)
RX_SOURCE = "adc"
PIN_RX_IF = const(8)
RX_IF_METHOD = "pll"  # "pll"(DPLL。乱れたエッジに強い)/ "count"(周期の逆数計数)
PIN_BEACON_SW = const(21)  # 内部プルアップ。Low(GND に落とす)でビーコン送出オン(未接続はオフ)
BEACON_SW_ON = 0
PIN_STATUS_LED = const(7)

# 表示(ST7789 2.4 インチ 240x320、横長 320x240 で使う)。パネルを替えるときは LCD_PANEL を変える
LCD_PANEL = "st7789_320x240"  # hw.st7789.PANELS のキー(Pico Display Pack は "pico_display")
LCD_SPI = const(0)
PIN_LCD_SCK = const(18)
PIN_LCD_MOSI = const(19)
PIN_LCD_CS = const(17)
PIN_LCD_DC = const(16)
PIN_LCD_BL = const(20)
LCD_BAUD = const(62_500_000)
BUTTONS = ()  # 画面操作のボタン(rev1 の基板には無い)。(A, B, X, Y) の GPIO 番号

# microSD(SPI1)
SD_SPI = const(1)
PIN_SD_SCK = const(10)
PIN_SD_MOSI = const(11)
PIN_SD_MISO = const(12)
PIN_SD_CS = const(15)
PIN_SD_DET = const(9)

# GNSS(u-blox MAX-M10M)。UART0、ドライバは micropython-maxm10
GNSS_UART = const(0)
PIN_GNSS_TX = const(0)  # Pico から送る(モジュールの RXD)
PIN_GNSS_RX = const(1)  # Pico が受ける(モジュールの TXD)
PIN_GNSS_RESETN = const(6)
PIN_GNSS_PPS = const(22)
GNSS_BAUD = const(115200)

FS = const(19200)  # 送受信のサンプリング周波数(1200 baud x 16)
PWM_TOP = const(1023)  # PWM の分解能 10 bit、搬送波 150MHz / 1024 = 146.5 kHz
TX_AMPLITUDE = 0.4  # PWM のフルスケールに対する振幅(後段の分圧で無線機のレベルに合わせる)
TX_DELAY_MS = const(300)  # PTT を入れてからデータまでのフラグ送出時間(無線機の立ち上がり待ち)
TX_TAIL_MS = const(30)
# 送信波形のバッファの長さ(起動時に 1 回だけ確保して使い回す。1 ms あたり 38.4 バイト)。
# ビーコン(Mic-E、コメント付き)は約 0.7 秒。これより長いパケットは送れない
TX_BUFFER_MS = const(1500)
PTT_ACTIVE_HIGH = True  # True: GP2 = 1 で送信(NPN/N-MOS で PTT 線を GND へ落とす回路)

# 自局。ソースに書かない(SAF-01)。None の間は core.txgate が送信を拒否する。
# 仮: APP-05(設定の保存)ができるまでは、git 管理外の src/config_local.py で設定する
# (下のこのファイルの最後を参照。例は tools/config_local.example.py)。
# 電波を出す前に SSID・経路も含めて確認する
CALLSIGN = None
SSID = 7
TEST_CALL = "Q1TEST"  # 架空のコールサイン。PTT を入れない音声だけのデモで使う
TX_PATH = ("WIDE1-1", "WIDE2-1")
# Mic-E 以外のパケットの宛先(ソフトウェア識別子)。APZxxx は実験用に予約された範囲
TOCALL = "APZPI2"
SYMBOL = ("/", "[")  # シンボル表, シンボル(/[ = 人)

# ビーコン(Mic-E)。前回の位置から BEACON_MOVE_M 以上動いていれば BEACON_MOVING_S ごと、
# 動いていなければ BEACON_STILL_S ごと。スイッチを入れて測位が取れたら最初の 1 回はすぐ送る
BEACON_MOVING_S = 90
BEACON_STILL_S = 600
BEACON_MOVE_M = 10.0
# 異常で再起動した直後は、最初のビーコンをこの秒数だけ待つ(再起動の繰り返しで送り続けないように)
BEACON_RESTART_HOLD_S = 90
BEACON_MESSAGE = 1  # aprs.mice.MESSAGES の番号(1 = En Route)
BEACON_COMMENT = b"Pico2 APRS"
# 実際に電波を出すか。False の間はビーコンの時機の管理・画面・SD の記録(TX(dry) の印)だけ動き、
# PTT と音声は出さない。無線機の接続を確かめてから True にする。
# True でも、コールサインと周波数がそろわなければ core.txgate が拒否する
TX_ENABLED = False
# tx_demo で本物の PTT を入れるための明示フラグ(既定 None = 音声だけ)。無線機側で確定している
# 送信周波数(Hz)を書く。電波が出るので、ダミーロードをつないでオーナーが判断したときだけ設定する。
# tests/ はこの値を見ない(実機テストは常に偽のピンを使う)
BENCH_TX_HZ = None
# Si5351B(hw.si5351)。出力の番号は未確定なので None。局にはまだ組み込んでいない
SI5351_I2C_ADDR = const(0x60)
SI5351_TX_CLK = None
SI5351_LO1_CLK = None
SI5351_I_CLK = None
SI5351_Q_CLK = None
SI5351_APR = 240  # 仮: VCXO の引き込み幅(ppm)。生成例の値でデータシートの上限(+/-240)
SI5351_IDRV_LO1_MA = 8  # 仮: 出力の駆動電流(2、4、6、8 mA)。CBPro の生成例の値
SI5351_IDRV_IQ_MA = 8  # 仮
SI5351_IDRV_TX_MA = 8  # 仮
SI5351_XTAL_CL_PF = 8  # 仮: 水晶の内部負荷容量(6、8、10 pF)。CBPro の生成例の値
# レジスタ 9。0x00 = OEB ピンが出力を制御する(オーナー決定 2026-11-05)。ピンは 0 ohm で GND につなぐ
SI5351_OEB_MASK = 0x00

# 仮: このマシンだけの設定(git 管理外)。上の値を上書きする
try:
    from config_local import *  # noqa: F403  # pyright: ignore
except ImportError:
    pass
