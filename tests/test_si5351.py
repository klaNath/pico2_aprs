"""hw.si5351(Si5351B ドライバ)のテスト。偽の I2C で動かすので PC と実機の両方で動く。

GENERATED は ClockBuilder Pro の生成例(親の docs/Si5351B-RevB-Registers_1454MHz.h、145 MHz)。
割り当ては生成例のとおり CLK0 = LO1、CLK2/3 = I/Q、CLK4 = 送信(出力の番号は未確定)。
"""

from core.txgate import TxGate, TxRefused
from hw.si5351 import Si5351B, Si5351Error, ms_params, rx_ratio, tx_ratio, vcxo_param

GENERATED = {
    0x02: 0x13, 0x03: 0x00, 0x04: 0x00, 0x07: 0x01, 0x0F: 0x00, 0x10: 0x4F,
    0x11: 0x8C, 0x12: 0x0F, 0x13: 0x0F, 0x14: 0x6F, 0x15: 0x8C, 0x16: 0x8C,
    0x17: 0x8C, 0x1A: 0x00, 0x1B: 0x09, 0x1C: 0x00, 0x1D: 0x0A, 0x1E: 0xE3,
    0x1F: 0x00, 0x20: 0x00, 0x21: 0x05, 0x22: 0x42, 0x23: 0x48, 0x24: 0x00,
    0x25: 0x0E, 0x26: 0x1C, 0x27: 0xF6, 0x28: 0xC8, 0x29: 0x20, 0x2A: 0x00,
    0x2B: 0x01, 0x2C: 0x0C, 0x2D: 0x00, 0x2E: 0x00, 0x2F: 0x00, 0x30: 0x00,
    0x31: 0x00, 0x3A: 0x00, 0x3B: 0x01, 0x3C: 0x00, 0x3D: 0x0A, 0x3E: 0x00,
    0x3F: 0x00, 0x40: 0x00, 0x41: 0x00, 0x42: 0x00, 0x43: 0x01, 0x44: 0x00,
    0x45: 0x0A, 0x46: 0x00, 0x47: 0x00, 0x48: 0x00, 0x49: 0x00, 0x4A: 0x00,
    0x4B: 0x01, 0x4C: 0x00, 0x4D: 0x01, 0x4E: 0x00, 0x4F: 0x00, 0x50: 0x00,
    0x51: 0x00, 0x5A: 0x00, 0x5B: 0x00, 0x95: 0x00, 0x96: 0x00, 0x97: 0x00,
    0x98: 0x00, 0x99: 0x00, 0x9A: 0x00, 0x9B: 0x00, 0xA2: 0x6A, 0xA3: 0x73,
    0xA4: 0x0F, 0xA5: 0x00, 0xA7: 0x00, 0xA8: 0x00, 0xA9: 0x00, 0xB7: 0x92,
}
# 生成例と値が違ってよいレジスタ
# 3: 生成例は全出力有効。ドライバは送信だけ無効のまま(CLK4 = 1)
EXPECT_OEB = 0xF2
# PLLA の P2、P3(c = 9 -> 5625、比は同じ。P1 は同じ)
PLLA_C = {0x1A, 0x1B, 0x1F, 0x20, 0x21}
# PLLB の P2、P3(c = 1000008 -> 10^6。P1 と VCXO_Param は同じ)
PLLB_C = {0x22, 0x23, 0x27, 0x28, 0x29}
# 168: CLK3_PHOFF(生成例は 0、ドライバは 90 度)
PHOFF_Q = 0xA8
# 生成例にない明示の書き込み(0xB1 は PLL リセット)
EXTRA = {9, 24, 25, 0xB1}


class FakeI2C:
    """レジスタと書き込みの順序を覚える。fail_write: この回数の書き込みのあと OSError"""

    def __init__(self, status=0, fail_write=None):
        self.regs = {}
        self.log = []  # (レジスタ, 値) を 1 バイトごとに
        self.status = status
        self.fail_write = fail_write
        self.fail_read = False
        self.writes = 0

    def writeto_mem(self, addr, reg, data):
        assert addr == 0x60
        if self.fail_write is not None and self.writes >= self.fail_write:
            raise OSError("i2c")
        self.writes += 1
        for i, v in enumerate(data):
            self.regs[reg + i] = v
            self.log.append((reg + i, v))

    def readfrom_mem(self, addr, reg, n):
        assert addr == 0x60 and reg == 0 and n == 1
        if self.fail_read:
            raise OSError("i2c")
        return bytes((self.status,))


def _dev(i2c=None, **kw):
    args = dict(tx_clk=4, lo1_clk=0, i_clk=2, q_clk=3)
    args.update(kw)
    return Si5351B(i2c if i2c is not None else FakeI2C(), **args)


