"""LCD の表示確認: 向き・オフセット・色を目で確かめるための画面を出す。

    python tools/dev.py run src/lcd_test.py -t 20

表示されるもの:
    - 画面の外周に 1 ピクセルの白枠(上下左右が欠けていなければオフセットは正しい)
    - 左上に赤の四角と "TL"、右下に青の四角と "BR"(向きの確認)
    - 中央に 赤/緑/青/白/黒 の色帯(色の並び RGB/BGR と反転の確認)
    - ボタン A/B/X/Y を押すと、押したボタン名が下に表示される(10 秒間。config.BUTTONS がある場合)
"""

import time

from machine import SPI, Pin

import config
from hw.st7789 import ST7789, rgb
from ui.screen import Screen

spi = SPI(config.LCD_SPI, baudrate=config.LCD_BAUD, sck=Pin(config.PIN_LCD_SCK),
          mosi=Pin(config.PIN_LCD_MOSI))
lcd = ST7789(spi, config.PIN_LCD_DC, config.PIN_LCD_CS, config.PIN_LCD_BL, config.LCD_PANEL)
scr = Screen(lcd)
W, H = scr.width, scr.height
WHITE = rgb(255, 255, 255)
RED = rgb(255, 0, 0)
GREEN = rgb(0, 255, 0)
BLUE = rgb(0, 0, 255)
BLACK = rgb(0, 0, 0)


def draw(fb, dy):
    fb.rect(0, 0 + dy, W, H, WHITE)
    fb.fill_rect(2, 2 + dy, 20, 20, RED)
    fb.text("TL", 26, 8 + dy, WHITE)
    fb.fill_rect(W - 22, H - 22 + dy, 20, 20, BLUE)
    fb.text("BR", W - 44, H - 16 + dy, WHITE)
    labels = (("R", RED), ("G", GREEN), ("B", BLUE), ("W", WHITE), ("K", BLACK))
    bw = (W - 40) // len(labels)
    for i, (name, col) in enumerate(labels):
        x = 20 + i * bw
        fb.fill_rect(x, 40 + dy, bw - 4, 30, col)
        fb.text(name, x + bw // 2 - 6, 74 + dy, WHITE)
    fb.text("%dx%d %s" % (W, H, config.LCD_PANEL), 26, 92 + dy, GREEN)


t0 = time.ticks_us()
scr.region(0, 0, W, H, draw, BLACK)
dt = time.ticks_diff(time.ticks_us(), t0)
lcd.backlight(0.8)
print("LCD %dx%d drawn in %d ms" % (W, H, dt // 1000))

buttons = {name: Pin(pin, Pin.IN, Pin.PULL_UP) for name, pin in zip("ABXY", config.BUTTONS)}
if not buttons:
    print("no buttons on this board (config.BUTTONS is empty)")
seen = set()
t_end = time.ticks_add(time.ticks_ms(), 10_000)
while time.ticks_diff(t_end, time.ticks_ms()) > 0:
    pressed = [n for n, p in buttons.items() if p.value() == 0]
    if pressed:
        seen.update(pressed)
        msg = "pressed: " + " ".join(sorted(seen))
        scr.region(26, 108, W - 52, 10, lambda fb, dy, m=msg: fb.text(m, 0, 1 + dy, WHITE), BLACK)
        print(msg)
    time.sleep_ms(50)
print("done, buttons seen:", sorted(seen))
