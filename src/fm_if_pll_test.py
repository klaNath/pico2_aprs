# viper の関数に array / memoryview を渡す(ptr16 / ptr32)。型の検査を外す
# pyright: reportArgumentType=false
"""IF の FM 復調の比較実験(ソフトで作った信号): 周期の逆数計数(今の方式)と DPLL。

    python tools/dev.py run src/fm_if_pll_test.py -t 600

ピンは使わない。AFSK と 1 kHz の音で 455 kHz を FM 変調(+/-3 kHz)したエッジの時刻を viper で作り、
乱れ(時刻の揺らぎ、余分なエッジ、欠けたエッジ)を加えて、PIO と同じ 2 クロック単位で量子化し、
M = 24 と M = 8 の区間に分ける。同じエッジの列から 2 つの方式で 19.2 kHz の音声を作り、比べる。
    A: 周期の逆数計数(hw.fm_if_rx._if2audio、M = 24)
    B: DPLL(M = 8 の区間ごとに更新、2 次のループ、位相誤差のゲート付き)
比べるもの: AFSK 3 フレームの復号数(局と同じ 2 つの復調器のどちらかで)と、1 kHz の音の S/N。

DPLL: 位相は 1 周期 = 2^32 の整数。M 周期進むと M * 2^32 = 0(32 bit で折り返す)なので、
区間の終わりの NCO の位相 e = ph + f * cyc がそのまま位相誤差になる(割り算なし)。
    c = e >> KP、ph <- e - c(比例: 位相で補正)、f <- f - (e >> KI)(積分)
    出力 u = f - c / cyc(NCO の制御の値の全体。積分の項 f だけだと高い音が落ちる)
    |e| > GATE(1/4 周期)のときは補正しない
u を軽く低域通過し、区間の終わりの値の間を直線で結んで 19.2 kHz ごとに読み出す。
割り算は見込みの区間の長さから求めた逆数で済ませる。
結果(2026-10-26、CFG_B = KP 1、KI 16、低域通過 1、補間、制御の値の全体):
周波数特性 300 Hz-3 kHz で +/-1.5 dB(A は 3 kHz で -1.3 dB)。3 kHz 以下の S/N は
乱れなし 43.6 dB(A 39.9)、揺らぎ 50 ns 24.0 dB(A 22.6)、余分・欠けたエッジ 1e-2 で
約 38 dB(A は復号できない)。AFSK はどの条件でも 3/3(A は余分・欠けたエッジで 0-1/3)。
"""

import gc
import math
import time
from array import array

import micropython
from machine import freq
from micropython import const

from hw.fm_if_rx import _if2audio
from modem.demod import PROFILE_HUM, PROFILE_NOISE, Demodulator
from tnc.afsk_mod import Modulator
from tnc.ax25 import DecodeError, Frame
from tnc.hdlc import Deframer, encode_levels, levels_size

# hw.fm_if_rx._if2audio の状態の添字(向こうは const なので import できない。同じ値)
A_E, A_PV, A_OT, A_MEAN, A_K, A_STEP, A_C0, A_RCP = range(8)
FS = 19200
FC = 455_000
HZ_PER_COUNT = 6  # 音声の値 1 あたりの偏移(振幅 500 で +/-3 kHz)
U = 65536  # 時刻の単位: 1 クロック = 65536
M_A = 24
M_B = 8
# DPLL: (KP, KI, 出力の低域通過のシフト 0 = なし, 補間 1 = する, 出力 1 = 制御の値の全体)
CFG_B = (1, 16, 1, 1, 1)  # 周波数特性 300 Hz-3 kHz で +/-1.5 dB 以内
GATE = 1 << 30  # 位相誤差のゲート: 1/4 周期
TEXTS = (
    "JK1BBJ-7>APZPI2,WIDE1-1:>PLL test 1",
    "JK1BBJ-7>APZPI2,WIDE1-1:!3546.06N/14001.91E[PLL test 2 0123456789",
    "JK1BBJ-7>APZPI2:>PLL test 3 ABCDEFGHIJKLMNOPQRSTUVWXYZ",
)
# (名前, 時刻の揺らぎ ns rms, 余分なエッジの確率, 欠けたエッジの確率)
CONDITIONS = (
    ("clean", 0, 0.0, 0.0),
    ("jitter 5ns", 5, 0.0, 0.0),
    ("jitter 20ns", 20, 0.0, 0.0),
    ("jitter 50ns", 50, 0.0, 0.0),
    ("extra 1e-4", 5, 1e-4, 0.0),
    ("extra 1e-3", 5, 1e-3, 0.0),
    ("extra 1e-2", 5, 1e-2, 0.0),
    ("missing 1e-3", 5, 0.0, 1e-3),
    ("missing 1e-2", 5, 0.0, 1e-2),
)

