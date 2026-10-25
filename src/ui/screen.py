"""帯(バンド)分割で描画する画面ヘルパ。

画面全体のフレームバッファ(240x135 なら 64.8 KB、320x240 なら 153.6 KB)は持たず、
固定の作業用バッファ(既定 24 KB)を使って、領域を横長の帯に分けて描いては転送する。
描画関数 draw(fb, dy) は「領域の左上を (0, 0) とした座標」に dy を足して描く。
framebuf は範囲外を自動で切り捨てるので、帯ごとに同じ描画関数を呼ぶだけでよい。
"""

import framebuf


class Screen:
    def __init__(self, lcd, scratch_bytes=24 * 1024, scratch=None):
        """scratch: 作業用バッファ(bytearray)。None なら scratch_bytes をここで確保する"""
        self.lcd = lcd
        self.width = lcd.width
        self.height = lcd.height
        self._scratch = scratch if scratch is not None else bytearray(scratch_bytes)
        self._mv = memoryview(self._scratch)

    def region(self, x, y, w, h, draw, bg=0):
        """(x, y, w, h) の領域を draw(fb, dy) で描いて転送する。"""
        rows = max(1, len(self._scratch) // (2 * w))
        top = 0
        while top < h:
            bh = min(rows, h - top)
            n = 2 * w * bh
            fb = framebuf.FrameBuffer(self._mv[:n], w, bh, framebuf.RGB565)
            fb.fill(bg)
            draw(fb, -top)
            self.lcd.blit(self._mv[:n], x, y + top, w, bh)
            top += bh

    def fill(self, color):
        self.region(0, 0, self.width, self.height, lambda fb, dy: None, color)
