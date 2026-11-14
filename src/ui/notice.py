"""LCD に文字だけの通知を出す(局の異常停止、未完成のモードなど)。

画面を作り直して描く。局の画面(ui.controller)とは独立で、失敗しても呼び出し側が無視できるよう、
例外はそのまま投げる。作業領域は小さく取る(メモリ不足で止まった直後でも描けるように)。
"""

import gc

from machine import SPI, Pin

import config
from hw.st7789 import ST7789
from ui.screen import Screen
from ui.views import BLACK, CH, ROW, WHITE


def wrap(lines, cols, max_rows):
    """各行を cols 文字で折り返す。空行は残す。max_rows 行まで"""
    out = []
    for s in lines:
        out += [s[i:i + cols] for i in range(0, len(s), cols)] or [""]
    return out[:max_rows]


def show(lines, head_color, scratch_bytes=4096):
    """lines を左上から並べて出す。先頭行だけ head_color、ほかは白"""
    gc.collect()
    spi = SPI(config.LCD_SPI, baudrate=config.LCD_BAUD, sck=Pin(config.PIN_LCD_SCK),
              mosi=Pin(config.PIN_LCD_MOSI))
    lcd = ST7789(spi, config.PIN_LCD_DC, config.PIN_LCD_CS, config.PIN_LCD_BL, config.LCD_PANEL)
    rows = wrap(lines, lcd.width // CH, lcd.height // ROW)

    def draw(fb, dy):
        for i, s in enumerate(rows):
            fb.text(s, 0, 1 + i * ROW + dy, head_color if i == 0 else WHITE)

    Screen(lcd, scratch_bytes).region(0, 0, lcd.width, lcd.height, draw, BLACK)
    lcd.backlight(0.8)
