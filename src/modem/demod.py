"""Bell 202 (1200 baud AFSK) 復調器: 直交相関(スライディング DFT)+ トーン別 AGC + DPLL。

各サンプルで次を行う(整数演算のみ、1 ブロックを 1 回の viper 呼び出しで処理):
    1. 前段フィルタ: 2 次 IIR(biquad)を 2 段。既定は HPF 900Hz + LPF 2700Hz。
       DC・50Hz ハムとその高調波・帯域外の雑音を落とす
    2. マーク(1200Hz)/スペース(2200Hz)それぞれの I/Q 相関を 1 ビット長の窓で
       スライディング加算(1 サンプルあたり乗算 4 回)
    3. 振幅 |I|+|Q| の近似(max + 3/8 min)
    4. トーン別のピーク追従(AGC)で正規化して差をとる
       → FM 無線機のプリ/デエンファシスによるトーン間の振幅差(ツイスト)を打ち消す
    5. DPLL でビットの中央を推定してサンプリングし、NRZI を復号して 0/1 を出す

係数の設計(biquad、cos テーブル、AGC の時定数)は起動時に float で行い、整数に量子化する。
PC(CPython)でも同じコードが動く(tools/host_test.py が viper/ptr を素通しにする)。
PC では状態配列 array('i') への格納が int32 を超えると OverflowError になるので、
実機でのオーバーフローを PC のテストで検出できる。
"""

import math
from array import array

import micropython
from micropython import const

# 状態配列 st のオフセット
_PHM = const(0)  # マーク基準位相(24 bit)
_PHS = const(1)  # スペース基準位相
_INCM = const(2)
_INCS = const(3)
_R = const(4)  # リングの書き込み位置
_N = const(5)  # 窓長 = 1 ビットのサンプル数
_SMI = const(6)
_SMQ = const(7)
_SSI = const(8)
_SSQ = const(9)
_EM = const(10)  # マーク振幅のピーク追従(Q6)
_ES = const(11)
_PLL = const(12)  # DPLL 位相(1/256 サンプル単位、[-P/2, P/2))
_P = const(13)  # ビット周期(1/256 サンプル単位)
_LAST = const(14)  # 直前のサンプリング値(NRZI 復号用)
_PREV = const(15)  # 直前の判定値の符号(遷移検出用)
_PLLSH = const(16)  # 遷移時に PLL 位相を (p >> PLLSH) だけ 0 へ寄せる
_KDEC = const(17)  # AGC の減衰係数(Q16 / サンプル)
_MSH = const(18)  # 相関和を振幅に変換するときの右シフト
_MID = const(19)  # 入力の中点(ADC 12 bit なら 2048)
_ENVMIN = const(20)  # AGC の下限(Q6、無信号時に雑音で暴れないように)
_CAP = const(21)  # 出力ビットバッファの大きさ(viper は境界検査をしないので必ず守る)
_BQ = const(24)  # biquad 3 段: 各 [b0 b1 b2 a1 a2 x1 x2 y1 y2](係数 Q14、状態 Q2)
_COS = const(56)  # cos テーブル 256 個(Q7)
_RING = const(_COS + 256)  # 4 本のリング(各 _NMAX)
_NMAX = const(32)
_SIZE = const(_RING + 4 * _NMAX)

_PHASE_BITS = const(24)
_PHASE_MASK = const((1 << _PHASE_BITS) - 1)
_TBL_SHIFT = const(_PHASE_BITS - 8)


