"""The transmit exit. PTT is operated only here. Hardware independent, so it is testable on a PC.

Pins can be replaced with fakes (hw.txctl passes the real pins).
    TxGate     keys PTT only when all conditions hold. Refuses everything by default
    AudioOnly  test helper that outputs audio (GP3) only. Never touches PTT
Transmit conditions (no transmission without a callsign, none outside 144-146 MHz):
    callsign is set / transmitting is allowed / frequency is fixed and inside the band
External TNC mode (default, hw.txctl.tnc_gate) does not check the frequency (check_freq=False).
The radio decides frequency and emission type and the Pico cannot know them, so they are the
owner's responsibility. Si5351 mode (unfinished) checks the frequency.
A table of allowed frequencies per emission type (legal band usage and band plan) is to be
added to refuse_reason() only after its contents are checked against primary sources.
Order (the frequency source's output is enabled only while transmitting):
    start: check conditions (including the source's tx_ready) -> T/R switch -> TX clock on -> PTT
    end:   PTT -> TX clock off -> T/R switch
The key-down time limit (5 s per APRS transmission, docs/tx-time-limit.md) is also kept here.
The caller (hw.afsk_tx) watches expired() and stops.
This is not a protection against firmware hangs (that is the watchdog's job).
"""

import time

APRS_MAX_MS = 5000  # 送信時間の上限の既定(APRS のビーコン 1 回で 5 秒、仮)
TX_MIN_HZ = 144_000_000  # この範囲の外では送信しない
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
    """Frequency source stub (provisional). Until the Si5351 driver exists, the frequency is
    always unknown.

    Drivers take the same shape:
        tx_hz()   the fixed transmit frequency (None if not fixed)
        tx_ready()  whether the TX clock may be output (not necessarily PLL lock detection)
        tx_on() / tx_off()  enable / disable the TX clock output
    Notes for drivers:
        A Pico reset does not reset the Si5351; its settings and outputs remain. TxGate calls
        tx_off() when constructed, so I2C must be writable at that point
        The CBPro register list has CLK_OEB = 0, so writing it as is also enables the TX
        clock. Do not write 0x0003 as is
        Do not hard-code the TX clock number (config.SI5351_TX_CLK)"""

    def tx_hz(self):
        return None

    def tx_ready(self):
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

    def tx_ready(self):
        return True

    def tx_on(self):
        pass

    def tx_off(self):
        pass


class TxGate:
    def __init__(self, pin, active_high=True, callsign=None, allowed=False, freq=None, tr=None,
                 max_ms=APRS_MAX_MS, check_freq=True):
        """callsign: own callsign (without SSID). allowed: transmit permission (config.TX_ENABLED)
        freq: frequency source with tx_hz() (default UnsetFreq = always refuse)
        tr: T/R switch (only a plug-in point, since the antenna switching scheme is undecided).
            Has tx() and rx(). None does nothing
        check_freq: if False, do not check that the frequency is fixed and in band. For external
        TNC mode (the radio decides the frequency, which the Pico cannot know). The callsign,
        permission and time limit still apply the same way"""
        self._pin = pin
        self._on = 1 if active_high else 0
        self.callsign = callsign
        self.allowed = allowed
        self.check_freq = check_freq
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
        if not self.check_freq:
            return None
        hz = self.freq.tx_hz()
        if hz is None:
            return "frequency not set"
        if not TX_MIN_HZ <= hz <= TX_MAX_HZ:
            return "frequency out of band"
        if not self.freq.tx_ready():
            return "frequency source not ready"
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
