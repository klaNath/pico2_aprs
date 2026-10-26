# PIO のアセンブラ(jmp, mov など)は rp2.asm_pio が関数の中に注入するので未定義の名前の検査を外す
# ruff: noqa: F821
# pyright: reportUndefinedVariable=false
"""455 kHz の FM IF(リミッタの出力の方形波)をディジタル入力で受ける受信器(ADC の代わり)。

    IfRing      PIO が立ち上がりエッジ M 個の区間の長さを数え、DMA が 16 bit のリングに書き続ける
    IfReceiver  Core1 で区間の長さを 19.2 kHz の音声サンプルに直し(viper)、hw.afsk_rx.Receiver と
                同じ 2 つの復調器とデフレーマで復号する。start/stop/close/get は Receiver と同じ

周波数の出し方は 2 通り(IfReceiver の method):
    "pll"   DPLL(M = 8、_pll)。既定。余分・欠けたエッジ(弱い信号でリミッタの出力が乱れる)に強い
    "count" 周期の逆数計数(M = 24、_if2audio)
比較の実験は src/fm_if_pll_test.py(ソフトで作った信号)と src/fm_if_rt_test.py(実信号)。

周期の逆数計数: 区間の長さ(クロック数)cyc から音声の値。周波数は M * clk / cyc なので、
中心のまわりで一次近似して
    v = 2048 + (平均の cyc - cyc) * k         (割り算なし。偏移 +/-0.8% で誤差はその 0.8% 程度)
平均の cyc はゆっくり追う(時定数 4096 区間 = 約 216 ms。搬送波のずれを打ち消す)。平均は 4096 倍の
累積値 acc で持ち acc += cyc - (acc >> 12) とする((差) >> 12 で平均を直接動かすと、切り捨てで
下へ偏り、差が小さいと動かない不感帯ができる)。
19.2 kHz ちょうどの間隔に線形補間で並べ直す。時刻は「前の区間の中央」からの半クロック数で持つ。
補間の比率は、区間の中央の間隔の逆数(平均から求める。ブロックごとに 1 回だけ割り算)を掛けて出す。
455 kHz・M = 24 で区間は毎秒約 1.9 万個、リング 4096 個は約 216 ms 分。

DPLL: 位相は 1 周期 = 2^32 の整数。M 周期進むと M * 2^32 = 0(32 bit で折り返す)なので、区間の
終わりの NCO の位相 e = ph + f * cyc がそのまま位相誤差になる(割り算なし。エッジを 1 つ数え違えても
誤差は 1 周期 = 0 なので、数え違いの影響を受けない)。
    c = e >> KP、ph <- e - c(比例: 位相で補正)、f <- f - (e >> KI)(積分)
    出力 u = f - c / cyc(NCO の制御の値の全体。積分の項 f だけだと高い音が落ちる)
    |e| > 1/4 周期のときは補正しない
u を軽く低域通過し(シフト LPS)、区間の終わりの値の間を直線で結んで 19.2 kHz ごとに読み出す。
割り算は見込みの区間の長さから求めた逆数で済ませる。KP 1、KI 16、LPS 1(M = 8、更新 56.9 kHz)で
周波数特性 300 Hz-3 kHz が +/-1.5 dB。M = 8 で区間は毎秒約 5.7 万個、リング 8192 個は約 144 ms 分。
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

M = 24  # 周期の逆数計数の 1 区間のエッジ数
M_PLL = 8  # DPLL の 1 区間のエッジ数
IF_HZ = 455_000
_OUT_MAX = const(2 * 256 + 2)  # 1 回の処理で出すサンプルの上限(約 260 + 余裕)
_PIO0_BASE = const(0x50200000)
_RXF0 = const(0x20)
_DREQ_PIO0_RX0 = const(4)
# _if2audio の状態(array('i'))の添字
_E = const(0)  # 前の区間の中央から、その区間の終わりまで(半クロック)
_PV = const(1)  # 前の区間の値
_OT = const(2)  # 次に出すサンプルの時刻(前の区間の中央から、半クロック)
_MEAN = const(3)  # 区間の長さの平均 x 4096(クロック。累積値)
_K = const(4)  # 区間の長さ 1 クロックあたりの値の変化 x 16
_STEP = const(5)  # 出力 1 サンプルの半クロック数(150 MHz / 19.2 kHz で 15625)
_C0 = const(6)  # 区間の長さの定数項 4 * M + 3(edge_win のコメント)
_RCP = const(7)  # 区間の中央の間隔の逆数 x 2^26
# _pll の状態(array('i'))の添字
_PPH = const(0)  # NCO の位相(1 周期 = 2^32)
_PF = const(1)  # NCO の周波数(積分の項。1 クロックあたりの位相)
_PLP = const(2)  # 出力の低域通過
_PMN = const(3)  # 直流(出力の f0 からのずれの平均 x 4096)
_POT = const(4)  # 次に出すサンプルまで(半クロック。0 以下になったら出す)
_PC0 = const(5)  # 区間の長さの定数項 4 * M + 3
_PKP = const(6)
_PKI = const(7)
_PST = const(8)  # 出力 1 サンプルの半クロック数
_PVS = const(9)  # 出力の値へのシフト(3 kHz の偏移で約 +/-670)
_PF0 = const(10)  # 搬送波の周波数の見込み(出力の基準)
_PXP = const(11)  # 前の区間の終わりの値(補間用)
_PRCP = const(12)  # 区間の長さ(半クロック)の逆数 x 2^26(補間用)
_PLPS = const(13)  # 出力の低域通過のシフト(0 = なし)
_PRW = const(14)  # 区間の長さ(クロック)の逆数 x 2^24(比例の補正を周波数に直す)
_GATE = const(0x3FFFFFFF)  # 位相誤差のゲート: 1/4 周期(1 << 30 は small int に入らない)


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
        mean += cyc - (mean >> 12)
        v = 2048 + ((((mean >> 8) - cyc * 16) * k) >> 8)
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


@micropython.viper
def _pll(ring: ptr16, off: int, rd: int, n: int, mask: int, st: ptr32, out: ptr16) -> int:
    """リングの rd から n 個の区間(M = 8)-> DPLL -> out に 19.2 kHz の値。出したサンプル数を返す。
    out は _OUT_MAX 個(1 回に渡す区間は 768 個 = 約 13.5 ms まで)"""
    ph = int(st[_PPH])
    f = int(st[_PF])
    lp = int(st[_PLP])
    mean = int(st[_PMN])
    ot = int(st[_POT])
    c0 = int(st[_PC0])
    kp = int(st[_PKP])
    ki = int(st[_PKI])
    step = int(st[_PST])
    vs = int(st[_PVS])
    f0 = int(st[_PF0])
    xp = int(st[_PXP])
    rcp = int(st[_PRCP])
    lps = int(st[_PLPS])
    rcpw = int(st[_PRW])
    no = 0
    i = 0
    while i < n:
        cyc = 2 * (0xFFFF - int(ring[off + ((rd + i) & mask)])) + c0
        e = ph + f * cyc  # 32 bit で折り返す: M 周期 = 0
        if e > _GATE or e < 0 - _GATE:
            ph = e  # 外れ値: 補正しない(次の区間の端で戻る)
            u = f
        else:
            f -= e >> ki
            c = e >> kp  # 比例の補正(位相)
            ph = e - c
            u = f - (((c >> 12) * rcpw) >> 12)  # 制御の値の全体 = f - c / cyc
        if lps:
            lp += (u - lp) >> lps
        else:
            lp = u
        x = lp - f0
        mean += x - (mean >> 12)  # 直流(時定数 4096 区間 = 72 ms)
        dc = mean >> 12
        ot -= 2 * cyc
        while ot <= 0:
            # 出力の時刻はこの区間の頭から 2 * cyc + ot(半クロック)。
            # 前の区間の終わりの値と直線で結ぶ
            fr = ((2 * cyc + ot) * rcp) >> 14  # 0..4096
            if fr > 4096:
                fr = 4096
            v = 2048 + (((xp + (((x - xp) * fr) >> 12)) - dc) >> vs)
            if v < 0:
                v = 0
            elif v > 4095:
                v = 4095
            if no < _OUT_MAX:
                out[no] = v
                no += 1
            ot += step
        xp = x
        i += 1
    st[_PPH] = ph
    st[_PF] = f
    st[_PLP] = lp
    st[_PMN] = mean
    st[_POT] = ot
    st[_PXP] = xp
    return no


class IfRing:
    """PIO(edge_win)+ DMA で、区間の長さを 16 bit のリングへ書き続ける。AdcRing と同じ形。
    ring_bits: リングのバイト数の 2 のべき(13 = 4096 区間、14 = 8192 区間)"""

    def __init__(self, pin=8, m=M, sm_id=0, ring_bits=13):
        self.pin = Pin(pin, Pin.IN, Pin.PULL_DOWN)  # 未接続のとき浮かないように
        self.m = m
        self.ring_bits = ring_bits
        self.ring_bytes = 1 << ring_bits
        # リングをその大きさの境界に揃えるため 2 倍確保する
        self.buf = array("H", bytes(2 * self.ring_bytes))
        base = uctypes.addressof(self.buf)
        self.addr = (base + self.ring_bytes - 1) & ~(self.ring_bytes - 1)
        self.off = (self.addr - base) // 2
        self.n = self.ring_bytes // 2
        # Pico 2 W では無線チップの通信にも PIO を使うので、組み込むときは sm_id を確かめる
        self.sm = rp2.StateMachine(sm_id, edge_win, jmp_pin=self.pin, in_base=self.pin)
        self._sm_id = sm_id
        self.dma = rp2.DMA()

    def start(self):
        pio = self._sm_id // 4
        ctrl = self.dma.pack_ctrl(size=1, inc_read=False, inc_write=True, ring_sel=True,
                                  ring_size=self.ring_bits,
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
        return ((w - self.addr) & (self.ring_bytes - 1)) >> 1

    def overflowed(self):
        return False  # PIO の FIFO が溢れたかは見ていない(DMA は 1 区間 52 us に 1 回で十分速い)

    def close(self):
        self.stop()
        self.dma.close()
        rp2.PIO(self._sm_id // 4).remove_program(edge_win)


class IfReceiver(Receiver):
    """Receiver の入力を ADC から IF に替えたもの(start/stop/close/get は Receiver と同じ)。

    method: "pll"(DPLL、M = 8)または "count"(周期の逆数計数、M = 24)
    k: count の区間の長さ 1 クロックあたりの値の変化 x 16
       (既定 144 = 9 倍。偏移 +/-3 kHz で約 +/-540)
    kp, ki, lps: pll のループの定数と出力の低域通過(モジュールのコメント)
    """

    def __init__(self, pin=8, fs=19200, nslots=8, method="pll", k=144, kp=1, ki=16, lps=1,
                 sm_id=0):
        self.method = method
        pll = method == "pll"
        m = M_PLL if pll else M
        # どちらも 1 回に約 13.5 ms 分を処理する(出力は約 260 サンプル)
        self.wblock = 768 if pll else 256
        self.ring = IfRing(pin, m, sm_id, 14 if pll else 13)
        self.chains = [
            (Demodulator(fs, max_block=_OUT_MAX, **PROFILE_NOISE), Deframer(nslots)),
            (Demodulator(fs, max_block=_OUT_MAX, **PROFILE_HUM), Deframer(nslots)),
        ]
        self.running = False
        self.stopped = True
        self.lag_max = 0
        self.overruns = 0
        self.samples = 0
        self._recent = []
        self.busy_us = 0  # Core1 で処理していた時間の合計(負荷率の計測用、small int のうちに回す)
        self.out = array("H", bytes(2 * _OUT_MAX))
        clk = freq()
        cyc = m * clk // IF_HZ  # 区間の長さの見込み
        if pll:
            st = array("i", [0] * 15)
            st[_PF] = (IF_HZ << 32) // clk
            st[_PLP] = st[_PF]
            st[_PF0] = st[_PF]
            st[_PC0] = 4 * m + 3
            st[_PKP] = kp
            st[_PKI] = ki
            st[_PST] = 2 * clk // fs
            st[_PVS] = 7
            st[_PRCP] = (1 << 26) // (2 * cyc)
            st[_PLPS] = lps
            st[_PRW] = (1 << 24) // cyc
        else:
            st = array("i", [0] * 8)
            st[_E] = cyc
            st[_PV] = 2048
            st[_MEAN] = cyc * 4096
            st[_K] = k
            st[_STEP] = 2 * clk // fs
            st[_C0] = 4 * m + 3
        self.st = st

    def _loop(self):
        ring = self.ring
        buf = ring.buf
        off = ring.off
        n = ring.n
        mask = n - 1
        st = self.st
        out = self.out
        chains = self.chains
        wblock = self.wblock
        pll = self.method == "pll"
        rd = ring.position()
        try:
            while self.running:
                wr = ring.position()
                avail = (wr - rd) & mask
                if avail > self.lag_max:
                    self.lag_max = avail
                if avail > n - wblock:  # 追いつけない: 1 周分捨てて現在位置から再開
                    self.overruns += 1
                    rd = wr
                    continue
                if avail < wblock // 2:
                    time.sleep_ms(5)
                    continue
                k = min(avail, wblock)
                t0 = time.ticks_us()
                if pll:
                    no = _pll(buf, off, rd, k, mask, st, out)  # type: ignore[arg-type]  (viper)
                else:
                    # 区間の中央の間隔 = 平均の 2 倍(半クロック)
                    st[_RCP] = (1 << 26) // (st[_MEAN] >> 11)
                    no = _if2audio(buf, off, rd, k, mask, st, out)  # type: ignore[arg-type]  (viper)
                for dem, dfr in chains:
                    nb = dem.process(out, no, 0)
                    dfr.feed(dem.bits, nb)
                self.busy_us = (self.busy_us + time.ticks_diff(time.ticks_us(), t0)) & 0x3FFFFFFF
                rd = (rd + k) & mask
                self.samples = (self.samples + no) & 0x3FFFFFFF
        finally:
            self.stopped = True