# _synth の引数 p(array('i'))の添字
_P0 = const(0)  # 搬送波の周期(1/65536 クロック)
_KPER = const(1)  # 音声の値 1 あたりの周期の変化(1/65536 クロック、周期は短くなる)
_TS = const(2)  # 音声 1 サンプルの長さ(1/65536 クロック)
_JIT = const(3)  # 揺らぎの係数(_synth のコメント)
_PEXT = const(4)  # 余分なエッジの確率 x 2^32(符号なしとして比べる)
_PMISS = const(5)  # 欠けたエッジの確率 x 2^32
_EXT = const(6)  # 余分なエッジの遅れの最大 / 256(1/65536 クロック)
_RNG = const(7)
_N24 = const(8)
_N8 = const(9)
_TN = const(10)  # ここからは区切りをまたいで持ち越す状態
_LB24 = const(11)
_LB8 = const(12)
_R24 = const(13)
_R8 = const(14)
_K24 = const(15)
_K8 = const(16)


@micropython.viper
def _synth(audio: ptr16, na: int, p: ptr32, o24: ptr16, c24: int, o8: ptr16, c8: int):
    """audio(19.2 kHz、2048 中心)で FM 変調した方形波の立ち上がりを作り、M = 24 と M = 8 の
    区間に分けて PIO(edge_win)と同じ形の値(0xFFFF - 減った数)を o24 / o8 に書く。
    時刻は 1/65536 クロック。区間の長さは 1 クロック単位で切り捨て、PIO と同じく必ず奇数にする
    (2 * d + 4M + 3。偶数なら 1 クロックを次の区間へ回すので、区間の合計は正しい時間のまま)。
    揺らぎ: 16 bit の一様乱数 4 個の和 s(標準偏差 37837)から ((s >> 8) * _JIT) >> 8"""
    p0 = int(p[_P0])
    kper = int(p[_KPER])
    ts = int(p[_TS])
    jit = int(p[_JIT])
    pext = uint(p[_PEXT])
    pmiss = uint(p[_PMISS])
    ext = int(p[_EXT])
    x = uint(p[_RNG])
    n24 = int(p[_N24])
    n8 = int(p[_N8])
    tn = int(p[_TN])  # 次の本来のエッジ(このサンプルの頭から)
    lb24 = int(p[_LB24])  # 前の区間の端(このサンプルの頭から、負)
    lb8 = int(p[_LB8])
    r24 = int(p[_R24])  # 前の区間の端の、格子からのはみ出し
    r8 = int(p[_R8])
    k24 = int(p[_K24])  # 区間の中のエッジ数
    k8 = int(p[_K8])
    i = 0
    while i < na:
        per = p0 - (int(audio[i]) - 2048) * kper
        while tn < ts:
            x ^= x << 13
            x ^= x >> 17
            x ^= x << 5
            miss = x < pmiss
            x ^= x << 13
            x ^= x >> 17
            x ^= x << 5
            extra = x < pext
            delay = int(x >> 24) * ext
            x ^= x << 13
            x ^= x >> 17
            x ^= x << 5
            s = int(x >> 16) + int(x & 0xFFFF) - 65536
            x ^= x << 13
            x ^= x >> 17
            x ^= x << 5
            s += int(x >> 16) + int(x & 0xFFFF) - 65536
            t = tn + (((s >> 8) * jit) >> 8)
            m = 0 if miss else 1
            while m < 3:
                if m == 2:
                    if not extra:
                        break
                    t += delay
                if m > 0:
                    k24 += 1
                    if k24 == 24:
                        k24 = 0
                        v = r24 + t - lb24
                        w = v >> 16
                        r24 = v & 0xFFFF
                        if (w & 1) == 0:
                            w -= 1
                            r24 += 0x10000
                        d = (w - 4 * 24 - 3) >> 1
                        if n24 < c24:
                            o24[n24] = 0xFFFF - d
                            n24 += 1
                        lb24 = t
                    k8 += 1
                    if k8 == 8:
                        k8 = 0
                        v = r8 + t - lb8
                        w = v >> 16
                        r8 = v & 0xFFFF
                        if (w & 1) == 0:
                            w -= 1
                            r8 += 0x10000
                        d = (w - 4 * 8 - 3) >> 1
                        if n8 < c8:
                            o8[n8] = 0xFFFF - d
                            n8 += 1
                        lb8 = t
                m += 1
                if m == 1 and miss:
                    m = 2  # 欠けたエッジのあとでも余分なエッジは作る
            tn += per
        tn -= ts
        lb24 -= ts
        lb8 -= ts
        i += 1
    p[_RNG] = x
    p[_N24] = n24
    p[_N8] = n8
    p[_TN] = tn
    p[_LB24] = lb24
    p[_LB8] = lb8
    p[_R24] = r24
    p[_R8] = r8
    p[_K24] = k24
    p[_K8] = k8