@micropython.viper
def _demod(samp: ptr16, n: int, st: ptr32, out: ptr8) -> int:
    phm = int(st[_PHM])
    phs = int(st[_PHS])
    incm = int(st[_INCM])
    incs = int(st[_INCS])
    r = int(st[_R])
    nwin = int(st[_N])
    smi = int(st[_SMI])
    smq = int(st[_SMQ])
    ssi = int(st[_SSI])
    ssq = int(st[_SSQ])
    em = int(st[_EM])
    es = int(st[_ES])
    pll = int(st[_PLL])
    period = int(st[_P])
    half = period >> 1
    last = int(st[_LAST])
    prev = int(st[_PREV])
    pllsh = int(st[_PLLSH])
    kdec = int(st[_KDEC])
    msh = int(st[_MSH])
    mid = int(st[_MID])
    envmin = int(st[_ENVMIN])
    cap = int(st[_CAP])
    # biquad 1
    a0 = int(st[_BQ + 0])
    a1 = int(st[_BQ + 1])
    a2 = int(st[_BQ + 2])
    a3 = int(st[_BQ + 3])
    a4 = int(st[_BQ + 4])
    ax1 = int(st[_BQ + 5])
    ax2 = int(st[_BQ + 6])
    ay1 = int(st[_BQ + 7])
    ay2 = int(st[_BQ + 8])
    # biquad 2
    c0 = int(st[_BQ + 9])
    c1 = int(st[_BQ + 10])
    c2 = int(st[_BQ + 11])
    c3 = int(st[_BQ + 12])
    c4 = int(st[_BQ + 13])
    cx1 = int(st[_BQ + 14])
    cx2 = int(st[_BQ + 15])
    cy1 = int(st[_BQ + 16])
    cy2 = int(st[_BQ + 17])
    # biquad 3
    e0 = int(st[_BQ + 18])
    e1 = int(st[_BQ + 19])
    e2 = int(st[_BQ + 20])
    e3 = int(st[_BQ + 21])
    e4 = int(st[_BQ + 22])
    ex1 = int(st[_BQ + 23])
    ex2 = int(st[_BQ + 24])
    ey1 = int(st[_BQ + 25])
    ey2 = int(st[_BQ + 26])
    r0 = _RING
    r1 = _RING + _NMAX
    r2 = _RING + 2 * _NMAX
    r3 = _RING + 3 * _NMAX
    nb = 0
    i = 0
    while i < n:
        # 1. 前段フィルタ(Q2)
        x0 = (int(samp[i]) - mid) << 2
        y = (a0 * x0 + a1 * ax1 + a2 * ax2 - a3 * ay1 - a4 * ay2) >> 14
        ax2 = ax1
        ax1 = x0
        ay2 = ay1
        ay1 = y
        z = (c0 * y + c1 * cx1 + c2 * cx2 - c3 * cy1 - c4 * cy2) >> 14
        cx2 = cx1
        cx1 = y
        cy2 = cy1
        cy1 = z
        y = (e0 * z + e1 * ex1 + e2 * ex2 - e3 * ey1 - e4 * ey2) >> 14
        ex2 = ex1
        ex1 = z
        ey2 = ey1
        ey1 = y
        x = y >> 2

        # 2. 直交相関(スライディング窓)
        cm = int(st[_COS + (phm >> _TBL_SHIFT)])
        sm = int(st[_COS + (((phm >> _TBL_SHIFT) - 64) & 255)])
        cs = int(st[_COS + (phs >> _TBL_SHIFT)])
        ss = int(st[_COS + (((phs >> _TBL_SHIFT) - 64) & 255)])
        phm = (phm + incm) & _PHASE_MASK
        phs = (phs + incs) & _PHASE_MASK
        v = x * cm
        smi += v - int(st[r0 + r])
        st[r0 + r] = v
        v = x * sm
        smq += v - int(st[r1 + r])
        st[r1 + r] = v
        v = x * cs
        ssi += v - int(st[r2 + r])
        st[r2 + r] = v
        v = x * ss
        ssq += v - int(st[r3 + r])
        st[r3 + r] = v
        r += 1
        if r >= nwin:
            r = 0

        # 3. 振幅 ~ max + 3/8 min
        a = smi if smi >= 0 else 0 - smi
        b = smq if smq >= 0 else 0 - smq
        if a < b:
            t = a
            a = b
            b = t
        am = (a + (b >> 2) + (b >> 3)) >> msh
        a = ssi if ssi >= 0 else 0 - ssi
        b = ssq if ssq >= 0 else 0 - ssq
        if a < b:
            t = a
            a = b
            b = t
        aspace = (a + (b >> 2) + (b >> 3)) >> msh

        # 4. トーン別ピーク追従(Q6)と正規化差
        t = am << 6
        if t > em:
            em += (t - em) >> 2
        else:
            em -= (em * kdec) >> 16
        t = aspace << 6
        if t > es:
            es += (t - es) >> 2
        else:
            es -= (es * kdec) >> 16
        if em < envmin:
            em = envmin
        if es < envmin:
            es = envmin
        # 符号 = am/em - as/es の符号(12 bit 入力・窓 16・mag_shift 8 で am, es>>6 <= 約 2^15、
        # 積は int32 に収まる)
        d = am * (es >> 6) - aspace * (em >> 6)

        # 5. DPLL: 遷移を位相 0 に、サンプリングを位相 +-P/2(ビットの中央)に
        cur = 1 if d > 0 else 0
        if cur != prev:
            pll -= pll >> pllsh
            prev = cur
        pll += 256
        if pll >= half:
            pll -= period
            # 雑音で遷移が頻発すると PLL が進み、ビットが最大で約 2 倍の速さで出る。
            # バッファはその分を見込んでいるが、溢れる分は捨てる(メモリ破壊を防ぐ)
            if nb < cap:
                out[nb] = 1 if cur == last else 0  # NRZI: 変化なし = 1
                nb += 1
            last = cur
        i += 1

    st[_PHM] = phm
    st[_PHS] = phs
    st[_R] = r
    st[_SMI] = smi
    st[_SMQ] = smq
    st[_SSI] = ssi
    st[_SSQ] = ssq
    st[_EM] = em
    st[_ES] = es
    st[_PLL] = pll
    st[_LAST] = last
    st[_PREV] = prev
    st[_BQ + 5] = ax1
    st[_BQ + 6] = ax2
    st[_BQ + 7] = ay1
    st[_BQ + 8] = ay2
    st[_BQ + 14] = cx1
    st[_BQ + 15] = cx2
    st[_BQ + 16] = cy1
    st[_BQ + 17] = cy2
    st[_BQ + 23] = ex1
    st[_BQ + 24] = ex2
    st[_BQ + 25] = ey1
    st[_BQ + 26] = ey2
    return nb