def _raises(exc, fn, *a):
    try:
        fn(*a)
    except exc:
        return True
    raise AssertionError("%s expected" % exc)


def test_reproduces_generated_registers():
    i2c = FakeI2C()
    _dev(i2c).init(145_000_000, 145_000_000)
    for reg, want in GENERATED.items():
        if reg == 3 or reg in PLLA_C or reg in PLLB_C or reg == PHOFF_Q:
            continue
        assert i2c.regs.get(reg) == want, (hex(reg), i2c.regs.get(reg), want)
    assert i2c.regs[3] == EXPECT_OEB
    assert i2c.regs[PHOFF_Q] == 24
    assert set(i2c.regs) == set(GENERATED) | EXTRA
    assert i2c.regs[9] == 0 and i2c.regs[24] == 0 and i2c.regs[25] == 0


def test_generated_values_decode():
    # 生成例の設計値: PLLA 25 + 7/9、PLLB 32 + 2/9(c = 1000008)、P1 と VCXO_Param
    assert ms_params(25, 7, 9) == (2787, 5, 9)
    assert ms_params(32, 222224, 1000008) == (3612, 444448, 1000008)
    assert vcxo_param(32, 222222, 240) == 1012586


def test_145_000():
    assert rx_ratio(145_000_000) == (25, 4375)  # = 25 + 7/9
    assert ms_params(25, 4375, 5625) == (2787, 3125, 5625)
    assert tx_ratio(145_000_000) == (32, 222222)
    assert ms_params(32, 222222, 10**6) == (3612, 444416, 10**6)
    i2c = FakeI2C()
    _dev(i2c).init(145_000_000, 145_000_000)
    assert [i2c.regs[r] for r in range(34, 42)] == [0x42, 0x40, 0, 0x0E, 0x1C, 0xF6, 0xC8, 0x00]
    assert [i2c.regs[r] for r in (162, 163, 164)] == [0x6A, 0x73, 0x0F]


def test_144_660():
    assert rx_ratio(144_660_000) == (25, 4035)
    assert ms_params(25, 4035, 5625) == (2779, 4605, 5625)
    assert tx_ratio(144_660_000) == (32, 146667)
    assert ms_params(32, 146667, 10**6) == (3602, 773376, 10**6)
    assert vcxo_param(32, 146667, 240) == 1012567
    i2c = FakeI2C()
    d = _dev(i2c)
    d.init(144_660_000, 144_660_000)
    assert i2c.regs[162] | i2c.regs[163] << 8 | i2c.regs[164] << 16 == 1012567


def test_vcxo_param_follows_frequency_and_apr():
    i2c = FakeI2C()
    d = _dev(i2c)
    d.init(145_000_000, 145_000_000)
    d.set_tx(144_660_000)
    assert i2c.regs[162] | i2c.regs[163] << 8 | i2c.regs[164] << 16 == 1012567
    assert i2c.log[-1] == (0xB1, 0x8C)  # PLLB だけリセット
    assert vcxo_param(32, 222222, 30) == 126573  # APR は設定
    _raises(ValueError, vcxo_param, 32, 0, 100000)  # 22 ビットを超える


def test_band_edges():
    for hz in (144_000_000, 146_000_000):
        a, b = tx_ratio(hz)
        assert a == 32 and 0 <= b < 10**6
        assert 864_000 <= 27_000 * (a * 10**6 + b) // 10**6 <= 876_000  # VCO(kHz)= f_TX x 6
        a, b = rx_ratio(hz)
        assert 15 <= a <= 90 and 0 <= b < 5625
        assert 691_200 <= 27_000 * (a * 5625 + b) // 5625 <= 700_800  # VCO(kHz)= lnatest の範囲
    assert rx_ratio(144_000_000) == (25, 3375)  # lnatest の例
    for hz in (143_999_999, 146_000_001, 0):
        _raises(ValueError, tx_ratio, hz)
        _raises(ValueError, rx_ratio, hz)
        i2c = FakeI2C()
        _raises(ValueError, _dev(i2c).init, hz, None)
        _raises(ValueError, _dev(i2c).init, None, hz)
        assert i2c.log == []  # I2C に触らない
    _raises(ValueError, rx_ratio, 145_000_500)  # 受信は 1 kHz 単位


def test_tx_rounds_to_nearest_step():
    assert tx_ratio(144_000_000) == (32, 0)
    assert tx_ratio(144_000_002) == (32, 0)  # VCO で 12 Hz < 13.5 Hz
    assert tx_ratio(144_000_003) == (32, 1)  # VCO で 18 Hz > 13.5 Hz