# _pll の状態(array('i'))の添字
_PH = const(0)
_F = const(1)
_LP = const(2)
_MN = const(3)
_OT = const(4)
_C0B = const(5)
_KPB = const(6)
_KIB = const(7)
_STB = const(8)
_GT = const(9)
_VS = const(10)
_SKIP = const(11)
_F0 = const(12)
_XP = const(13)  # 前の区間の終わりの値(補間用)
_RCPB = const(14)  # 区間の長さ(半クロック)の逆数 x 2^26(補間用。見込みの長さから)
_LPS = const(15)  # 出力の低域通過のシフト(0 = なし)
_INTP = const(16)  # 1 = 区間の間を補間する、0 = 区間の終わりの値のまま読む
_OUTP = const(17)  # 出力: 0 = 積分の項 f だけ、1 = 制御の値の全体 f - (比例の補正 / 区間の長さ)
_RCPW = const(18)  # 区間の長さ(クロック)の逆数 x 2^24(比例の補正を周波数に直す)


@micropython.viper
def _pll(win: ptr16, n: int, st: ptr32, out: ptr16, cap: int) -> int:
    """M = 8 の区間 n 個 -> DPLL -> out に 19.2 kHz の値。出したサンプル数を返す"""
    ph = int(st[_PH])
    f = int(st[_F])
    lp = int(st[_LP])
    mean = int(st[_MN])
    ot = int(st[_OT])
    c0 = int(st[_C0B])
    kp = int(st[_KPB])
    ki = int(st[_KIB])
    step = int(st[_STB])
    gate = int(st[_GT])
    vs = int(st[_VS])
    skip = int(st[_SKIP])
    f0 = int(st[_F0])
    xp = int(st[_XP])
    rcp = int(st[_RCPB])
    lps = int(st[_LPS])
    intp = int(st[_INTP])
    outp = int(st[_OUTP])
    rcpw = int(st[_RCPW])
    no = 0
    i = 0
    while i < n:
        cyc = 2 * (0xFFFF - int(win[i])) + c0
        e = ph + f * cyc  # 32 bit で折り返す: M 周期 = 0
        if gate and (e > gate or e < 0 - gate):
            ph = e  # 外れ値: 補正しない(次の区間の端で戻る)
            skip += 1
            u = f
        else:
            f -= e >> ki
            c = e >> kp  # 比例の補正(位相)
            ph = e - c
            # NCO の制御の値の全体 = 積分の項 - 比例の補正を周波数に直したもの(c / 区間の長さ)
            u = f - ((((c >> 12) * rcpw) >> 12) if outp else 0)
        if lps:
            lp += (u - lp) >> lps
        else:
            lp = u
        x = lp - f0
        mean += x - (mean >> 12)  # 直流(x の平均 x 4096、時定数 4096 区間 = 72 ms)
        dc = mean >> 12
        ot -= 2 * cyc
        while ot <= 0:
            if intp:
                # 出力の時刻はこの区間の頭から 2 * cyc + ot(半クロック)。
                # 前の区間の終わりの値と直線で結ぶ
                fr = ((2 * cyc + ot) * rcp) >> 14  # 0..4096
                if fr > 4096:
                    fr = 4096
                xi = xp + (((x - xp) * fr) >> 12)
            else:
                xi = x
            v = 2048 + ((xi - dc) >> vs)
            if v < 0:
                v = 0
            elif v > 4095:
                v = 4095
            if no < cap:
                out[no] = v
                no += 1
            ot += step
        xp = x
        i += 1
    st[_PH] = ph
    st[_F] = f
    st[_LP] = lp
    st[_MN] = mean
    st[_OT] = ot
    st[_SKIP] = skip
    st[_XP] = x
    return no


