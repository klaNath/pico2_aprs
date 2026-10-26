# PIO のアセンブラ(jmp, mov など)は rp2.asm_pio が関数の中に注入するので未定義の名前の検査を外す
# ruff: noqa: F821
# pyright: reportUndefinedVariable=false
"""455 kHz の FM IF(リミッタの出力の方形波)をディジタル入力で受ける受信器(ADC の代わり)。

    IfRing      PIO が立ち上がりエッジ M 個の区間の長さを数え、DMA が 16 bit のリングに書き続ける
    IfReceiver  Core1 で区間の長さを 19.2 kHz の音声サンプルに直し(viper)、hw.afsk_rx.Receiver と
                同じ 2 つの復調器とデフレーマで復号する。start/stop/close/get は Receiver と同じ

区間の長さ(クロック数)cyc から音声の値: 周波数は M * clk / cyc なので、中心のまわりで一次近似して
    v = 2048 + (平均の cyc - cyc) * k         (割り算なし。偏移 +/-0.8% で誤差はその 0.8% 程度)
平均の cyc はゆっくり追う(時定数 4096 区間 = 約 216 ms。搬送波のずれを打ち消す)。
19.2 kHz ちょうどの間隔に線形補間で並べ直す。時刻は「前の区間の中央」からの半クロック数で持つ。
補間の比率は、区間の中央の間隔の逆数(平均から求める。ブロックごとに 1 回だけ割り算)を掛けて出す。
455 kHz・M = 24 で区間は毎秒約 1.9 万個、リング 4096 個は約 216 ms 分。
"""

import time
from array import array

import micropython
import rp2
import uctypes
from machine import Pin, freq
from micropython import const

from hw.afsk_rx import Receiver
from modem.demod import PROFILE_HUM, PROFILE_NOISE, Demodulator
from tnc.hdlc import Deframer

