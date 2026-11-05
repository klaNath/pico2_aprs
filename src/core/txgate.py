"""送信の出口。PTT はここだけで操作する。ハードウェアに依存しないので PC でもテストできる。

ピンは偽物に差し替えられる(hw.txctl が本物のピンを渡す)。
    TxGate     条件がそろったときだけ PTT を入れる。既定はすべて拒否
    AudioOnly  音声(GP3)だけを出す試験用。PTT には触らない
送信の条件(SAF-01、RF-01):
    コールサインが設定されている / 送信が許可されている / 周波数が確定して帯域内にある
RF-04(使用区別の表)は V-02 の完了後に、refuse_reason() に加える。
順序(周波数源の出力は送信の間だけ有効にする):
    開始: 条件の確認(PLL のロックを含む)-> 送受切替 -> 送信クロック有効 -> PTT
    終了: PTT -> 送信クロック無効 -> 送受切替
入れている時間の上限(SAF-02)もここで持つ。expired() を見て止めるのは呼び出し側(hw.afsk_tx)。
ファームウェアが止まったときの保護ではない(それはウォッチドッグの役目)。
"""

import time

APRS_MAX_MS = 5000  # 送信時間の上限の既定(SAF-02 の APRS 5 秒、仮)
TX_MIN_HZ = 144_000_000  # RF-01
TX_MAX_HZ = 146_000_000
NO_CALL = "NOCALL"  # 未設定のときの表示と dry run のフレーム用。送信はしない


class TxTimeout(Exception):
    """送信が時間の上限までに終わらなかった"""


class TxRefused(Exception):
    """送信の条件がそろっていない"""


def valid_callsign(call):
    """形式だけを見る(英大文字と数字で 3〜6 文字、数字を含む)。SSID は含めない"""
    if not isinstance(call, str) or not 3 <= len(call) <= 6 or call == NO_CALL:
        return False
    digit = False
    for c in call:
        if "0" <= c <= "9":
            digit = True
        elif not "A" <= c <= "Z":
            return False
    return digit


def display_call(call, ssid=0):
    """表示とログの見出し用の自局名。未設定なら NO_CALL"""
    if not valid_callsign(call):
        call = NO_CALL
    return call if ssid == 0 else "%s-%d" % (call, ssid)


class UnsetFreq:
    """周波数源のスタブ(仮)。Si5351 のドライバができるまで、周波数は常に未確定とする。

    ドライバは同じ形にする:
        tx_hz()   確定した送信周波数(未確定なら None)
        locked()  送信 PLL がロックしているか(LOL_B = 0)
        tx_on() / tx_off()  送信クロックの出力を有効 / 無効にする
    ドライバへの注意:
        Pico のリセットでは Si5351 は初期化されず、設定と出力が残る。TxGate は作るときに tx_off() を
        呼ぶので、ここで I2C に書けるようにしておく
        CBPro の一覧は CLK_OEB = 0 で、そのまま書くと送信クロックも有効になる。0x0003 をそのまま書かない
        送信クロックの番号は固定しない(config.SI5351_TX_CLK)"""

    def tx_hz(self):
        return None

    def locked(self):
        return False

    def tx_on(self):
        pass

    def tx_off(self):
        pass


class FixedFreq:
    """無線機側で周波数を確定している、という明示(tx_demo の試験と、偽のピンのテスト用)"""

    def __init__(self, hz):
        self.hz = hz

    def tx_hz(self):
        return self.hz

    def locked(self):
        return True

    def tx_on(self):
        pass

    def tx_off(self):
        pass


class TxGate:
    def __init__(self, pin, active_high=True, callsign=None, allowed=False, freq=None, tr=None,
                 max_ms=APRS_MAX_MS):
        """callsign: 自局(SSID なし)。allowed: 送信の許可(config.TX_ENABLED)
        freq: tx_hz() を持つ周波数源(省略時は UnsetFreq = 常に拒否)
        tr: 送受切替(D-11 が未決なので差し込む形だけ)。tx() と rx() を持つ。None なら何もしない"""
        self._pin = pin
        self._on = 1 if active_high else 0
        self.callsign = callsign
        self.allowed = allowed
        self.freq = freq if freq is not None else UnsetFreq()
        self.tr = tr
        self.max_ms = max_ms
        self.keyed = False
        self._clk_fault = False
        self._t0 = 0
        self._limit = max_ms
        pin.value(1 - self._on)
        self.freq.tx_off()  # Pico のリセットでは周波数源の出力が残っている

    def refuse_reason(self):
        """送信できない理由の文字列。送信できるなら None"""
        if self._clk_fault:
            return "TX clock off failed"
        if not valid_callsign(self.callsign):
            return "callsign not set"
        if not self.allowed:
            return "TX not enabled"
        hz = self.freq.tx_hz()
        if hz is None:
            return "frequency not set"
        if not TX_MIN_HZ <= hz <= TX_MAX_HZ:
            return "frequency out of band"
        if not self.freq.locked():
            return "frequency source not locked"
        return None

    def check(self):
        why = self.refuse_reason()
        if why is not None:
            raise TxRefused(why)

    def key(self, limit_ms=None):
        """条件を確かめて PTT を入れる。そろっていなければ TxRefused で、ピンには触らない。
        limit_ms: 今回の送信の見込み(max_ms を超える分は切り詰める)"""
        self.check()
        self._limit = self.max_ms if limit_ms is None else min(limit_ms, self.max_ms)
        self.keyed = True  # 途中で例外になっても unkey() で戻せるように先に立てる
        try:
            if self.tr is not None:
                self.tr.tx()
            self.freq.tx_on()
        except BaseException:
            self.unkey()
            raise
        self._t0 = time.ticks_ms()
        self._pin.value(self._on)

    def expired(self):
        return self.keyed and time.ticks_diff(time.ticks_ms(), self._t0) >= self._limit

    def unkey(self):
        """PTT を切る。何度呼んでもよい。最初の行で切る(以降で例外が起きても切れているように)"""
        self._pin.value(1 - self._on)
        if self.keyed:
            self.keyed = False
            try:
                self.freq.tx_off()
            except OSError:  # 後始末を止めない。PTT は切れているが、以後の送信は拒否する
                self._clk_fault = True
            finally:
                if self.tr is not None:
                    self.tr.rx()


class _NoPin:
    def value(self, v=None):
        return 0


class AudioOnly(TxGate):
    """AfskTx に渡すと音声だけを出す。PTT のピンには一切触らない(hw.txctl が切ったままにする)"""

    def __init__(self):
        TxGate.__init__(self, _NoPin())

    def refuse_reason(self):
        return None  # PTT を入れないので、送信の条件は要らない