def audio_afsk(frame):
    mod = Modulator(FS, mid=2048, amp=500)
    lv = bytearray(levels_size(len(frame), 40, 4))
    k, _ = encode_levels(frame, memoryview(lv), 40, 4, 0)
    a = array("H", bytes(2 * (mod.samples_size(k) + FS // 5)))
    pos = mod.silence(a, 0, FS // 10)
    pos = mod.modulate(lv, k, a, pos)
    pos = mod.silence(a, pos, FS // 10)
    return a, pos


def audio_tone(seconds=0.5, hz=1000):
    n = int(seconds * FS)
    return array("H", (int(2048 + 500 * math.sin(2 * math.pi * hz * i / FS)) for i in range(n))), n


CHUNK = 2048  # 1 回に流す音声のサンプル数(約 107 ms)


class Synth:
    """音声を区切りごとに渡すと、M = 24 と M = 8 の区間を返す(状態は持ち越す)"""

    def __init__(self, clk, jitter_ns, p_extra, p_miss, seed):
        p0 = clk * U // FC
        p = array("i", [0] * 17)
        p[_P0] = p0
        p[_KPER] = p0 * HZ_PER_COUNT // FC
        p[_TS] = clk * U // FS
        jit_u = jitter_ns * 1e-9 * clk * U  # 揺らぎの標準偏差(1/65536 クロック)
        p[_JIT] = int(jit_u * 65536 / 37837)  # ((s >> 8) * JIT) >> 8 = s * jit_u / 37837
        p[_PEXT] = int(p_extra * 4294967296)
        p[_PMISS] = int(p_miss * 4294967296)
        p[_EXT] = p0 // 10 // 256  # 余分なエッジは本来のエッジの 0.1 周期以内
        p[_RNG] = seed
        self.p = p
        edges = int(CHUNK / FS * (FC + 6000) * 1.05) + 8
        self.o24 = array("H", bytes(2 * (edges // 24 + 2)))
        self.o8 = array("H", bytes(2 * (edges // 8 + 2)))

    def run(self, audio, na):
        p = self.p
        p[_N24] = 0
        p[_N8] = 0
        _synth(audio, na, p, self.o24, len(self.o24), self.o8, len(self.o8))
        return p[_N24], p[_N8]


class DemodA:
    """周期の逆数計数(hw.fm_if_rx._if2audio、M = 24)。状態は持ち越す"""

    def __init__(self, clk):
        st = array("i", [0] * 8)
        cyc = M_A * clk // FC
        st[A_E] = cyc
        st[A_PV] = 2048
        st[A_MEAN] = cyc * 4096
        st[A_K] = 144
        st[A_STEP] = 2 * clk // FS
        st[A_C0] = 4 * M_A + 3
        self.st = st
        self.tmp = array("H", bytes(2 * (2 * 256 + 2)))

    def run(self, win, n, sink):
        st = self.st
        for j in range(0, n, 256):
            k = min(256, n - j)
            st[A_RCP] = (1 << 26) // (st[A_MEAN] >> 11)
            c = _if2audio(win, j, 0, k, 0xFFFFFFF, st, self.tmp)
            sink.put(self.tmp, c)


class DemodB:
    """DPLL(M = 8)。状態は持ち越す。cfg = (KP, KI, 出力の低域通過のシフト, 補間するか)"""

    def __init__(self, clk, cfg=None):
        kp, ki, lps, intp, outp = cfg or CFG_B
        st = array("i", [0] * 19)
        st[_F] = (FC << 32) // clk
        st[_LP] = st[_F]
        st[_F0] = st[_F]
        st[_C0B] = 4 * M_B + 3
        st[_KPB] = kp
        st[_KIB] = ki
        st[_STB] = 2 * clk // FS
        st[_GT] = GATE
        st[_VS] = 7
        st[_RCPB] = (1 << 26) // (2 * (M_B * clk // FC))
        st[_LPS] = lps
        st[_INTP] = intp
        st[_OUTP] = outp
        st[_RCPW] = (1 << 24) // (M_B * clk // FC)
        self.st = st
        self.tmp = array("H", bytes(2 * 1100))

    def run(self, win, n, sink):
        mv = memoryview(win)
        for j in range(0, n, 1024):
            k = min(1024, n - j)
            c = _pll(mv[j:], k, self.st, self.tmp, len(self.tmp))
            sink.put(self.tmp, c)

    def skipped(self):
        return self.st[_SKIP]


class Sink:
    """19.2 kHz の値を受けて、512 個ずつ 2 つの復調器で復号する。

    keep > 0 なら先頭から keep 個を残す(1 kHz の音の S/N を測るため)
    """

    def __init__(self, keep=0):
        self.chains = [(Demodulator(FS, max_block=512, **p), Deframer(8))
                       for p in (PROFILE_NOISE, PROFILE_HUM)]
        self.buf = array("H", bytes(2 * 512))
        self.n = 0
        self.got = []
        self.keep = array("H", bytes(2 * keep))
        self.nkeep = 0

    def put(self, src, c):
        for q in range(c):
            v = src[q]
            if self.nkeep < len(self.keep):
                self.keep[self.nkeep] = v
                self.nkeep += 1
            self.buf[self.n] = v
            self.n += 1
            if self.n == 512:
                self.flush()

    def flush(self):
        if not self.n:
            return
        for dem, dfr in self.chains:
            nb = dem.process(self.buf, self.n, 0)
            dfr.feed(dem.bits, nb)
            while True:
                m = dfr.get()
                if m is None:
                    break
                b = bytes(m)
                dfr.done()
                try:
                    Frame.decode(b)
                    self.got.append(b)
                except DecodeError:
                    pass
        self.n = 0


def run_both(audio, na, clk, cond, seed, keep=0, cfg=None, use_a=True):
    """audio を条件 cond で FM のエッジにし、2 つの方式で復調して Sink を 2 つ返す
    (use_a=False なら A は動かさず None)"""
    _, jit, pe, pm = cond
    syn = Synth(clk, jit, pe, pm, seed)
    da = DemodA(clk) if use_a else None
    db = DemodB(clk, cfg)
    sa = Sink(keep) if use_a else None
    sb = Sink(keep)
    mv = memoryview(audio)
    for off in range(0, na, CHUNK):
        k = min(CHUNK, na - off)
        n24, n8 = syn.run(mv[off:], k)
        if da is not None:
            da.run(syn.o24, n24, sa)
        db.run(syn.o8, n8, sb)
    if sa is not None:
        sa.flush()
    sb.flush()
    return sa, sb, db.skipped()


def _filtered(a, n, coef):
    """a[0:n] を 2 次の低域通過に通した値を 1 つずつ返す(配列に保存しない)。

    coef = None なら素通し
    """
    if coef is None:
        for i in range(n):
            yield float(a[i])
        return
    b0, a1, a2 = coef
    x1 = x2 = y1 = y2 = float(a[0])
    for i in range(n):
        x0 = float(a[i])
        y0 = b0 * (x0 + 2 * x1 + x2) - a1 * y1 - a2 * y2
        x2, x1, y2, y1 = x1, x0, y1, y0
        yield y0


def _fit(y, k0, n, hz, coef=None):
    """y[k0:n](coef があれば低域通過を通した値)に hz のサインを当てはめる
    -> (振幅, 残差の rms, 残差の最大)。値は 2 回、その場で作り直す"""
    m = n - k0
    s0 = sc = ss = 0.0
    for i, v in enumerate(_filtered(y, n, coef)):
        if i >= k0:
            w = 2 * math.pi * hz * i / FS
            s0 += v
            sc += v * math.cos(w)
            ss += v * math.sin(w)
    mean = s0 / m
    sc = 2 * sc / m
    ss = 2 * ss / m
    # 平均を引いた分の補正(cos / sin の和は 0 に近いが、区間が周期の整数倍とは限らない)
    r2 = 0.0
    peak = 0.0
    for i, v in enumerate(_filtered(y, n, coef)):
        if i >= k0:
            w = 2 * math.pi * hz * i / FS
            r = v - mean - sc * math.cos(w) - ss * math.sin(w)
            r2 += r * r
            if abs(r) > peak:
                peak = abs(r)
    return math.sqrt(sc * sc + ss * ss), math.sqrt(r2 / m), peak


def tone_snr(a, n, hz=1000, skip_s=0.1, band_hz=3000):
    """a[skip:n] に hz のサインを当てはめる。

    -> (S/N 全帯域 dB, S/N band_hz 以下 dB, 最大の外れ / 振幅)。
    帯域内は 2 次のバターワース低域通過(双一次変換)を通してから当てはめる"""
    k0 = int(skip_s * FS)
    amp, rms, peak = _fit(a, k0, n, hz)
    kk = math.tan(math.pi * band_hz / FS)
    nrm = 1 / (1 + math.sqrt(2) * kk + kk * kk)
    coef = (kk * kk * nrm, 2 * (kk * kk - 1) * nrm, (1 - math.sqrt(2) * kk + kk * kk) * nrm)
    amp2, rms2, _ = _fit(a, k0, n, hz, coef)

    def db(am, r):
        return 20 * math.log10(am / math.sqrt(2) / r) if r > 0 else 99.0

    return db(amp, rms), db(amp2, rms2), peak / amp


def main():
    clk = freq()
    frames = [bytes(Frame.parse(t).encode()) for t in TEXTS]
    print("A: pulse count M=%d   B: DPLL M=%d KP=%d KI=%d LP=%d interp=%d out=%d gate 1/4"
          % ((M_A, M_B) + CFG_B))
    print("%-13s | AFSK A  B | SNR full A  B | SNR <3k A  B | click A  B | B skipped" % "condition")
    for ci, cond in enumerate(CONDITIONS):
        ok = [0, 0]
        skipped = 0
        for fi, fr in enumerate(frames):
            audio, na = audio_afsk(fr)
            sa, sb, sk = run_both(audio, na, clk, cond, 0x1234567 + 97 * ci + fi)
            assert sa is not None
            ok[0] += fr in sa.got
            ok[1] += fr in sb.got
            skipped += sk
            del audio, sa, sb
            gc.collect()
        audio, na = audio_tone()
        sa, sb, sk = run_both(audio, na, clk, cond, 0x7654321 + ci, keep=na)
        assert sa is not None
        del audio
        snr_a, band_a, click_a = tone_snr(sa.keep, sa.nkeep)
        del sa
        gc.collect()
        snr_b, band_b, click_b = tone_snr(sb.keep, sb.nkeep)
        del sb
        gc.collect()
        print("%-13s |  %d/3 %d/3 | %5.1f %5.1f | %5.1f %5.1f |  %5.2f %5.2f | %d" % (
            cond[0], ok[0], ok[1], snr_a, snr_b, band_a, band_b, click_a, click_b, skipped + sk))


TUNE_CONDITIONS = (CONDITIONS[0], CONDITIONS[2], CONDITIONS[3], CONDITIONS[6])
TUNE_CONFIGS = (
    (1, 15, 0, 1, 0),  # 積分の項だけ(前回の最良)
    (1, 15, 0, 1, 1),
    (1, 16, 0, 1, 1),
    (2, 15, 0, 1, 1),
    (2, 16, 0, 1, 1),
    (2, 17, 0, 1, 1),
    (3, 16, 0, 1, 1),
    (3, 17, 0, 1, 1),
)


def response(cfgs, freqs=(300, 1000, 2200, 3000)):
    """周波数特性(1 kHz に対する dB)。cfg が None なら A(周期の逆数計数)"""
    clk = freq()
    print("cfg            " + " ".join("%6dHz" % f for f in freqs) + "   (dB rel. 1 kHz)")
    for cfg in cfgs:
        amps = []
        for hz in freqs:
            audio, na = audio_tone(0.5, hz)
            sa, sb, _ = run_both(audio, na, clk, CONDITIONS[0], 5, keep=na, cfg=cfg,
                                 use_a=cfg is None)
            snk = sa if cfg is None else sb
            assert snk is not None
            amps.append(_fit(snk.keep, int(0.1 * FS), snk.nkeep, hz)[0])
            del audio, sa, sb
            gc.collect()
        ref = amps[list(freqs).index(1000)]
        print("%-14s" % ("A" if cfg is None else "%d %d %d %d %d" % cfg)
              + " ".join("%8.2f" % (20 * math.log10(a / ref)) for a in amps))


def tune():
    """DPLL の定数を探す(B だけ動かす)。条件ごとに AFSK 3 フレームと 1 kHz の音"""
    clk = freq()
    frames = [bytes(Frame.parse(t).encode()) for t in TEXTS]
    print("DPLL tuning (M=%d): cfg = KP KI LP interp out" % M_B)
    print("%-11s | " % "cfg" + " | ".join("%-21s" % c[0] for c in TUNE_CONDITIONS))
    print("%-11s | " % "" + " | ".join("%-21s" % "AFSK full <3k" for _ in TUNE_CONDITIONS))
    for cfg in TUNE_CONFIGS:
        cells = []
        for ci, cond in enumerate(TUNE_CONDITIONS):
            ok = 0
            for fi, fr in enumerate(frames):
                audio, na = audio_afsk(fr)
                _, sb, _ = run_both(audio, na, clk, cond, 0x1234567 + 97 * ci + fi, cfg=cfg,
                                    use_a=False)
                ok += fr in sb.got
                del audio, sb
                gc.collect()
            audio, na = audio_tone()
            _, sb, _ = run_both(audio, na, clk, cond, 0x7654321 + ci, keep=na, cfg=cfg,
                                use_a=False)
            del audio
            full, band, _ = tone_snr(sb.keep, sb.nkeep)
            del sb
            gc.collect()
            cells.append("%d/3  %5.1f %5.1f" % (ok, full, band))
        print("%-11s | " % ("%d %d %d %d %d" % cfg) + " | ".join("%-21s" % c for c in cells))


if __name__ == "__main__":
    t0 = time.ticks_ms()
    main()
    print("done in %d s" % (time.ticks_diff(time.ticks_ms(), t0) // 1000))
