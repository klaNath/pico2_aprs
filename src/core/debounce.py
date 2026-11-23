"""スイッチのチャタリング・誤検出の除去(ビーコンスイッチ GP21 用)。

オフはすぐ(送信を止める側は遅らせない)、オンは on_ms 続けて読めたときだけ。
送信時の RF や静電気でピンが一瞬 Low になっても、ビーコンの送出を始めない。
確保なし。ハードに依存しない(read は呼ぶと bool を返すもの)。
"""

import time


class OnDelay:
    def __init__(self, read, on_ms=100):
        self.read = read
        self.on_ms = on_ms
        self._since = None  # オンを最初に読んだ ticks_ms
        self._on = False  # on_ms 続いた(オフを読むまで保つ。差を取り続けると 6 日余りで反転する)

    def value(self, now=None):
        if not self.read():
            self._since = None
            self._on = False
            return False
        if self._on:
            return True
        if now is None:
            now = time.ticks_ms()
        if self._since is None:
            self._since = now
        if time.ticks_diff(now, self._since) >= self.on_ms:
            self._on = True
        return self._on
