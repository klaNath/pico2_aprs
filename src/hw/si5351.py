"""Si5351B(27 MHz 水晶、受信 PLLA + 送信 VCXO PLLB)のドライバ。core.txgate の周波数源の形。

出典: AN619 Rev 0.8、データシート Si5351-B Rev 1.3(初期化の順序は Figure 10)、
ClockBuilder Pro の生成例(親の docs/Si5351B-RevB-Registers_1454MHz.h)。
I2C は引数で渡すので、PC でも偽物でテストできる。

    受信: PLLA。VCO = 27 MHz x (a + b/5625)、LO1 = VCO/4、I/Q = VCO/24(Q は PHOFF で 90 度)
          同調周波数 = LO1 - IF = 5 VCO / 24 = 5.625 MHz x (a + b/5625) なので b の 1 が 1 kHz
          同調周波数 = 受信信号の周波数 + rx_offset_hz(低 IF、lnatest の +24 kHz)。帯域の判定は信号で行う
    送信: PLLB。VCO = f_TX x 6 = 27 MHz x (a + b/10^6)。c = 10^6 は VCXO の条件(AN619 3.2 と 7)
          MS4 相当は 6 分周の整数。VCXO_Param は周波数を変えるたびに計算し直す
CLK の番号は確定していないので引数で受ける(config.SI5351_*_CLK)。

Pico のリセット(ウォッチドッグを含む)では Si5351 は初期化されず、設定と出力が残る。TX_EN が Low でも
送信クロックが動いていると空中線端子へ漏れ、受信と同じ周波数なら受信できなくなる(electric の手計算で
-40〜-20 dBm、+/-10 dB、仮。未測定)。局に組み込むときの必須手順(この順で、起動の最初に行う):
    1. GP2 を Low にする。I2C を使う前、I2C のオブジェクトを作る前に行う(I2C が使えなくても必ず実行される)
    2. TxGate を作る。作るときに GP2 を切ってから tx_off() を呼び、init() の前なのでレジスタ 3 = 0xFF で
       全出力を止める
    3. init() で設定する。受信の出力だけを有効にする
1 と 2 は hw.txctl.si5351_gate が行う。2 で I2C が失敗したら、何もせずウォッチドッグのリセットで再起動を
繰り返す(オーナー決定、hw.txctl.halt_for_watchdog)。ウォッチドッグは未実装なので、それまでは永久に止まる(docs/tx-time-limit.md §4)。
送信の出力は tx_on() まで無効のまま。受信の再選局(set_rx)でも有効にならない。
tx_off() の I2C が失敗すると、PTT は切れていても送信クロックは動いたままで、受信に影響が残る。
core.txgate は再起動まで送信を拒否するが、受信の妨害は解消しない。送信クロックを止めるには、I2C が
戻ったあとの init() か再起動が要る(自動で再試行はしない)。
init() の途中で失敗したら ready にならない
(tx_hz() が None、tx_ready() が False なので core.txgate が拒否する)。

未確認(データシートはレジスタの説明を AN619 に委ねていて、解決しない):
    レジスタ 7 = 0x01 の意味(CBPro の値のまま。AN619 では Reserved)
    レジスタ 4 = 0x00 の意味(CBPro の DIS_RESET_LOL。AN619 では Reserved)
制限: PLLB のロック外れは検出できない。AN619 のレジスタ 0 は LOL_B を「Si5351A/C only」としているので、
Si5351B では LOL_B を使わない。送信側はリセット後の待ち時間(TX_SETTLE_MS)と、SYS_INIT、LOS_XTAL の
確認で管理する。tx_ready() が True でも PLLB がロックしている保証はない。実機の測定項目。LOL_A は使える。
データシートとの照合で残る問題:
    VCXO の出力の中心周波数は 112.5 MHz までと書かれている(データシート 3 章)。送信の
    144〜146 MHz はデータシートの記載を超えるが、オーナーの実験(144.66 MHz)で確認済み。保証外
    112.5 MHz を超える異なる周波数は同時に 2 つまで(Table 7)。LO1 と送信でちょうど 2 つ
"""

