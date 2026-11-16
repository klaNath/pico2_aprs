"""AFSK 送信: PWM + DMA で音声を出し、ゲート(core.txgate)を通して PTT を入れる。

    音声サンプル(16 bit、PWM のデューティ値)を DMA で PWM の CC レジスタへ書き込む。
    DMA のペーシングは DMA タイマ(システムクロック x 2/15625 = 19200 Hz ちょうど)。
    CPU は送出中ほかの処理ができる(待ちは asyncio で行う)。
    送信波形のバッファ(max_ms 分)とレベル列のバッファは作るときに 1 回だけ確保し、使い回す。
    長く動かしてヒープが断片化しても、送信のたびに大きな連続領域を探さずに済む
    (ビーコン 1 回で約 26 KB。起動直後に確保すれば確実に取れる)。
    バッファは bytearray 1 つで、変調器には uctypes の 16 bit 配列として渡す。array('H', bytes(n))
    だと一時的な bytes と配列で同じ大きさの連続領域が 2 つ要り、断片化したヒープで
    MemoryError になった
    (uctypes でも書き込みの速さは同じ: 0.7 秒分で 105 ms、array は 114 ms)。

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

import rp2
import uctypes
from machine import PWM, Pin, mem32
from micropython import const

from core.txgate import TxRefused, TxTimeout
from modem.fx25 import Fx25Tx
from tnc.afsk_mod import Modulator
from tnc.hdlc import encode_levels, levels_size

_PWM_BASE = const(0x400A8000)
_PWM_STRIDE = const(0x14)
_PWM_CC = const(0x0C)
_PWM_TOP = const(0x10)
_DMA_SIZE_16 = const(1)
_DONE_MARGIN_MS = const(200)  # 波形の長さにこれを足しても終わらなければ DMA の異常とみなす


class AfskTx:
    def __init__(self, pin_audio, gate, fs=19200, pwm_top=1023, amplitude=0.4,
                 txdelay_ms=300, txtail_ms=30, settle_ms=60, max_ms=1500, raw=None, fx25=False):
        """gate: core.txgate のゲート(hw.txctl で作る)。音声だけなら AudioOnly。
        raw: 送信波形のバッファ(bytearray、1 サンプル 2 バイト)。
        None なら max_ms 分をここで確保する。
        fx25: True なら FX.25(RS(48,32) / (80,64) / (144,128)、チェック 16 バイト)で送る。
        内側が 128 バイトを超えるフレームは従来の AX.25 のまま"""
        self.fs = fs
        self.busy = False
        self.fx = None  # FX.25 の符号器(有効なとき)。None = 従来の AX.25
        self._fx = None  # 一度作った符号器(set_fx25 で切り替えても作り直さない)
        self.set_fx25(fx25)
        self.fx_used = 0  # 直近の build() で FX.25 にしたフレーム数
        self.top = pwm_top
        mid = (pwm_top + 1) // 2
        self.mod = Modulator(fs, mid=mid, amp=int(mid * amplitude))
        # 送信波形(PWM のデューティ値)とレベル列のバッファ。build() はここへ書く
        self.raw = raw if raw is not None else bytearray(2 * (fs * max_ms // 1000))  # DMA が読む
        self.nmax = len(self.raw) // 2  # 入るサンプル数
        self.buf = uctypes.struct(uctypes.addressof(self.raw),
                                  {"v": (uctypes.ARRAY | 0, uctypes.UINT16 | self.nmax)}).v
        self.lv = bytearray(self.nmax // self.mod.spb)
        self.txdelay_ms = txdelay_ms
        self.txtail_ms = txtail_ms
        self.settle_ms = settle_ms  # 中点に戻してから PTT までの待ち(C3 1uF x 約 11k の 5 倍強)
        self._mid = mid
        self.gate = gate
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

    def set_fx25(self, on):
        """FX.25 の有効/無効(実行中に切り替えられる。設定の変更で使う)。
        符号器は最初に有効にしたときに 1 回だけ確保する(約 1 KB)。送出中は RuntimeError"""
        if self.busy:
            raise RuntimeError("TX busy")
        if on and self._fx is None:
            self._fx = Fx25Tx()
        self.fx = self._fx if on else None

    def _write_cc(self, v):
        # A/B 両方に同じ値(DMA の 16 bit 書き込みも両半分に複製されるので揃える)
        mem32[self._cc] = v | (v << 16)

    def build(self, frames):
        """フレーム(FCS 込み bytes のリスト)の音声サンプルを self.raw に作り、サンプル数を返す。
        fx25 を有効にしてあれば、内側が 128 バイト以下のフレームは FX.25 で、それ以外は
        従来の AX.25 で作る(self.fx_used に FX.25 にしたフレーム数が入る)。
        メモリは確保しない。入りきらなければ ValueError、送出中なら RuntimeError"""
        if self.busy:
            raise RuntimeError("TX busy: the buffer is being sent")
        pre = max(1, self.txdelay_ms * 1200 // 8000)  # フラグ 1 個 = 8 ビット
        post = max(1, self.txtail_ms * 1200 // 8000)
        lv = memoryview(self.lv)
        cap = len(self.lv)
        fx = self.fx
        self.fx_used = 0
        n = 0
        last = 0
        nf = len(frames)
        for i, f in enumerate(frames):
            p = pre if i == 0 else 2
            q = post if i == nf - 1 else 0
            # prepare() が 0 なら対象外(内側が 128 バイト超): 従来の AX.25 で送る
            fxf = fx if fx is not None and fx.prepare(f) > 0 else None
            if fxf is not None:
                need = fxf.levels_size(p, q)
            else:
                need = levels_size(len(f), p, q)
            if need > cap - n:
                raise ValueError("frames too long for the TX buffer (%d > %d levels)"
                                 % (n + need, cap))
            if fxf is not None:
                k, last = fxf.write(lv[n:], p, q, last)
                self.fx_used += 1
            else:
                k, last = encode_levels(f, lv[n:], p, q, last)
            n += k
        self.mod.modulate(self.lv, n, self.buf, 0)
        return self.mod.samples_size(n)

    def start(self, samples=None, n=-1):
        """PWM を中点に戻して settle_ms 待ち、PTT を入れて DMA で送出を始める。
        samples を省くと self.raw(build() で作った n サンプル)を送る。"""
        if samples is None:
            samples = self.raw
        if n < 0:
            n = len(samples)
        self.gate.check()  # 条件がそろっていなければ TxRefused(PWM に触る前に)
        self._write_cc(self._mid)
        time.sleep_ms(self.settle_ms)
        self.dma.config(
            read=samples,
            write=self._cc,
            count=n,
            ctrl=self.dma.pack_ctrl(size=_DMA_SIZE_16, inc_write=False, treq_sel=self.timer),
        )
        self.busy = True
        try:
            self.gate.key(n * 1000 // self.fs + _DONE_MARGIN_MS)
        except TxRefused:  # 待っている間に条件が変わった
            self.busy = False
            self._write_cc(0)
            raise
        self.dma.active(1)

    def finish(self):
        """送出が終わっていれば PTT を切って True。
        時間の上限を過ぎても終わらなければ PTT を切って TxTimeout(ペーシングの停止などで
        DMA が終わらないとき、呼び出し側の待ちのループが抜けられるように)"""
        if self.busy and self.gate.expired():
            self.abort()
            raise TxTimeout("TX did not finish in time")
        if self.dma.active():
            return False
        if self.busy:
            self._write_cc(self._mid)
            self.gate.unkey()  # 先に PTT を切ってから
            self._write_cc(0)  # PWM を止める(受信中にエッジを出さない)
            self.busy = False
        return True

    def abort(self):
        """送信を今すぐやめる。何度呼んでもよい。最初に PTT を切る"""
        self.gate.unkey()
        self.dma.active(0)
        self._write_cc(0)
        self.busy = False

    async def send(self, frames):
        self.start(n=self.build(frames))
        while not self.finish():
            await asyncio.sleep_ms(10)

    def send_blocking(self, frames):
        self.start(n=self.build(frames))
        while not self.finish():
            time.sleep_ms(5)

    def close(self):
        self.gate.unkey()  # 最初に切る(以降で例外や Ctrl-C があっても PTT を残さない)
        self.dma.active(0)
        self.dma.close()
        self.timer.close()
        self.pwm.deinit()
