"""色の変換(ハードウェアに依存しないので、ui.views を PC でテストするときも import できる)"""


def rgb(r, g, b):
    """8 bit の R, G, B -> framebuf にそのまま描ける(バイト入れ替え済みの)RGB565"""
    c = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    return ((c & 0xFF) << 8) | (c >> 8)