import time

from micropython import const

XTAL_HZ = const(27_000_000)
RX_C = const(5625)  # 受信の分数の分母。b の 1 が 1 kHz
TX_C = const(1_000_000)  # VCXO の条件
TX_MS = const(6)  # 送信の出力 Multisynth の分周比
IQ_MS = const(24)
PHOFF_90 = const(24)  # DesiredOffset x 4 x Fvco = (T_out / 4) x 4 x Fvco = 分周比(AN619 6)
MIN_HZ = const(144_000_000)  # 144〜146MHz の外には設定しない
MAX_HZ = const(146_000_000)

_STATUS = const(0)
_MASK = const(2)
_OEB = const(3)
_REG4 = const(4)
_REG7 = const(7)
_OEB_MASK = const(9)
_PLL_SRC = const(15)
_CLK0_CTRL = const(16)
_DIS_STATE = const(24)
_MSNA = const(26)
_MSNB = const(34)
_MS0 = const(42)
_MS6_P1 = const(90)
_SS = const(149)
_VCXO = const(162)
_PHOFF0 = const(165)
_RESET = const(177)
_XTAL_CL = const(183)

_SYS_INIT = const(0x80)
_LOL_A = const(0x20)
_LOS_XTAL = const(0x08)
_RESET_BOTH = const(0xAC)  # データシートの Figure 10 の値(PLLB_RST、PLLA_RST)
_RESET_A = const(0x2C)  # 仮: 0xAC から PLLB_RST を除いたもの
_RESET_B = const(0x8C)  # 仮: 0xAC から PLLA_RST を除いたもの
_REG2_VALUE = const(0x13)  # CBPro の値
_REG7_VALUE = const(0x01)  # 未確認(AN619 では Reserved)。CBPro の値のまま書く
_CTRL_PDN = const(0x80)
_CTRL_UNUSED = const(0x8C)
_CTRL_LO1 = const(0x4C)  # MS_INT = 1(DIVBY4 の条件)、PLLA、CLK_SRC = MS
_CTRL_IQ = const(0x0C)  # MS_INT = 0(PHOFF の条件)、PLLA
_CTRL_TX = const(0x6C)  # MS_INT = 1、PLLB
_DIVBY4 = const(0x0C)  # Multisynth の 3 バイト目の MSx_DIVBY4
_IDRV = {2: 0, 4: 1, 6: 2, 8: 3}
_XTAL_BITS = {6: 0x52, 8: 0x92, 10: 0xD2}  # 下位 6 ビットは 010010b(AN619 のレジスタ 183)

# 仮: 待ちの上限。文書にこの値はない。電源投入から出力までの T_RDY は最大 10 ms(Table 5)なので余裕を取る
INIT_TIMEOUT_MS = 1000  # SYS_INIT
RX_LOCK_TIMEOUT_MS = 200  # PLLA のロック(LOL_A)
# PLLB をリセットしたあとに送信クロックを出すまでの待ち。ロック検出の代わり。
# データシート Rev 1.3 Table 5 の T_RDY(電源投入から出力まで)最大 10 ms を流用する。同表の周波数変更
# T_FREQ は 10 us だが、ここでは PLL をリセットするので長い方を取る。PLL リセット後の時間そのものの規定は
# 文書にない(仮)
TX_SETTLE_MS = 10


class Si5351Error(Exception):
    pass


def ms_params(a, b, c):
    """分周比 a + b/c -> (P1, P2, P3)(AN619 3.2 と 4.1.2)"""
    k = 128 * b // c
    return 128 * a + k - 512, 128 * b - c * k, c