def test_init_order():
    i2c = FakeI2C()
    _dev(i2c).init(145_000_000, 145_000_000)
    regs = [r for r, _v in i2c.log]
    # 最初に全出力を止める
    assert i2c.log[0] == (3, 0xFF)
    assert i2c.log[1:9] == [(r, 0x80) for r in range(16, 24)]
    # PLL リセットは設定のあと、1 回だけ
    reset = regs.index(0xB1)
    assert i2c.log[reset] == (0xB1, 0xAC) and regs.count(0xB1) == 1
    assert max(i for i, r in enumerate(regs) if r not in (3, 0xB1)) < reset
    # レジスタ 3 を書くのは最初と最後だけで、0x00 は書かない。送信の出力は有効にしない
    oeb = [v for r, v in i2c.log if r == 3]
    assert oeb == [0xFF, EXPECT_OEB] and i2c.log[-1] == (3, EXPECT_OEB)
    assert oeb[-1] & (1 << 4)


def test_waits_for_sys_init_with_limit():
    import hw.si5351 as m

    keep = m.INIT_TIMEOUT_MS
    m.INIT_TIMEOUT_MS = 10
    try:
        i2c = FakeI2C(status=0x80)
        d = _dev(i2c)
        _raises(Si5351Error, d.init, 145_000_000, 145_000_000)
        assert (0xB1, 0xAC) not in i2c.log and d.tx_hz() is None
        assert i2c.log == [(3, 0xFF)]  # 失敗の後始末で止めるだけ
    finally:
        m.INIT_TIMEOUT_MS = keep


def test_plla_lock_failure_leaves_outputs_off():
    import hw.si5351 as m

    keep = m.RX_LOCK_TIMEOUT_MS
    m.RX_LOCK_TIMEOUT_MS = 10
    try:
        i2c = FakeI2C(status=0x20)  # LOL_A
        d = _dev(i2c)
        _raises(Si5351Error, d.init, 145_000_000, 145_000_000)
        assert i2c.regs[3] == 0xFF and d.tx_hz() is None and not d.tx_ready()
        _raises(Si5351Error, d.tx_on)
        assert i2c.regs[3] == 0xFF
    finally:
        m.RX_LOCK_TIMEOUT_MS = keep


def test_lol_b_is_not_used_on_si5351b():
    # AN619: LOL_B は Si5351A/C のみ。立っていても判定を変えない(PLLB のロック外れは検出できない)
    i2c = FakeI2C(status=0x40)
    d = _dev(i2c)
    d.init(145_000_000, 145_000_000)
    assert d.tx_ready()


def test_tx_settle_wait_replaces_lock_detection():
    import time

    import hw.si5351 as m

    d = _dev(FakeI2C())
    t0 = time.ticks_ms()
    d.init(145_000_000, 145_000_000)
    assert time.ticks_diff(time.ticks_ms(), t0) >= m.TX_SETTLE_MS
    t0 = time.ticks_ms()
    d.set_tx(144_660_000)
    assert time.ticks_diff(time.ticks_ms(), t0) >= m.TX_SETTLE_MS


def test_i2c_error_at_any_step_keeps_tx_off():
    total = FakeI2C()
    _dev(total).init(145_000_000, 145_000_000)
    for k in range(total.writes):
        i2c = FakeI2C(fail_write=k)
        i2c.regs[3] = 0xFF
        d = _dev(i2c)
        _raises(OSError, d.init, 145_000_000, 145_000_000)
        assert i2c.regs[3] & (1 << 4), k
        assert d.tx_hz() is None and not d.tx_ready(), k
        _raises(Si5351Error, d.tx_on)
        assert i2c.regs[3] & (1 << 4), k


def test_tx_on_off_touches_only_tx_bit():
    i2c = FakeI2C()
    d = _dev(i2c)
    d.init(145_000_000, 145_000_000)
    assert d.tx_hz() == 145_000_000 and d.rx_hz() == 145_000_000 and d.tx_ready()
    d.tx_on()
    assert i2c.regs[3] == 0xE2
    _raises(Si5351Error, d.set_tx, 144_660_000)  # 送信中は変えない
    d.tx_off()
    assert i2c.regs[3] == EXPECT_OEB


def test_tx_off_before_init_disables_everything():
    i2c = FakeI2C()
    i2c.regs[3] = 0x00  # Pico のリセット前の出力が残っている
    d = _dev(i2c)
    d.tx_off()
    assert i2c.regs[3] == 0xFF
    assert d.tx_hz() is None and not d.tx_ready()
    _raises(Si5351Error, d.tx_on)


def test_tx_ready_reads_status_and_fails_safe():
    i2c = FakeI2C()
    d = _dev(i2c)
    d.init(145_000_000, 145_000_000)
    assert d.tx_ready()
    for status in (0x80, 0x08):  # SYS_INIT、LOS_XTAL
        i2c.status = status
        assert not d.tx_ready()
    i2c.status = 0x20  # LOL_A(受信)は送信の条件にしない
    assert d.tx_ready()
    i2c.status = 0
    i2c.fail_read = True
    assert not d.tx_ready()