M = 24  # 1 区間のエッジ数
IF_HZ = 455_000
_RING_BITS = const(13)  # 8 KB
_RING_BYTES = const(1 << _RING_BITS)
_RING_N = const(_RING_BYTES // 2)  # 区間の数
_WBLOCK = const(256)  # 1 回に処理する区間の数(約 13.5 ms、出力は約 260 サンプル)
_PIO0_BASE = const(0x50200000)
_RXF0 = const(0x20)
_DREQ_PIO0_RX0 = const(4)
# _if2audio の状態(array('i'))の添字
_E = const(0)  # 前の区間の中央から、その区間の終わりまで(半クロック)
_PV = const(1)  # 前の区間の値
_OT = const(2)  # 次に出すサンプルの時刻(前の区間の中央から、半クロック)
_MEAN = const(3)  # 区間の長さの平均 x 16(クロック)
_K = const(4)  # 区間の長さ 1 クロックあたりの値の変化 x 16
_STEP = const(5)  # 出力 1 サンプルの半クロック数(150 MHz / 19.2 kHz で 15625)
_C0 = const(6)  # 区間の長さの定数項 4 * M + 3(edge_win のコメント)
_RCP = const(7)  # 区間の中央の間隔の逆数 x 2^26


@rp2.asm_pio(autopush=True, push_thresh=32)
def edge_win():
    # 立ち上がりエッジ M 個(OSR の値 + 1)ごとに、その区間で x が減った数を ~x として出す。
    # x はループ 1 回(2 クロック)ごとに 1 減り、出すたびに 0xFFFFFFFF に戻す。区間の端はいつも
    # エッジを見つけた瞬間なので、区間を足すと全体の時間にちょうどなる(誤差が積み重ならない)。
    # 1 区間のクロック数: ループ 2 * (減った数)、High から Low へ 2 * M、普通の立ち上がり
    # 2 * (M - 1)、出す立ち上がり 5 → 2 * (減った数) + 4 * M + 3(window_cycles)
    # 減った数は 1 区間で約 3850 なので、DMA は FIFO の下位 16 bit だけを取る(0xFFFF - 減った数)
    pull(block)
    wrap_target()
    mov(x, invert(null))
    mov(y, osr)
    label("hi")
    jmp(pin, "hi_dec")
    jmp("lo")
    label("hi_dec")
    jmp(x_dec, "hi")
    label("lo")
    jmp(pin, "rise")
    jmp(x_dec, "lo")
    label("rise")
    jmp(y_dec, "hi")
    in_(x, 32)
    wrap()


def window_cycles(d, m=M):
    """M エッジの区間で x が減った数 -> 区間のクロック数(edge_win のコメント)"""
    return 2 * d + 4 * m + 3


@micropython.viper
def _if2audio(ring: ptr16, off: int, rd: int, n: int, mask: int, st: ptr32, out: ptr16) -> int:
    """リングの rd から n 個の区間 -> out に 19.2 kHz の値。出したサンプル数を返す。
    out は n * 2 + 2 個以上(区間の長さは出力の間隔とほぼ同じなので、1 区間で出るのは 1 個前後)"""
    e = int(st[_E])
    pv = int(st[_PV])
    ot = int(st[_OT])
    mean = int(st[_MEAN])
    k = int(st[_K])
    step = int(st[_STEP])
    c0 = int(st[_C0])
    rcp = int(st[_RCP])
    no = 0
    i = 0
    while i < n:
        cyc = 2 * (0xFFFF - int(ring[off + ((rd + i) & mask)])) + c0
        mean += (cyc * 16 - mean) >> 12
        v = 2048 + (((mean - cyc * 16) * k) >> 8)
        if v < 0:  # 状態に負の値を残さない(ptr32 の読み出しは符号なし)
            v = 0
        elif v > 4095:
            v = 4095
        c = e + cyc  # この区間の中央(前の区間の中央から、半クロック)
        while ot <= c:
            s = pv + (((v - pv) * ((ot * rcp) >> 10)) >> 16)
            if s < 0:
                s = 0
            elif s > 4095:
                s = 4095
            out[no] = s
            no += 1
            ot += step
        ot -= c  # この区間の中央を基準にし直す
        e = cyc
        pv = v
        i += 1
    st[_E] = e
    st[_PV] = pv
    st[_OT] = ot
    st[_MEAN] = mean
    return no


class IfRing:
    """PIO(edge_win)+ DMA で、区間の長さを 16 bit のリングへ書き続ける。AdcRing と同じ形"""

    def __init__(self, pin=8, m=M, sm_id=0):
        self.pin = Pin(pin, Pin.IN, Pin.PULL_DOWN)  # 未接続のとき浮かないように
        self.m = m
        self.buf = array("H", bytes(2 * _RING_BYTES))  # リングをその大きさの境界に揃えるため 2 倍
        base = uctypes.addressof(self.buf)
        self.addr = (base + _RING_BYTES - 1) & ~(_RING_BYTES - 1)
        self.off = (self.addr - base) // 2
        self.n = _RING_N
        # Pico 2 W では無線チップの通信にも PIO を使うので、組み込むときは sm_id を確かめる
        self.sm = rp2.StateMachine(sm_id, edge_win, jmp_pin=self.pin, in_base=self.pin)
        self._sm_id = sm_id
        self.dma = rp2.DMA()

    def start(self):
        pio = self._sm_id // 4
        ctrl = self.dma.pack_ctrl(size=1, inc_read=False, inc_write=True, ring_sel=True,
                                  ring_size=_RING_BITS,
                                  treq_sel=_DREQ_PIO0_RX0 + pio * 8 + self._sm_id % 4)
        # count の上位 4 bit = 0xF は RP2350 の「無限転送」モード。16 bit: FIFO の下位半分
        self.dma.config(read=_PIO0_BASE + pio * 0x100000 + _RXF0 + 4 * (self._sm_id % 4),
                        write=self.addr, count=0xF0000001, ctrl=ctrl)
        self.dma.active(1)
        self.sm.put(self.m - 1)
        self.sm.active(1)

    def stop(self):
        self.sm.active(0)
        self.dma.active(0)

    def position(self):
        """リング内の次の書き込み位置(0..n-1)"""
        w = self.dma.write  # type: ignore[attr-defined]  (スタブに無いが実機にある)
        return ((w - self.addr) & (_RING_BYTES - 1)) >> 1

    def overflowed(self):
        return False  # PIO の FIFO が溢れたかは見ていない(DMA は 1 区間 52 us に 1 回で十分速い)

    def close(self):
        self.stop()
        self.dma.close()
        rp2.PIO(self._sm_id // 4).remove_program(edge_win)


class IfReceiver(Receiver):
    """Receiver の入力を ADC から IF に替えたもの(start/stop/close/get は Receiver と同じ)。

    k: 区間の長さ 1 クロックあたりの値の変化 x 16(既定 144 = 9 倍。偏移 +/-3 kHz で約 +/-540)
    """

    def __init__(self, pin=8, fs=19200, nslots=8, m=M, k=144, sm_id=0):
        self.ring = IfRing(pin, m, sm_id)
        self.chains = [
            (Demodulator(fs, max_block=2 * _WBLOCK + 2, **PROFILE_NOISE), Deframer(nslots)),
            (Demodulator(fs, max_block=2 * _WBLOCK + 2, **PROFILE_HUM), Deframer(nslots)),
        ]
        self.running = False
        self.stopped = True
        self.lag_max = 0
        self.overruns = 0
        self.samples = 0
        self._recent = []
        self.busy_us = 0  # Core1 で処理していた時間の合計(負荷率の計測用、small int のうちに回す)
        self.out = array("H", bytes(2 * (2 * _WBLOCK + 2)))
        clk = freq()
        cyc = m * clk // IF_HZ  # 区間の長さの見込み(平均の初期値)
        self.st = array("i", [0] * 8)
        self.st[_E] = cyc
        self.st[_PV] = 2048
        self.st[_MEAN] = cyc * 16
        self.st[_K] = k
        self.st[_STEP] = 2 * clk // fs
        self.st[_C0] = 4 * m + 3

    def _loop(self):
        ring = self.ring
        buf = ring.buf
        off = ring.off
        n = ring.n
        mask = n - 1
        st = self.st
        out = self.out
        chains = self.chains
        rd = ring.position()
        try:
            while self.running:
                wr = ring.position()
                avail = (wr - rd) & mask
                if avail > self.lag_max:
                    self.lag_max = avail
                if avail > n - _WBLOCK:  # 追いつけない: 1 周分捨てて現在位置から再開
                    self.overruns += 1
                    rd = wr
                    continue
                if avail < _WBLOCK // 2:
                    time.sleep_ms(5)
                    continue
                k = min(avail, _WBLOCK)
                t0 = time.ticks_us()
                st[_RCP] = (1 << 26) // (st[_MEAN] >> 3)  # 中央の間隔 = 平均の 2 倍(半クロック)
                no = _if2audio(buf, off, rd, k, mask, st, out)  # type: ignore[arg-type]  (viper)
                for dem, dfr in chains:
                    nb = dem.process(out, no, 0)
                    dfr.feed(dem.bits, nb)
                self.busy_us = (self.busy_us + time.ticks_diff(time.ticks_us(), t0)) & 0x3FFFFFFF
                rd = (rd + k) & mask
                self.samples = (self.samples + no) & 0x3FFFFFFF
        finally:
            self.stopped = True
