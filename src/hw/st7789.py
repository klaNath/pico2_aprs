"""ST7789 LCD の最小ドライバ(SPI、RGB565)。描画は MicroPython 標準の framebuf で行う。

パネルごとの違い(解像度・オフセット・MADCTL・電圧/ガンマ)は PANELS にまとめてある。
初期化の手順と値は Pimoroni の pimoroni-pico(MIT License, Copyright (c) 2021 Pimoroni Ltd)
drivers/st7789/st7789.cpp を参考にした。

色について: framebuf の RGB565 は 16 bit 値を下位バイトから格納するが、ST7789 は上位バイトから
受け取る。rgb() はバイトを入れ替えた値を返すので、framebuf にそのまま描けば転送時の変換は要らない。
"""

import time

from machine import PWM, Pin
from micropython import const

from hw.rgb565 import rgb  # noqa: F401  (lcd_test などはここから import する)

_SWRESET = const(0x01)
_SLPOUT = const(0x11)
_INVON = const(0x21)
_DISPON = const(0x29)
_CASET = const(0x2A)
_RASET = const(0x2B)
_RAMWR = const(0x2C)
_TEON = const(0x35)
_MADCTL = const(0x36)
_COLMOD = const(0x3A)

# MADCTL のビット
ROW_ORDER = const(0x80)
COL_ORDER = const(0x40)
SWAP_XY = const(0x20)
SCAN_ORDER = const(0x10)

# 共通の初期化(電源・ポーチ設定)。(コマンド, データ)
_COMMON = (
    (0xB2, b"\x0c\x0c\x00\x33\x33"),  # PORCTRL
    (0xC0, b"\x2c"),  # LCMCTRL
    (0xC2, b"\x01"),  # VDVVRHEN
    (0xC3, b"\x12"),  # VRHS
    (0xC4, b"\x20"),  # VDVS
    (0xD0, b"\xa4\xa1"),  # PWCTRL1
    (0xC6, b"\x0f"),  # FRCTRL2(60 Hz)
    (0xB0, b"\x00\xc0"),  # RAMCTRL
)

# 横長の向き(ROTATE_0)で使う設定
PANELS = {
    # Pimoroni Pico Display Pack(1.14 インチ 240x135)。実機で確認する
    "pico_display": {
        "width": 240, "height": 135, "xoff": 40, "yoff": 53,
        "madctl": COL_ORDER | SWAP_XY | SCAN_ORDER,
        "init": (
            (0xC3, b"\x00"),  # VRHS
            (0xB7, b"\x75"),  # GCTRL
            (0xBB, b"\x3d"),  # VCOMS
            (0xD6, b"\xa1"),
            (0xE0, b"\x70\x04\x08\x09\x09\x05\x2a\x33\x41\x07\x13\x13\x29\x2f"),
            (0xE1, b"\x70\x03\x09\x0a\x09\x06\x2b\x34\x41\x07\x12\x14\x28\x2e"),
        ),
    },
    # 2.4 インチ 240x320(横長 320x240 で使う)。Pimoroni Pico Display 2.0 の値。
    # rev1 基板のモジュールで向き・枠・色・文字を目視確認済み(2026-10-25)
    "st7789_320x240": {
        "width": 320, "height": 240, "xoff": 0, "yoff": 0,
        "madctl": COL_ORDER | SWAP_XY | SCAN_ORDER,
        "init": (
            (0xB7, b"\x35"),
            (0xBB, b"\x1f"),
            (0xE0, b"\xd0\x08\x11\x08\x0c\x15\x39\x33\x50\x36\x13\x14\x29\x2d"),
            (0xE1, b"\xd0\x08\x10\x08\x06\x06\x39\x44\x51\x0b\x16\x14\x2f\x31"),
        ),
    },
}


class ST7789:
    def __init__(self, spi, dc, cs, bl=None, panel="pico_display"):
        """spi: 初期化済みの machine.SPI。dc/cs/bl: GPIO 番号。
        rp2 の SPI は MISO を指定しないと既定ピン(SPI0 なら GP16)を MISO にしてしまう。
        Pico Display Pack では GP16 が DC なので、SPI を作った後に DC を GPIO に戻している。"""
        p = PANELS[panel]
        self.width = p["width"]
        self.height = p["height"]
        self._xoff = p["xoff"]
        self._yoff = p["yoff"]
        self.spi = spi
        self.dc = Pin(dc, Pin.OUT, value=1)
        self.cs = Pin(cs, Pin.OUT, value=1)
        self.bl = PWM(Pin(bl), freq=1000, duty_u16=0) if bl is not None else None
        self._buf4 = bytearray(4)
        self._cmd(_SWRESET)
        time.sleep_ms(150)
        self._cmd(_TEON)
        self._cmd(_COLMOD, b"\x05")  # 16 bit/pixel
        for c, d in _COMMON:
            self._cmd(c, d)
        for c, d in p["init"]:
            self._cmd(c, d)
        self._cmd(_INVON)
        self._cmd(_SLPOUT)
        self._cmd(_DISPON)
        time.sleep_ms(100)
        self._cmd(_MADCTL, bytes([p["madctl"]]))

    def _cmd(self, c, data=None):
        self.cs(0)
        self.dc(0)
        self.spi.write(bytes([c]))
        if data:
            self.dc(1)
            self.spi.write(data)
        self.cs(1)

    def _window(self, x, y, w, h):
        b = self._buf4
        x0 = x + self._xoff
        x1 = x0 + w - 1
        y0 = y + self._yoff
        y1 = y0 + h - 1
        b[0] = x0 >> 8
        b[1] = x0 & 0xFF
        b[2] = x1 >> 8
        b[3] = x1 & 0xFF
        self._cmd(_CASET, b)
        b[0] = y0 >> 8
        b[1] = y0 & 0xFF
        b[2] = y1 >> 8
        b[3] = y1 & 0xFF
        self._cmd(_RASET, b)

    def blit(self, buf, x, y, w, h):
        """RGB565(rgb() の並び)の w*h ピクセルを (x, y) に転送する。
        buf は bytes 系か memoryview。"""
        if x < 0 or y < 0 or x + w > self.width or y + h > self.height:
            raise ValueError("blit out of screen")
        self._window(x, y, w, h)
        self.cs(0)
        self.dc(0)
        self.spi.write(bytes([_RAMWR]))
        self.dc(1)
        self.spi.write(buf)
        self.cs(1)

    def backlight(self, level):
        """0.0 - 1.0"""
        if self.bl is not None:
            self.bl.duty_u16(int(max(0.0, min(1.0, level)) * 65535))
