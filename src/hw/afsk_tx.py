"""AFSK 送信: PWM + DMA で音声を出し、PTT を制御する。

    音声サンプル(array('H')、PWM のデューティ値)を DMA で PWM の CC レジスタへ書き込む。
    DMA のペーシングは DMA タイマ(システムクロック x 2/15625 = 19200 Hz ちょうど)。
    CPU は送出中ほかの処理ができる(待ちは asyncio で行う)。

PWM の搬送波(146.5 kHz)は後段の 2 次 RC LPF で取り除く。
送信していない間は PWM のデューティを 0(ピンを Low に固定)にしてエッジを止める。
PWM のスイッチングは GND/基準電圧を通じて ADC に回り込み、受信の雑音になるため
(実測: 動作中は 4 シグマを超えるスパイクが 1.3%、止めると無くなる)。
送信開始時は中点に戻して出力の結合コンデンサが落ち着くのを待ってから PTT を入れ、
終了時は PTT を切ってから止めるので、直流の段差は送信音声に乗らない。
RP2350 の PWM レジスタ: ベース 0x400A8000、スライスごとに 0x14 バイト、CC は +0x0C
(CC の下位 16 bit が A、上位が B。両方に同じ値を書くので、出力ピンは A でも B でもよい)
(dev.py exec で machine.PWM の設定値が読み出せることを確認済み)。
"""

import asyncio
import time
from array import array

import rp2
from machine import PWM, Pin, mem32
from micropython import const

from tnc.afsk_mod import Modulator
from tnc.hdlc import encode_levels, levels_size

_PWM_BASE = const(0x400A8000)
_PWM_STRIDE = const(0x14)
_PWM_CC = const(0x0C)
_PWM_TOP = const(0x10)
_DMA_SIZE_16 = const(1)


class AfskTx:
    def __init__(self, pin_audio, pin_ptt, fs=19200, pwm_top=1023, amplitude=0.4,
                 txdelay_ms=300, txtail_ms=30, ptt_active_high=True, settle_ms=60):
        self.fs = fs
        self.top = pwm_top
        mid = (pwm_top + 1) // 2
        self.mod = Modulator(fs, mid=mid, amp=int(mid * amplitude))
        self.txdelay_ms = txdelay_ms
        self.txtail_ms = txtail_ms
        self.settle_ms = settle_ms  # 中点に戻してから PTT までの待ち(C3 1uF x 約 11k の 5 倍強)
        self._mid = mid
        self._ptt_on = 1 if ptt_active_high else 0
        self.ptt = Pin(pin_ptt, Pin.OUT, value=1 - self._ptt_on)
        self.pin_audio = pin_audio
        self.pwm = PWM(Pin(pin_audio), freq=150_000_000 // (pwm_top + 1), duty_u16=32768)
        self._slice = (pin_audio >> 1) & 7  # RP2350A: GPIO 0-15 / 16-29 -> slice 0-7
        self._cc = _PWM_BASE + self._slice * _PWM_STRIDE + _PWM_CC
        top_reg = _PWM_BASE + self._slice * _PWM_STRIDE + _PWM_TOP
        if mem32[top_reg] != pwm_top:  # machine.PWM の分周設定が想定と違えば止める
            raise RuntimeError("PWM TOP mismatch: %d" % mem32[top_reg])
        self._write_cc(0)  # 待機中はエッジを出さない
        self.timer = rp2.DMATimer(freq=fs)
        self.dma = rp2.DMA()
        self.busy = False

    def _write_cc(self, v):
        # A/B 両方に同じ値(DMA の 16 bit 書き込みも両半分に複製されるので揃える)
        mem32[self._cc] = v | (v << 16)

    def build(self, frames):
        """フレーム(FCS 込み bytes のリスト)-> 送出する音声サンプル array('H')"""
        pre = max(1, self.txdelay_ms * 1200 // 8000)  # フラグ 1 個 = 8 ビット
        post = max(1, self.txtail_ms * 1200 // 8000)
        n = 0
        for i, f in enumerate(frames):
            n += levels_size(len(f), pre if i == 0 else 2, post if i == len(frames) - 1 else 0)
        lv = bytearray(n)
        n = 0
        last = 0
        for i, f in enumerate(frames):
            k, last = encode_levels(f, memoryview(lv)[n:], pre if i == 0 else 2,
                                    post if i == len(frames) - 1 else 0, last)
            n += k
        out = array("H", bytes(2 * self.mod.samples_size(n)))
        self.mod.modulate(lv, n, out, 0)
        return out

    def start(self, samples, n=-1):
        """PWM を中点に戻して settle_ms 待ち、PTT を入れて DMA で送出を始める。"""
        if n < 0:
            n = len(samples)
        self._write_cc(self._mid)
        time.sleep_ms(self.settle_ms)
        self.dma.config(
            read=samples,
            write=self._cc,
            count=n,
            ctrl=self.dma.pack_ctrl(size=_DMA_SIZE_16, inc_write=False, treq_sel=self.timer),
        )
        self.busy = True
        self.ptt.value(self._ptt_on)
        self.dma.active(1)

    def finish(self):
        """送出が終わっていれば PTT を切って True。"""
        if self.dma.active():
            return False
        if self.busy:
            self._write_cc(self._mid)
            self.ptt.value(1 - self._ptt_on)  # 先に PTT を切ってから
            self._write_cc(0)  # PWM を止める(受信中にエッジを出さない)
            self.busy = False
        return True

    async def send(self, frames):
        samples = self.build(frames)
        self.start(samples)
        while not self.finish():
            await asyncio.sleep_ms(10)

    def send_blocking(self, frames):
        samples = self.build(frames)
        self.start(samples)
        while not self.finish():
            time.sleep_ms(5)

    def close(self):
        self.dma.active(0)
        self.ptt.value(1 - self._ptt_on)
        self.dma.close()
        self.timer.close()
        self.pwm.deinit()
