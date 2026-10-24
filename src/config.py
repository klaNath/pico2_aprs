"""ピン割り当てと動作設定(配線を変えたらここだけ直す)。

Pico 2 W に載せ替えても使えるよう、無線チップが使う GP23/GP24/GP25/GP29 と ADC3 は使わない。
実際の配線は CLAUDE.md の「配線メモ」が正。未配線のものは後のマイルストーンで確定する:
    GP0  UART0 TX  -> GNSS (Teseo-LIV3FL) RX
    GP1  UART0 RX  <- GNSS TX
    GP2  PTT(トランジスタ経由で無線機の PTT を GND へ落とす)。GP3 と同じ PWM スライス 1
         だが、PTT は普通の GPIO 出力なので PWM の動作とは干渉しない
    GP3  AFSK 送信音声(PWM、slice 1 B) -> RC フィルタ -> 無線機の MIC/DATA 入力
    GP6-8  空き(Display Pack の RGB LED は基板上で切り離し済み)
    GP9-11 SPI1 microSD(CS GP9 / SCK GP10 / MOSI GP11)、MISO は GP28(SPI1 RX)
    GP12-15 Display Pack のボタン A / B / X / Y(押すと Low、内部プルアップ)
    GP16-20 Display Pack の LCD(SPI0: DC GP16 / CS GP17 / SCK GP18 / MOSI GP19、BL GP20)
    GP21 GNSS 1PPS(予備)
    GP22 ビーコン送出のトグルスイッチ(GND へ落とす、内部プルアップ)
    GP26 ADC0 受信音声 <- 無線機の SP/DATA 出力(バイアス + HPF 経由)
    GP28 SPI1 MISO(microSD)。ADC2 は使えなくなる
"""

from micropython import const

PIN_AFSK_OUT = const(3)
PIN_BEACON_SW = const(22)
PIN_PTT = const(2)
PIN_RX_ADC = const(26)

# 表示(Pimoroni Pico Display Pack、240x135 ST7789)。パネルを替えるときは LCD_PANEL を変える
LCD_PANEL = "pico_display"  # hw.st7789.PANELS のキー。将来の 2.4 インチは "st7789_320x240"
LCD_SPI = const(0)
PIN_LCD_SCK = const(18)
PIN_LCD_MOSI = const(19)
PIN_LCD_CS = const(17)
PIN_LCD_DC = const(16)
PIN_LCD_BL = const(20)
LCD_BAUD = const(62_500_000)
PIN_BTN_A = const(12)
PIN_BTN_B = const(13)
PIN_BTN_X = const(14)
PIN_BTN_Y = const(15)

# microSD(SPI1)。マイルストーン 5 で使う
PIN_SD_SCK = const(10)
PIN_SD_MOSI = const(11)
PIN_SD_MISO = const(28)
PIN_SD_CS = const(9)
PIN_GNSS_PPS = const(21)

# GNSS(秋月 GT-505GGBL5-DR-N、MediaTek、NMEA 4.10、出荷時 115200 bps)。UART0
# 線の色: 赤 VCC / 黒 GND / 橙 TXD(モジュール出力 -> GP1) / 緑 RXD(<- GP0) / 茶 PPS(-> GP21)
GNSS_UART = const(0)
PIN_GNSS_TX = const(0)  # Pico から送る(モジュールの RXD = 緑)
PIN_GNSS_RX = const(1)  # Pico が受ける(モジュールの TXD = 橙)
GNSS_BAUD = const(115200)

FS = const(19200)  # 送受信のサンプリング周波数(1200 baud x 16)
PWM_TOP = const(1023)  # PWM の分解能 10 bit、搬送波 150MHz / 1024 = 146.5 kHz
TX_AMPLITUDE = 0.4  # PWM のフルスケールに対する振幅(後段の分圧で無線機のレベルに合わせる)
TX_DELAY_MS = const(300)  # PTT を入れてからデータまでのフラグ送出時間(無線機の立ち上がり待ち)
TX_TAIL_MS = const(30)
PTT_ACTIVE_HIGH = True  # True: GP2 = 1 で送信(NPN/N-MOS で PTT 線を GND へ落とす回路)

# 自局(CLAUDE.md の指定により既定は JK1BBJ)。電波を出す前に SSID・経路も含めて確認する
CALLSIGN = "JK1BBJ"
SSID = 7
TX_PATH = ("WIDE1-1", "WIDE2-1")
# Mic-E 以外のパケットの宛先(ソフトウェア識別子)。APZxxx は実験用に予約された範囲
TOCALL = "APZPI2"
SYMBOL = ("/", "[")  # シンボル表, シンボル(/[ = 人)