def _pack(p1, p2, p3, third=0):
    """Multisynth の 8 バイトのレジスタ。third: 3 バイト目の上位(R_DIV、DIVBY4)"""
    return bytes(
        (
            (p3 >> 8) & 0xFF,
            p3 & 0xFF,
            third | ((p1 >> 16) & 0x03),
            (p1 >> 8) & 0xFF,
            p1 & 0xFF,
            (((p3 >> 16) & 0x0F) << 4) | ((p2 >> 16) & 0x0F),
            (p2 >> 8) & 0xFF,
            p2 & 0xFF,
        )
    )


def _check_band(hz):
    if not MIN_HZ <= hz <= MAX_HZ:
        raise ValueError("frequency out of band: %d" % hz)


def rx_ratio(sig_hz, offset_hz=0):
    """受信信号の周波数 -> PLLA の (a, b)。帯域は信号で判定する。同調周波数は 1 kHz 単位"""
    _check_band(sig_hz)
    tuned = sig_hz + offset_hz
    if tuned % 1000:
        raise ValueError("RX tuning must be a multiple of 1 kHz")
    khz = tuned // 1000
    return khz // RX_C, khz % RX_C


def tx_ratio(tx_hz):
    """送信周波数 -> PLLB の (a, b)。b の 1 は出力で 4.5 Hz なので、最も近い値に丸める"""
    _check_band(tx_hz)
    vco = tx_hz * TX_MS
    # 帯域内の VCO は 864〜876 MHz(a = 32、b < 0.45 x 10^6)なので、丸めで a に繰り上がらない
    return vco // XTAL_HZ, (vco % XTAL_HZ + 13) // 27  # 27 MHz / 10^6 = 27 Hz


def vcxo_param(a, b, apr):
    """VCXO_Param = 1.03 x (128 a + b / 10^6) x APR(AN619 7)を四捨五入"""
    v = (103 * (128 * a * TX_C + b) * apr + 50_000_000) // 100_000_000
    if not 0 <= v < (1 << 22):
        raise ValueError("VCXO_Param out of range: %d" % v)
    return v


