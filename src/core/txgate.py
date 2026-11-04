"""送信の出口。PTT はここだけで操作する。ハードウェアに依存しないので PC でもテストできる。

ピンは偽物に差し替えられる(hw.txctl が本物のピンを渡す)。
    TxGate     実際に PTT を入れる
    AudioOnly  音声(GP3)だけを出す試験用。PTT には触らない
"""


class TxGate:
    def __init__(self, pin, active_high=True):
        self._pin = pin
        self._on = 1 if active_high else 0
        self.keyed = False
        pin.value(1 - self._on)

    def key(self):
        self.keyed = True
        self._pin.value(self._on)

    def unkey(self):
        """PTT を切る。何度呼んでもよい。最初の行で切る(以降で例外が起きても切れているように)"""
        self._pin.value(1 - self._on)
        self.keyed = False


class _NoPin:
    def value(self, v=None):
        return 0


class AudioOnly(TxGate):
    """AfskTx に渡すと音声だけを出す。PTT のピンには一切触らない(hw.txctl が切ったままにする)"""

    def __init__(self):
        TxGate.__init__(self, _NoPin())