def biquad(kind, f0, fs, q=0.7071):
    """RBJ Audio EQ Cookbook の 2 次フィルタ係数 -> (b0, b1, b2, a1, a2)(a0 = 1 に正規化)。
    kind: 'hp' / 'lp'。f0 <= 0 なら素通し。"""
    if f0 <= 0:
        return 1.0, 0.0, 0.0, 0.0, 0.0
    w = 2 * math.pi * f0 / fs
    alpha = math.sin(w) / (2 * q)
    cw = math.cos(w)
    if kind == "hp":
        b0 = (1 + cw) / 2
        b1 = -(1 + cw)
    else:
        b0 = (1 - cw) / 2
        b1 = 1 - cw
    a0 = 1 + alpha
    return b0 / a0, b1 / a0, b0 / a0, -2 * cw / a0, (1 - alpha) / a0


# 前段フィルタの設定(tools/afsk_bench.py で評価済み)。両方を並列に動かし、
# どちらかで復号できたフレームを採用すると、どの劣化条件でも単独より良い。
PROFILE_NOISE = {}  # HPF 900Hz(2 次)+ LPF 2700Hz: 白色雑音・プリエンファシスに強い
PROFILE_HUM = {"hpf2_hz": 1000}  # HPF 900Hz + 1000Hz(4 次)+ LPF: ハムの高調波に強い


class Demodulator:
    """samples(array('H')、ADC の 12 bit 値)-> データビット(bytearray、0/1)。

    fs はボーレートの整数倍(19200 = 16 x 1200 など)。
    process() は 1 回あたり最大 max_block サンプル。出力ビットは self.bits[:戻り値]。"""

    def __init__(self, fs=19200, max_block=1024, pll_shift=2, agc_ms=60, mag_shift=8,
                 hpf_hz=900, hpf2_hz=0, lpf_hz=2700, mid=2048, env_min=64, mark=1200, space=2200,
                 baud=1200):
        if fs % baud:
            raise ValueError("fs must be a multiple of the baud rate")
        nwin = fs // baud
        if nwin > _NMAX:
            raise ValueError("fs too high")
        self.fs = fs
        self.max_block = max_block
        self.bits = bytearray(2 * (max_block // nwin) + 4)
        st = array("i", bytes(4 * _SIZE))
        st[_INCM] = int(mark * (1 << _PHASE_BITS) / fs + 0.5)
        st[_INCS] = int(space * (1 << _PHASE_BITS) / fs + 0.5)
        st[_N] = nwin
        st[_P] = nwin << 8
        st[_PLLSH] = pll_shift
        st[_KDEC] = max(1, int(65536 / (agc_ms * fs / 1000) + 0.5))
        st[_MSH] = mag_shift
        st[_MID] = mid
        st[_ENVMIN] = env_min
        st[_CAP] = len(self.bits)
        for k, (kind, f0) in enumerate((("hp", hpf_hz), ("hp", hpf2_hz), ("lp", lpf_hz))):
            co = biquad(kind, f0, fs)
            for j in range(5):
                st[_BQ + 9 * k + j] = int(round(co[j] * 16384))
        for k in range(256):
            st[_COS + k] = int(round(127 * math.cos(2 * math.pi * k / 256)))
        self.st = st

    def process(self, samples, n=-1):
        """n サンプル(省略時は全部)を処理し、得られたデータビット数を返す。"""
        if n < 0:
            n = len(samples)
        if n > self.max_block:
            raise ValueError("block too large")
        return _demod(samples, n, self.st, self.bits)  # type: ignore[arg-type]  (viper のポインタ型)