def test_failed_retune_is_not_confirmed():
    i2c = FakeI2C()
    d = _dev(i2c)
    d.init(145_000_000, 145_000_000)
    i2c.fail_write = i2c.writes
    _raises(OSError, d.set_tx, 144_660_000)
    assert d.tx_hz() is None  # どこまで書けたか分からない
    _raises(ValueError, d.set_tx, 147_000_000)


def test_set_rx_resets_plla_only():
    i2c = FakeI2C()
    d = _dev(i2c)
    d.init(145_000_000, 145_000_000)
    d.set_rx(144_660_000)
    assert d.rx_hz() == 144_660_000 and i2c.log[-1] == (0xB1, 0x2C)
    assert d.tx_hz() == 145_000_000


def test_clock_numbers_are_settings():
    # lnatest の割り当て(CLK0/1 = I/Q、CLK2 = LO1)でも同じ値が別の出力に出る
    i2c = FakeI2C()
    d = Si5351B(i2c, tx_clk=4, lo1_clk=2, i_clk=0, q_clk=1)
    d.init(145_000_000, None)
    assert i2c.regs[16 + 2] == 0x4F and i2c.regs[16] == 0x0F and i2c.regs[16 + 1] == 0x0F
    assert i2c.regs[42 + 2 * 8 + 2] == 0x0C  # MS2 が DIVBY4
    assert i2c.regs[165 + 1] == 24 and i2c.regs[165] == 0
    assert i2c.regs[3] == 0xF8
    for bad in (dict(tx_clk=6), dict(tx_clk=0), dict(lo1_clk=None), dict(i_clk=9)):
        _raises(ValueError, lambda kw=bad: _dev(FakeI2C(), **kw))


def test_settings_change_registers():
    i2c = FakeI2C()
    _dev(i2c, idrv_lo1_ma=2, idrv_iq_ma=4, idrv_tx_ma=6, xtal_cl_pf=10, oeb_mask=0xFF).init(
        145_000_000, 145_000_000)
    assert i2c.regs[16] == 0x4C and i2c.regs[18] == 0x0D and i2c.regs[19] == 0x0D
    assert i2c.regs[20] == 0x6E and i2c.regs[183] == 0xD2 and i2c.regs[9] == 0xFF


def test_without_tx_clock_never_enables_tx():
    i2c = FakeI2C()
    d = Si5351B(i2c, lo1_clk=0, i_clk=2, q_clk=3)  # config.SI5351_TX_CLK = None の状態
    d.init(145_000_000, None)
    assert d.tx_hz() is None
    _raises(ValueError, d.init, 145_000_000, 145_000_000)
    _raises(Si5351Error, d.tx_on)
    d.tx_off()


class Pin:
    def __init__(self):
        self.v = None

    def value(self, v=None):
        if v is not None:
            self.v = v


CALL = "Q1TEST"  # 架空のコールサイン


def _gate(i2c, d):
    return TxGate(Pin(), callsign=CALL, allowed=True, freq=d)


def test_gate_with_driver_sequence():
    i2c = FakeI2C()
    d = _dev(i2c)
    gate = _gate(i2c, d)
    assert i2c.regs[3] == 0xFF  # ゲートを作るときに止める
    assert gate.refuse_reason() == "frequency not set"  # 初期化の前は拒否
    _raises(TxRefused, gate.key)
    d.init(145_000_000, 145_000_000)
    assert gate.refuse_reason() is None
    gate.key()
    assert gate._pin.v == 1 and i2c.regs[3] == 0xE2
    gate.unkey()
    assert gate._pin.v == 0 and i2c.regs[3] == EXPECT_OEB
    i2c.status = 0x08  # LOS_XTAL
    assert gate.refuse_reason() == "frequency source not ready"
    _raises(TxRefused, gate.key)
    assert i2c.regs[3] == EXPECT_OEB and gate._pin.v == 0


def test_gate_i2c_error_on_tx_on():
    i2c = FakeI2C()
    d = _dev(i2c)
    gate = _gate(i2c, d)
    d.init(145_000_000, 145_000_000)
    i2c.fail_write = i2c.writes
    _raises(OSError, gate.key)
    assert gate._pin.v == 0 and not gate.keyed


def test_gate_i2c_error_on_tx_off_refuses_later():
    i2c = FakeI2C()
    d = _dev(i2c)
    gate = _gate(i2c, d)
    d.init(145_000_000, 145_000_000)
    gate.key()
    i2c.fail_write = i2c.writes
    gate.unkey()  # 例外にしない(後始末を止めない)
    assert gate._pin.v == 0 and not gate.keyed
    assert gate.refuse_reason() == "TX clock off failed"
    _raises(TxRefused, gate.key)
