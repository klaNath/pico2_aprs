"""送信の出口。PTT はここだけで操作する。ハードウェアに依存しないので PC でもテストできる。

ピンは偽物に差し替えられる(hw.txctl が本物のピンを渡す)。
    TxGate     実際に PTT を入れる
    AudioOnly  音声(GP3)だけを出す試験用。PTT には触らない
入れている時間の上限(SAF-02)もここで持つ。expired() を見て止めるのは呼び出し側(hw.afsk_tx)。
ファームウェアが止まったときの保護ではない(それはウォッチドッグの役目)。
"""

import time

APRS_MAX_MS = 5000  # 送信時間の上限の既定(SAF-02 の APRS 5 秒、仮)


class TxTimeout(Exception):
    """送信が時間の上限までに終わらなかった"""


class TxGate:
    def __init__(self, pin, active_high=True, max_ms=APRS_MAX_MS):
        self._pin = pin
        self._on = 1 if active_high else 0
        self.max_ms = max_ms
        self.keyed = False
        self._t0 = 0
        self._limit = max_ms
        pin.value(1 - self._on)

    def key(self, limit_ms=None):
        """PTT を入れる。limit_ms: 今回の送信の見込み(max_ms を超える分は切り詰める)"""
        self._limit = self.max_ms if limit_ms is None else min(limit_ms, self.max_ms)
        self._t0 = time.ticks_ms()
        self.keyed = True
        self._pin.value(self._on)

    def expired(self):
        return self.keyed and time.ticks_diff(time.ticks_ms(), self._t0) >= self._limit

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