class Si5351B:
    def __init__(self, i2c, addr=0x60, tx_clk=None, lo1_clk=None, i_clk=None, q_clk=None, apr=240,
                 idrv_lo1_ma=8, idrv_iq_ma=8, idrv_tx_ma=8, xtal_cl_pf=8, oeb_mask=0x00,
                 rx_offset_hz=0):
        """*_clk: 出力の番号(0〜5。MS6、MS7 は DIVBY4 と PHOFF が使えないので不可)。
        tx_clk が None なら送信しない。lo1_clk、i_clk、q_clk はそろえて指定するか、すべて None
        rx_offset_hz: 受信信号の周波数に足して同調する量(低 IF)"""
        rx = (lo1_clk, i_clk, q_clk)
        if None in rx and rx != (None, None, None):
            raise ValueError("LO1, I and Q must be set together")
        used = [c for c in rx + (tx_clk,) if c is not None]
        for c in used:
            if not 0 <= c <= 5 or used.count(c) > 1:
                raise ValueError("bad CLK number: %d" % c)
        self.i2c = i2c
        self.addr = addr
        self.tx_clk = tx_clk
        self.lo1_clk = lo1_clk
        self.i_clk = i_clk
        self.q_clk = q_clk
        self.apr = apr
        self._idrv = (_IDRV[idrv_lo1_ma], _IDRV[idrv_iq_ma], _IDRV[idrv_tx_ma])
        self._xtal = _XTAL_BITS[xtal_cl_pf]
        self.oeb_mask = oeb_mask
        self.rx_offset_hz = rx_offset_hz
        self._oeb = 0xFF  # レジスタ 3 の写し(1 = 無効)
        self._ready = False
        self._tx_hz = None
        self._rx_hz = None
        self._keyed = False

    # ---- I2C
    def _write(self, reg, data):
        self.i2c.writeto_mem(self.addr, reg, data)

    def _write1(self, reg, v):
        self._write(reg, bytes((v,)))

    def _status(self):
        return self.i2c.readfrom_mem(self.addr, _STATUS, 1)[0]

    def _wait_clear(self, mask, timeout_ms, what):
        t0 = time.ticks_ms()
        while self._status() & mask:
            if time.ticks_diff(time.ticks_ms(), t0) >= timeout_ms:
                raise Si5351Error("timeout: " + what)
            time.sleep_ms(1)

    # ---- 周波数源の形(core.txgate)
    def tx_hz(self):
        """確定した送信周波数。初期化の前後の失敗中や、書き込みの失敗後は None"""
        return self._tx_hz if self._ready else None

    def tx_ready(self):
        """送信クロックを出してよいか。SYS_INIT と LOS_XTAL だけを見る。PLLB のロックは検出できない
        (モジュールの説明)。読めなければ False(送信しない側)"""
        if not self._ready:
            return False
        try:
            return not self._status() & (_SYS_INIT | _LOS_XTAL)
        except OSError:
            return False

    def tx_on(self):
        if self.tx_clk is None or self.tx_hz() is None:
            raise Si5351Error("TX clock is not ready")
        oeb = self._oeb & ~(1 << self.tx_clk)
        self._write1(_OEB, oeb)
        self._oeb = oeb
        self._keyed = True

    def tx_off(self):
        """送信の出力を無効にする。init() の前に呼ぶと、リセット前の出力も含めてすべて止める。
        写しは先に変える(書き込みが失敗しても、次に書くときに無効になるように)"""
        if self.tx_clk is not None:
            self._oeb |= 1 << self.tx_clk
        self._keyed = False
        self._write1(_OEB, self._oeb)

    # ---- 周波数
    def _write_rx(self, rx_hz):
        a, b = rx_ratio(rx_hz, self.rx_offset_hz)
        self._write(_MSNA, _pack(*ms_params(a, b, RX_C)))

    def _write_tx(self, tx_hz):
        a, b = tx_ratio(tx_hz)
        v = vcxo_param(a, b, self.apr)
        self._write(_MSNB, _pack(*ms_params(a, b, TX_C)))
        self._write(_VCXO, bytes((v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0x3F)))

    def set_rx(self, rx_hz):
        """受信信号の周波数を変えて PLLA をリセットする(I/Q の位相を揃えるため)"""
        rx_ratio(rx_hz, self.rx_offset_hz)
        if not self._ready or self.lo1_clk is None:
            raise Si5351Error("RX is not ready")
        self._rx_hz = None
        self._write_rx(rx_hz)
        self._write1(_RESET, _RESET_A)
        self._rx_hz = rx_hz

    def set_tx(self, tx_hz):
        """送信周波数を変える。送信中は変えない。失敗すると tx_hz() は None のまま(拒否される)"""
        tx_ratio(tx_hz)
        if self._keyed:
            raise Si5351Error("TX clock is on")
        if not self._ready or self.tx_clk is None:
            raise Si5351Error("TX is not ready")
        self._tx_hz = None
        self._write_tx(tx_hz)
        self._write1(_RESET, _RESET_B)
        time.sleep_ms(TX_SETTLE_MS)
        self._tx_hz = tx_hz

    def rx_hz(self):
        """受信信号の周波数(同調周波数ではない)"""
        return self._rx_hz if self._ready else None

    # ---- 初期化
    def init(self, rx_hz=None, tx_hz=None):
        """手順: SYS_INIT 待ち -> 全出力を止める -> 設定 -> PLL リセット -> LOL_A 確認と待ち -> 受信の出力。
        rx_hz は受信信号の周波数。失敗したら例外。送信の出力は有効にしない。周波数はここで範囲を確かめ、I2C の前に例外にする"""
        self._ready = False
        self._tx_hz = self._rx_hz = None
        self._keyed = False
        if rx_hz is not None:
            rx_ratio(rx_hz, self.rx_offset_hz)
            if self.lo1_clk is None:
                raise ValueError("RX CLK numbers are not set")
        if tx_hz is not None:
            tx_ratio(tx_hz)
            if self.tx_clk is None:
                raise ValueError("TX CLK number is not set")
        self._oeb = 0xFF
        try:
            self._wait_clear(_SYS_INIT, INIT_TIMEOUT_MS, "SYS_INIT")
            self._write1(_OEB, 0xFF)
            self._write(_CLK0_CTRL, bytes((_CTRL_PDN,) * 8))
            self._write_config(rx_hz, tx_hz)
            self._write1(_RESET, _RESET_BOTH)
            if rx_hz is not None:
                self._wait_clear(_LOL_A, RX_LOCK_TIMEOUT_MS, "PLLA lock")
            if tx_hz is not None:
                time.sleep_ms(TX_SETTLE_MS)
            if rx_hz is not None:
                self._oeb &= ~((1 << self.lo1_clk) | (1 << self.i_clk) | (1 << self.q_clk))
                self._write1(_OEB, self._oeb)
        except BaseException:
            self._oeb = 0xFF
            try:
                self._write1(_OEB, 0xFF)
            except OSError:
                pass
            raise
        self._rx_hz = rx_hz
        self._tx_hz = tx_hz
        self._ready = True

    def _write_config(self, rx_hz, tx_hz):
        # レジスタ 3 と 177 は書かない(0x0003 の 0x00 は送信の出力も有効にしてしまう)
        self._write1(_MASK, _REG2_VALUE)
        self._write1(_REG4, 0x00)  # 未確認(AN619 では Reserved)。CBPro の値のまま
        self._write1(_REG7, _REG7_VALUE)
        self._write1(_OEB_MASK, self.oeb_mask)
        self._write1(_PLL_SRC, 0x00)
        # 無効のときは Low(CBPro の設計の Disabled State = StopLow)
        self._write(_DIS_STATE, b"\x00\x00")
        self._write_controls(rx_hz, tx_hz)
        self._write_multisynths(rx_hz, tx_hz)
        self._write(_MS6_P1, b"\x00\x00")
        self._write(_SS, bytes(7))  # スペクトラム拡散なし
        self._write1(_XTAL_CL, self._xtal)

    def _write_controls(self, rx_hz, tx_hz):
        ctrl = [_CTRL_UNUSED] * 8
        if rx_hz is not None:
            ctrl[self.lo1_clk] = _CTRL_LO1 | self._idrv[0]
            ctrl[self.i_clk] = _CTRL_IQ | self._idrv[1]
            ctrl[self.q_clk] = _CTRL_IQ | self._idrv[1]
        if tx_hz is not None:
            ctrl[self.tx_clk] = _CTRL_TX | self._idrv[2]
        self._write(_CLK0_CTRL, bytes(ctrl))

    def _write_multisynths(self, rx_hz, tx_hz):
        if rx_hz is not None:
            self._write_rx(rx_hz)
            self._write(_MS0 + 8 * self.lo1_clk, _pack(0, 0, 1, _DIVBY4))  # 150 MHz を超えるので 4 分周
            iq = _pack(*ms_params(IQ_MS, 0, 1))
            self._write(_MS0 + 8 * self.i_clk, iq)
            self._write(_MS0 + 8 * self.q_clk, iq)
            self._write1(_PHOFF0 + self.lo1_clk, 0)
            self._write1(_PHOFF0 + self.i_clk, 0)
            self._write1(_PHOFF0 + self.q_clk, PHOFF_90)
        if tx_hz is not None:
            self._write_tx(tx_hz)
            self._write(_MS0 + 8 * self.tx_clk, _pack(*ms_params(TX_MS, 0, 1)))
            self._write1(_PHOFF0 + self.tx_clk, 0)
