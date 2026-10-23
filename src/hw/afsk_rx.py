"""AFSK 受信: ADC を DMA でリングバッファへ取り込み、Core1 で復調する。

    ADC(GP26 = ADC0)を 48MHz / 2500 = 19200 Hz ちょうどで連続変換し、
    DMA(リングモード、転送回数は無限)で 8 KB(4096 サンプル = 213 ms)のリングに書く。
    Core1 のスレッドがリングの書き込み位置を追いかけ、2 つの復調器(雑音向け/ハム向け)と
    デフレーマにかける。完成したフレームはデフレーマのスロットに入り、Core0 が get() で取る。

DMA のリングモードは「書き込みアドレスの下位ビットだけを回す」方式なので、
リングはその大きさの境界に揃っていなければならない(揃っていないと配列の外を壊す)。
そのため 2 倍の大きさを確保し、その中の境界に揃った部分を使う。

RP2350 の ADC レジスタ: ベース 0x400A0000、CS +0x00、FCS +0x08、FIFO +0x0C、DIV +0x10。
ADC の DREQ は 48。いずれも dev.py exec で確認済み(実測 19199.9 Hz)。
"""

import _thread
import time
from array import array

import rp2
import uctypes
from machine import ADC, Pin, mem32
from micropython import const

from modem.demod import PROFILE_HUM, PROFILE_NOISE, Demodulator
from tnc.hdlc import Deframer

_ADC = const(0x400A0000)
_CS = const(0x00)
_FCS = const(0x08)
_FIFO = const(0x0C)
_DIV = const(0x10)
_DREQ_ADC = const(48)
_ADC_CLK = const(48_000_000)

_RING_BITS = const(13)  # 8 KB
_RING_BYTES = const(1 << _RING_BITS)
_RING_N = const(_RING_BYTES // 2)  # サンプル数
_BLOCK = const(512)


class AdcRing:
    """ADC0..2 を DMA でリングバッファへ連続取り込みする。"""

    def __init__(self, pin=26, fs=19200):
        if _ADC_CLK % fs:
            raise ValueError("fs must divide 48 MHz")
        ch = pin - 26
        if not 0 <= ch <= 2:  # ADC3(GP29)は Pico 2 W で無線に使われるので使わない
            raise ValueError("ADC pin must be GP26..GP28")
        ADC(Pin(pin)).read_u16()  # パッドの初期化(デジタル入力無効・プル無し)を任せる
        self.buf = array("H", bytes(2 * _RING_BYTES))
        base = uctypes.addressof(self.buf)
        self.addr = (base + _RING_BYTES - 1) & ~(_RING_BYTES - 1)
        self.off = (self.addr - base) // 2  # buf の中でリングが始まる添字
        self.n = _RING_N
        self._fs = fs
        self._ch = ch
        self.dma = rp2.DMA()

    def start(self):
        mem32[_ADC + _CS] &= ~(1 << 3)  # START_MANY を止めてから設定
        mem32[_ADC + _DIV] = (_ADC_CLK // self._fs - 1) << 8
        # FIFO: 有効、DREQ 有効、1 サンプルで DREQ、UNDER/OVER をクリア
        mem32[_ADC + _FCS] = (1 << 24) | (1 << 3) | (1 << 0) | (3 << 10)
        ctrl = self.dma.pack_ctrl(size=1, inc_read=False, inc_write=True, ring_sel=True,
                                  ring_size=_RING_BITS, treq_sel=_DREQ_ADC)
        # count の上位 4 bit = 0xF は RP2350 の「無限転送」モード
        self.dma.config(read=_ADC + _FIFO, write=self.addr, count=0xF0000001, ctrl=ctrl)
        self.dma.active(1)
        cs = mem32[_ADC + _CS] & ~(0xF << 12)
        mem32[_ADC + _CS] = cs | (self._ch << 12) | (1 << 3) | 1

    def stop(self):
        mem32[_ADC + _CS] &= ~(1 << 3)
        time.sleep_ms(1)
        self.dma.active(0)

    def position(self):
        """リング内の次の書き込み位置(0..n-1)"""
        w = self.dma.write  # type: ignore[attr-defined]  (スタブに無いが実機にある)
        return ((w - self.addr) & (_RING_BYTES - 1)) >> 1

    def overflowed(self):
        """ADC の FIFO が溢れたか(DMA が追いつかなかった)。読むとクリアする。"""
        f = mem32[_ADC + _FCS]
        mem32[_ADC + _FCS] = f | (3 << 10)
        return bool(f & (1 << 11))

    def close(self):
        self.stop()
        self.dma.close()


class Receiver:
    """Core1 で ADC のリングを復調し、完成したフレームを Core0 に渡す。"""

    def __init__(self, pin=26, fs=19200, nslots=8):
        self.ring = AdcRing(pin, fs)
        self.chains = [
            (Demodulator(fs, max_block=_BLOCK, **PROFILE_NOISE), Deframer(nslots)),
            (Demodulator(fs, max_block=_BLOCK, **PROFILE_HUM), Deframer(nslots)),
        ]
        self.running = False
        self.stopped = True
        self.lag_max = 0  # 処理の遅れの最大(サンプル数)。リング長に近づいたら危険
        self.overruns = 0  # 処理が間に合わずにリング 1 周分以上を捨てた回数
        self.samples = 0
        self._recent = []  # 2 つの復調器から同じフレームが出たときの重複除去

    def start(self):
        self.running = True
        self.stopped = False
        self.ring.start()
        _thread.start_new_thread(self._loop, ())

    def stop(self):
        self.running = False
        while not self.stopped:
            time.sleep_ms(5)
        self.ring.stop()

    def close(self):
        if not self.stopped:
            self.stop()
        self.ring.close()

    def _loop(self):
        ring = self.ring
        buf = ring.buf
        off = ring.off
        n = ring.n
        chains = self.chains
        rd = ring.position()
        try:
            while self.running:
                wr = ring.position()
                avail = (wr - rd) % n
                if avail > self.lag_max:
                    self.lag_max = avail
                if avail > n - _BLOCK:  # 追いつけない: 1 周分捨てて現在位置から再開
                    self.overruns += 1
                    rd = wr
                    continue
                if avail < _BLOCK // 2:
                    time.sleep_ms(5)
                    continue
                k = min(avail, _BLOCK, n - rd)  # リングの終端で折り返す
                for dem, dfr in chains:
                    nb = dem.process(buf, k, off + rd)
                    dfr.feed(dem.bits, nb)
                rd = (rd + k) % n
                self.samples = (self.samples + k) & 0x3FFFFFFF  # small int のまま回す(確保なし)
        finally:
            self.stopped = True

    def get(self, now_ms=None):
        """完成したフレーム(FCS 込みの bytes)を 1 つ返す。無ければ None。
        2 つの復調器が同じフレームを出した場合は 1 回だけ返す(3 秒以内の同一内容)。"""
        if now_ms is None:
            now_ms = time.ticks_ms()
        for _dem, dfr in self.chains:
            while True:
                m = dfr.get()
                if m is None:
                    break
                b = bytes(m)
                dfr.done()
                if self._is_dup(b, now_ms):
                    continue
                return b
        return None

    def _is_dup(self, b, now_ms):
        self._recent = [(t, x) for t, x in self._recent if time.ticks_diff(now_ms, t) < 3000]
        for _t, x in self._recent:
            if x == b:
                return True
        self._recent.append((now_ms, b))
        return False
