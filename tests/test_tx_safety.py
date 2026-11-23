"""送信の安全(PTT の扱い)のテスト。偽のハードウェアで PC 上で動かす。実機では何もしない
(実機で PTT を動かさないため。tests/_fake_hw.py)。"""

import _fake_hw

CALL = "Q1TEST"  # 架空のコールサイン


def _ptt_on_writes(active_high=True):
    import config

    on = 1 if active_high else 0
    return [v for v in _fake_hw.Pin.history.get(config.PIN_PTT, []) if v == on]


def test_audio_only_never_keys_ptt():
    if not _fake_hw.install():
        return
    import config
    from hw import txctl
    from hw.afsk_tx import AfskTx

    _fake_hw.Pin.reset()
    tx = AfskTx(config.PIN_AFSK_OUT, txctl.audio_only(), config.FS, config.PWM_TOP, max_ms=1000)
    tx.send_blocking([b"\x00" * 8])
    tx.close()
    assert _ptt_on_writes(config.PTT_ACTIVE_HIGH) == []
    assert _fake_hw.Pin.state[config.PIN_PTT] == (0 if config.PTT_ACTIVE_HIGH else 1)


def test_bench_gate_needs_explicit_flag():
    if not _fake_hw.install():
        return
    import config
    from core.txgate import AudioOnly
    from hw import txctl

    keep = config.BENCH_TX_HZ, config.CALLSIGN, config.TX_ENABLED
    try:
        config.BENCH_TX_HZ = None
        assert isinstance(txctl.bench_gate(), AudioOnly)
        config.BENCH_TX_HZ = 145_000_000
        config.CALLSIGN, config.TX_ENABLED = CALL, False
        assert txctl.bench_gate().refuse_reason() == "TX not enabled"  # フラグだけでは入らない
        config.TX_ENABLED = True
        assert txctl.bench_gate().refuse_reason() is None
        config.CALLSIGN = None
        assert txctl.bench_gate().refuse_reason() == "callsign not set"
    finally:
        config.BENCH_TX_HZ, config.CALLSIGN, config.TX_ENABLED = keep


class FakeRing:
    def stop(self):
        pass

    def close(self):
        pass


def _open_gate(pin, TxGate, FixedFreq):
    import config

    return TxGate(pin, config.PTT_ACTIVE_HIGH, callsign=CALL, allowed=True,
                  freq=FixedFreq(145_000_000))


def _keyed_tx():
    """本物の AfskTx + 偽の DMA。PTT の状態は偽の Pin(GP2)で見る"""
    import config
    from core.txgate import FixedFreq, TxGate
    from hw.afsk_tx import AfskTx

    _fake_hw.Pin.reset()
    _fake_hw.DMA.stuck = False
    gate = _open_gate(_fake_hw.Pin(config.PIN_PTT), TxGate, FixedFreq)
    return AfskTx(config.PIN_AFSK_OUT, gate, config.FS, config.PWM_TOP, max_ms=1000), gate


def _ptt_is_off():
    import config

    return _fake_hw.Pin.state[config.PIN_PTT] == (0 if config.PTT_ACTIVE_HIGH else 1)


def test_stuck_dma_times_out_and_drops_ptt():
    if not _fake_hw.install():
        return
    import time

    from core.txgate import TxTimeout

    tx, gate = _keyed_tx()
    tx.settle_ms = 0
    _fake_hw.DMA.stuck = True
    try:
        tx.start(n=20)
        assert not _ptt_is_off()
        for _ in range(200):  # 波形 1 ms + 余裕 200 ms で抜けるはず
            tx.finish()
            time.sleep_ms(5)
        raise AssertionError("TxTimeout expected")
    except TxTimeout:
        pass
    finally:
        _fake_hw.DMA.stuck = False
    assert _ptt_is_off() and not gate.keyed and not tx.busy


def test_gate_limit_is_capped_by_max_ms():
    if not _fake_hw.install():
        return
    import time

    from core.txgate import FixedFreq, TxGate

    gate = TxGate(_fake_hw.Pin(99), callsign=CALL, allowed=True, freq=FixedFreq(145_000_000),
                  max_ms=20)
    gate.key(10_000)
    assert not gate.expired()
    time.sleep_ms(30)
    assert gate.expired()
    gate.unkey()
    assert not gate.expired()


def test_receiver_stop_does_not_wait_forever():
    if not _fake_hw.install():
        return
    from hw.afsk_rx import Receiver

    rx = Receiver.__new__(Receiver)
    rx.ring = FakeRing()  # pyright: ignore[reportAttributeAccessIssue]
    rx.running = True
    rx.stopped = False  # Core1 が応答しない
    assert rx.stop(timeout_ms=20) is False


def _station_for_close(rx):
    import station

    st = station.Station.__new__(station.Station)
    st.tx, st.gate = _keyed_tx()
    st.rx = rx
    # Fakes in place of the real hardware types
    st.led = _fake_hw.Any()  # pyright: ignore[reportAttributeAccessIssue]
    st.sd = _fake_hw.Any()  # pyright: ignore[reportAttributeAccessIssue]
    st.gnss = _NoTimeGnss()  # pyright: ignore[reportAttributeAccessIssue]
    return st


class _NoTimeGnss:
    """close() が SD に渡す時刻を聞く(時刻は不明)"""

    class rtc:
        synced = False


class RxChecksPtt:
    """close を呼ばれた時点で PTT が切れているかを記録する。interrupt で Ctrl-C を再現する"""

    def __init__(self, interrupt=False):
        self.ptt_off_at_close = None
        self.interrupt = interrupt

    def close(self):
        self.ptt_off_at_close = _ptt_is_off()
        if self.interrupt:
            raise KeyboardInterrupt


def test_station_close_drops_ptt_before_rx():
    if not _fake_hw.install():
        return
    rx = RxChecksPtt()
    st = _station_for_close(rx)
    st.tx.settle_ms = 0
    st.tx.start(n=20)
    assert not _ptt_is_off()
    st.close()
    assert rx.ptt_off_at_close is True


def test_second_ctrl_c_in_close_leaves_ptt_off():
    if not _fake_hw.install():
        return
    rx = RxChecksPtt(interrupt=True)
    st = _station_for_close(rx)
    st.tx.settle_ms = 0
    st.tx.start(n=20)
    try:
        st.close()
        raise AssertionError("KeyboardInterrupt expected")
    except KeyboardInterrupt:
        pass
    assert _ptt_is_off()


def test_main_drops_ptt_when_run_is_interrupted():
    if not _fake_hw.install():
        return
    import station

    tx, gate = _keyed_tx()
    tx.settle_ms = 0
    tx.start(n=20)
    assert not _ptt_is_off()

    def run_interrupted(wdt=None):
        raise KeyboardInterrupt

    keep_run, keep_gate = station.run, station._gate
    station.run, station._gate = run_interrupted, gate
    try:
        station.main()
    finally:
        station.run, station._gate = keep_run, keep_gate
    assert _ptt_is_off()


class Rec:
    """書き込みだけを記録する偽のピン"""

    def __init__(self):
        self.writes = []

    def value(self, v=None):
        if v is not None:
            self.writes.append(v)


def test_gate_refuses_by_default():
    from core.txgate import TxGate, TxRefused

    pin = Rec()
    gate = TxGate(pin)
    assert gate.refuse_reason() == "callsign not set"
    try:
        gate.key()
        raise AssertionError("TxRefused expected")
    except TxRefused:
        pass
    assert 1 not in pin.writes and not gate.keyed


def test_gate_conditions():
    from core.txgate import FixedFreq, TxGate, TxRefused, UnsetFreq

    ok = FixedFreq(145_000_000)
    for kw, why in (
        ({"callsign": None, "allowed": True, "freq": ok}, "callsign not set"),
        ({"callsign": "", "allowed": True, "freq": ok}, "callsign not set"),
        ({"callsign": "NOCALL", "allowed": True, "freq": ok}, "callsign not set"),
        ({"callsign": "q1test", "allowed": True, "freq": ok}, "callsign not set"),
        ({"callsign": CALL, "allowed": False, "freq": ok}, "TX not enabled"),
        ({"callsign": CALL, "allowed": True}, "frequency not set"),  # 既定は未確定
        ({"callsign": CALL, "allowed": True, "freq": UnsetFreq()}, "frequency not set"),
        ({"callsign": CALL, "allowed": True, "freq": FixedFreq(143_999_999)},
         "frequency out of band"),
        ({"callsign": CALL, "allowed": True, "freq": FixedFreq(146_000_001)},
         "frequency out of band"),
    ):
        pin = Rec()
        gate = TxGate(pin, **kw)
        assert gate.refuse_reason() == why, (kw, gate.refuse_reason())
        try:
            gate.key()
            raise AssertionError("TxRefused expected")
        except TxRefused:
            pass
        assert 1 not in pin.writes
    for hz in (144_000_000, 146_000_000):
        gate = TxGate(Rec(), callsign=CALL, allowed=True, freq=FixedFreq(hz))
        assert gate.refuse_reason() is None


def test_gate_active_low_and_tr_order():
    from core.txgate import FixedFreq, TxGate

    log = []

    class Tr:
        def tx(self):
            log.append("tx")

        def rx(self):
            log.append("rx")

    class Pin:
        def value(self, v=None):
            if v is not None:
                log.append(v)

    gate = TxGate(Pin(), False, callsign=CALL, allowed=True, freq=FixedFreq(145_000_000), tr=Tr())
    assert log == [1]  # 負論理: 切った状態は 1
    del log[:]
    gate.key()
    assert log == ["tx", 0]  # 切替のあとで PTT
    del log[:]
    gate.unkey()
    assert log == [1, "rx"]  # PTT を切ってから切替
    del log[:]
    gate.unkey()
    assert log == [1]  # 2 回目は切替を動かさない


def test_display_call():
    from core.txgate import display_call

    assert display_call(None, 7) == "NOCALL-7"
    assert display_call(None) == "NOCALL"
    assert display_call(CALL, 7) == CALL + "-7"
    assert display_call(CALL, 0) == CALL


def test_config_defaults_do_not_transmit():
    import config

    try:
        import config_local  # noqa: F401
    except ImportError:
        assert config.CALLSIGN is None
        assert config.TX_ENABLED is False
        assert config.MODE == "tnc"  # 既定は外付け TNC
        assert config.BENCH_TX_HZ is None


def _station(gate_on=False):
    """本物の Station(偽のハードウェア)。測位あり、ビーコンスイッチはオン"""
    import config
    import station
    from core.txgate import FixedFreq

    _fake_hw.Pin.reset()
    _fake_hw.DMA.stuck = False
    st = station.Station()
    if gate_on:
        st.gate.callsign, st.gate.allowed, st.gate.freq = CALL, True, FixedFreq(145_000_000)
    else:  # 既定の状態にする(src/config_local.py でコールサインと送信の許可を設定していても)
        st.gate.callsign, st.gate.allowed = None, False
    st.call = CALL + "-7"
    st.msgs.set_call(st.call)
    st.channel.rand = lambda n: 0  # チャネルが空いてからの乱数の待ちを 0 に
    st.tx.settle_ms = 0
    st.status.fix, st.status.lat, st.status.lon = 3, 35.68, 139.76
    st.gnss.rtc.last_ms = 0
    st.gnss.gps.speed, st.gnss.gps.course, st.gnss.gps.altitude = 0.0, 0.0, 10.0
    _fake_hw.Pin.state[config.PIN_BEACON_SW] = config.BEACON_SW_ON
    return st


def test_station_default_never_keys_ptt():
    if not _fake_hw.install():
        return
    import config

    st = _station()
    # 既定(コールサイン未設定、送信は許可されていない)では入らない
    st._beacon(0)
    assert _ptt_on_writes(config.PTT_ACTIVE_HIGH) == []
    assert st.status.tx_count == 1 and st._refused == "callsign not set"  # dry run として数える
    # コールサインだけでは入らない。許可(TX_ENABLED)も要る
    st.gate.callsign = CALL
    st.beacon.reset()
    st._beacon(0)
    assert _ptt_on_writes(config.PTT_ACTIVE_HIGH) == []
    assert st.status.tx_count == 2 and st._refused == "TX not enabled"
    st.close()


def test_tnc_gate_does_not_need_a_frequency():
    # 外付け TNC モード: 周波数は無線機側で決まる。コールサインと許可だけで開く
    if not _fake_hw.install():
        return
    import config
    from hw import txctl

    keep = config.CALLSIGN, config.TX_ENABLED
    try:
        config.CALLSIGN, config.TX_ENABLED = CALL, True
        gate = txctl.tnc_gate()
        assert gate.refuse_reason() is None
        config.CALLSIGN = None
        assert txctl.tnc_gate().refuse_reason() == "callsign not set"
        config.CALLSIGN, config.TX_ENABLED = CALL, False
        assert txctl.tnc_gate().refuse_reason() == "TX not enabled"
    finally:
        config.CALLSIGN, config.TX_ENABLED = keep


def test_gate_checks_frequency_unless_told_not_to():
    # 既定(Si5351 モードなど周波数を Pico が決める側)は、これまでどおり周波数を見る
    from core.txgate import TxGate

    assert TxGate(Rec(), callsign=CALL, allowed=True).refuse_reason() == "frequency not set"
    gate = TxGate(Rec(), callsign=CALL, allowed=True, check_freq=False)
    assert gate.refuse_reason() is None
    gate.key()
    assert gate.keyed
    gate.unkey()


def test_station_keys_when_gate_is_open():
    if not _fake_hw.install():
        return
    import config

    st = _station(gate_on=True)
    st._beacon(0)
    assert len(_ptt_on_writes(config.PTT_ACTIVE_HIGH)) == 1
    assert _ptt_is_off() and not st.status.tx_active
    st.close()


def test_beacon_exception_drops_ptt():
    if not _fake_hw.install():
        return
    st = _station(gate_on=True)

    def finish_interrupted():
        assert not _ptt_is_off()
        raise KeyboardInterrupt

    st.tx.finish = finish_interrupted
    try:
        st._beacon(0)
        raise AssertionError("KeyboardInterrupt expected")
    except KeyboardInterrupt:
        pass
    assert _ptt_is_off() and not st.status.tx_active
    st.close()


def _switch(st, on):
    import config

    v = config.BEACON_SW_ON if on else 1 - config.BEACON_SW_ON
    _fake_hw.Pin.state[config.PIN_BEACON_SW] = v


def test_beacon_switch_open_means_off():
    if not _fake_hw.install():
        return
    import config

    assert config.BEACON_SW_ON == 0  # GND に落とすとオン(断線は High でオフ)
    st = _station(gate_on=True)
    del _fake_hw.Pin.state[config.PIN_BEACON_SW]
    _fake_hw.Pin(config.PIN_BEACON_SW, _fake_hw.Pin.IN, _fake_hw.Pin.PULL_UP)  # 開放
    st._beacon(0)
    assert _ptt_on_writes(config.PTT_ACTIVE_HIGH) == [] and st.status.tx_count == 0
    assert not st.status.beacon_on
    _switch(st, True)
    st._beacon(0)
    assert st.status.tx_count == 1 and st.status.beacon_on
    st.close()


def test_no_immediate_beacon_after_abnormal_restart():
    if not _fake_hw.install():
        return
    import time

    import config
    from hw import restart

    restart.set_fails(1)
    try:
        st = _station(gate_on=True)
    finally:
        restart.set_fails(0)
    now = time.ticks_ms()
    st._beacon(now)
    nb = st.status.next_beacon_s
    assert st.status.tx_count == 0 and nb is not None and nb > 0
    st._beacon(time.ticks_add(now, (config.BEACON_RESTART_HOLD_S + 1) * 1000))
    assert st.status.tx_count == 1
    st.close()


def test_stale_fix_stops_beacon():
    if not _fake_hw.install():
        return
    from core.gnssfix import update_status

    st = _station(gate_on=True)
    gps = st.gnss.gps
    gps.valid = True
    gps.time_since_fix = lambda: 60_000
    update_status(st.status, gps)
    st._beacon(0)
    assert st.status.tx_count == 0 and st.status.lat is None
    st.close()


class LogFreq:
    """周波数源の呼ばれ方を記録する偽物"""

    def __init__(self, log, ready=True, fail_on=False):
        self.log = log
        self.ready = ready
        self.fail_on = fail_on

    def tx_hz(self):
        return 145_000_000

    def tx_ready(self):
        self.log.append("ready?")
        return self.ready

    def tx_on(self):
        self.log.append("clk_on")
        if self.fail_on:
            raise OSError("i2c")

    def tx_off(self):
        self.log.append("clk_off")


class LogPin:
    def __init__(self, log):
        self.log = log

    def value(self, v=None):
        if v is not None:
            self.log.append("ptt_on" if v else "ptt_off")


def test_gate_tx_clock_order():
    from core.txgate import TxGate

    log = []
    gate = TxGate(LogPin(log), callsign=CALL, allowed=True, freq=LogFreq(log))
    assert log == ["ptt_off", "clk_off"]  # Pico のリセット後も出力が残らないように切る
    del log[:]
    gate.key()
    assert log == ["ready?", "clk_on", "ptt_on"]
    del log[:]
    gate.unkey()
    assert log == ["ptt_off", "clk_off"]


def test_gate_refuses_when_not_ready():
    from core.txgate import TxGate, TxRefused

    log = []
    gate = TxGate(LogPin(log), callsign=CALL, allowed=True, freq=LogFreq(log, ready=False))
    assert gate.refuse_reason() == "frequency source not ready"
    del log[:]
    try:
        gate.key()
        raise AssertionError("TxRefused expected")
    except TxRefused:
        pass
    assert "clk_on" not in log and "ptt_on" not in log


def test_gate_clock_failure_never_keys_ptt():
    from core.txgate import TxGate

    log = []
    gate = TxGate(LogPin(log), callsign=CALL, allowed=True, freq=LogFreq(log, fail_on=True))
    try:
        gate.key()
        raise AssertionError("OSError expected")
    except OSError:
        pass
    assert "ptt_on" not in log and not gate.keyed and log[-1] == "clk_off"


def test_gate_tx_off_error_of_any_kind_refuses_later():
    # tx_off の例外が OSError でなくても後始末を止めず、PTT を切り、以後は送らない
    from core.txgate import TxGate

    log = []
    freq = LogFreq(log)
    gate = TxGate(LogPin(log), callsign=CALL, allowed=True, freq=freq)
    gate.key()

    def broken_off():
        log.append("clk_off")
        raise ValueError("driver bug")

    freq.tx_off = broken_off
    gate.unkey()
    assert log[-2:] == ["ptt_off", "clk_off"] and not gate.keyed
    assert gate.refuse_reason() == "TX clock off failed"


def test_gate_fault_refuses():
    from core.txgate import TxGate, TxRefused

    log = []
    gate = TxGate(LogPin(log), callsign=CALL, allowed=True, freq=LogFreq(log))
    gate.fault = "watchdog reset scope"
    assert gate.refuse_reason() == "watchdog reset scope"
    del log[:]
    try:
        gate.key()
        raise AssertionError("TxRefused expected")
    except TxRefused:
        pass
    assert "ptt_on" not in log


def test_unset_freq_is_not_ready():
    from core.txgate import UnsetFreq

    assert UnsetFreq().tx_hz() is None and UnsetFreq().tx_ready() is False


class FailingFreq:
    """起動時に出力を止められない周波数源(I2C が使えない)。呼ばれた時点の GP2 を記録する"""

    def __init__(self):
        import config

        self.gp2_at_off = None
        self.pin = config.PIN_PTT

    def tx_hz(self):
        return 145_000_000

    def tx_ready(self):
        return True

    def tx_on(self):
        raise AssertionError("must not enable the TX clock")

    def tx_off(self):
        self.gp2_at_off = _fake_hw.Pin.state.get(self.pin)
        raise OSError("i2c")


class Halted(Exception):
    pass


def _halt():
    raise Halted


def test_startup_i2c_failure_halts_with_gp2_low_on_every_restart():
    if not _fake_hw.install():
        return
    import config
    from hw import restart, txctl

    off = 0 if config.PTT_ACTIVE_HIGH else 1
    resets = []
    machine = _fake_hw.sys.modules["machine"]
    keep = machine.reset, config.CALLSIGN, config.TX_ENABLED
    machine.__dict__["reset"] = lambda: resets.append(1)
    config.CALLSIGN, config.TX_ENABLED = CALL, True  # 送信を許可していても送らない
    restart.set_fails(0)
    try:
        for _ in range(3):  # ウォッチドッグのリセットごとに同じ経路を通る
            _fake_hw.Pin.reset()
            _fake_hw.Pin.state[config.PIN_PTT] = 1 - off  # リセット前の High が残っている場合
            freq = FailingFreq()
            order = []

            def make_freq(f=freq, o=order):
                o.append(_fake_hw.Pin.state[config.PIN_PTT])  # I2C を作る時点
                return f

            try:
                txctl.si5351_gate(make_freq, halt=_halt)
                raise AssertionError("halt expected")
            except Halted:
                pass
            assert order == [off] and freq.gp2_at_off == off  # I2C より先に GP2 が Low
            assert set(_fake_hw.Pin.history[config.PIN_PTT]) == {off}  # High を書かない
        assert resets == [] and restart.fails() == 0  # バックオフの経路に入らない
    finally:
        machine.__dict__["reset"], config.CALLSIGN, config.TX_ENABLED = keep


def test_startup_make_freq_failure_also_halts():
    if not _fake_hw.install():
        return
    import config
    from hw import txctl

    def make_freq():
        raise OSError("no i2c")

    _fake_hw.Pin.reset()
    try:
        txctl.si5351_gate(make_freq, halt=_halt)
        raise AssertionError("halt expected")
    except Halted:
        pass
    assert _fake_hw.Pin.state[config.PIN_PTT] == (0 if config.PTT_ACTIVE_HIGH else 1)


def test_halt_waits_until_interrupted():
    if not _fake_hw.install():
        return
    from hw import txctl

    calls = []

    def sleep(ms):
        calls.append(ms)
        if len(calls) == 3:
            raise KeyboardInterrupt

    keep = txctl.time.sleep_ms
    txctl.time.sleep_ms = sleep
    try:
        txctl.halt_for_watchdog()
        raise AssertionError("must not return")
    except KeyboardInterrupt:
        pass
    finally:
        txctl.time.sleep_ms = keep
    assert len(calls) == 3


def test_si5351_gate_success_is_not_open_by_default_settings():
    if not _fake_hw.install():
        return
    import config
    from core.txgate import FixedFreq
    from hw import txctl

    keep = config.CALLSIGN, config.TX_ENABLED
    config.CALLSIGN, config.TX_ENABLED = None, False
    try:
        gate = txctl.si5351_gate(lambda: FixedFreq(145_000_000), halt=_halt)
        assert gate.refuse_reason() == "callsign not set"
    finally:
        config.CALLSIGN, config.TX_ENABLED = keep


# ---- ウォッチドッグの組み込み(hw/watchdog.py、docs/tx-time-limit.md §4)
def _armed_station(gate_on=True):
    from hw import watchdog

    _fake_hw.WDT.instances.clear()
    wd = watchdog.Watchdog()  # 偽の machine.WDT を使う
    wd.arm(watchdog.LOOP_MS)
    st = _station(gate_on)
    st.wdt = wd
    return st, wd, _fake_hw.WDT.instances[-1]


def test_watchdog_reads_back_the_reset_scope():
    if not _fake_hw.install():
        return
    import machine

    from hw import watchdog

    wd = watchdog.Watchdog()
    wd.arm(watchdog.LOOP_MS)
    assert wd.scope_ok is True  # 実機と同じ値(_fake_hw)
    keep = machine.mem32[watchdog.PSM_WDSEL]
    machine.mem32[watchdog.PSM_WDSEL] = 0x01FFFFF3 & ~0x00200000  # SIO が入らない設定
    try:
        wd.arm(watchdog.LOOP_MS)
        assert wd.scope_ok is False
    finally:
        machine.mem32[watchdog.PSM_WDSEL] = keep


def test_step_feeds_the_watchdog():
    if not _fake_hw.install():
        return
    import time

    st, wd, fake = _armed_station(gate_on=False)
    n = fake.feeds
    st.step(time.ticks_ms())
    st.step(time.ticks_ms())
    assert fake.feeds == n + 2
    st.close()


def test_tx_feeds_once_before_waiting_not_while_waiting():
    # 送信の待ちのループでフィードすると、止まった DMA をウォッチドッグが見つけられない
    if not _fake_hw.install():
        return
    import time

    st, wd, fake = _armed_station(gate_on=True)
    n = fake.feeds
    st._beacon(time.ticks_ms())
    assert st.status.tx_count == 1 and fake.feeds == n + 1
    st.close()


def test_stuck_dma_beacon_is_cut_before_the_watchdog_limit():
    if not _fake_hw.install():
        return
    import time

    from hw import watchdog

    st, wd, fake = _armed_station(gate_on=True)
    _fake_hw.DMA.stuck = True
    t0 = time.ticks_ms()
    try:
        try:
            st._beacon(time.ticks_ms())
            raise AssertionError("TxTimeout expected")
        except Exception as e:
            assert type(e).__name__ == "TxTimeout"
    finally:
        _fake_hw.DMA.stuck = False
    assert _ptt_is_off()
    assert time.ticks_diff(time.ticks_ms(), t0) < watchdog.LOOP_MS
    st.close()


def test_no_immediate_beacon_after_watchdog_reset():
    if not _fake_hw.install():
        return
    import time

    import config
    from hw import watchdog

    keep = watchdog.timed_out
    watchdog.timed_out = lambda: True
    try:
        st = _station(gate_on=True)
    finally:
        watchdog.timed_out = keep
    now = time.ticks_ms()
    st._beacon(now)
    assert st.status.tx_count == 0 and (st.status.next_beacon_s or 0) > 0
    st._beacon(time.ticks_add(now, (config.BEACON_RESTART_HOLD_S + 1) * 1000))
    assert st.status.tx_count == 1
    st.close()


def _main_with(run, sleeps):
    """station.main() を、run を差し替えて動かす。sleeps に待ち時間(ms)を残す。WDT の一覧を返す"""
    import station
    from hw import restart, watchdog

    _fake_hw.WDT.instances.clear()
    _fake_hw.Timer.instances.clear()
    keep = (station.run, station._show_fatal, station.machine.reset,
            watchdog.Watchdog.sleep_s.__defaults__)
    station.run = run
    station._show_fatal = lambda e, wait: None
    station.machine.reset = lambda: sleeps.append("reset")
    watchdog.Watchdog.sleep_s.__defaults__ = (sleeps.append,)
    restart.set_fails(0)
    try:
        station.main()
    finally:
        (station.run, station._show_fatal, station.machine.reset,
         watchdog.Watchdog.sleep_s.__defaults__) = keep
        restart.set_fails(0)
    return list(_fake_hw.WDT.instances), list(_fake_hw.Timer.instances)


def test_main_arms_startup_then_error_wait_feeds_and_resets():
    if not _fake_hw.install():
        return
    from hw import watchdog

    sleeps = []

    def failing_run(duration_ms=None, wdt=None):
        assert wdt is not None
        wdt.arm(watchdog.LOOP_MS)  # run() がループに入るときの設定
        raise RuntimeError("boom")

    wdts, timers = _main_with(failing_run, sleeps)
    assert [w.timeout for w in wdts] == [watchdog.STARTUP_MS, watchdog.LOOP_MS]
    assert sleeps[-1] == "reset" and sum(s for s in sleeps[:-1]) == 10_000  # 最初の失敗は 10 秒待つ
    assert max(s for s in sleeps[:-1]) <= 1000 and wdts[-1].feeds >= 10
    assert timers == []


def test_main_ctrl_c_holds_the_watchdog_with_a_timer():
    if not _fake_hw.install():
        return
    from hw import watchdog

    def interrupted(duration_ms=None, wdt=None):
        raise KeyboardInterrupt

    wdts, timers = _main_with(interrupted, [])
    assert len(timers) == 1 and wdts[-1].timeout == watchdog.HOLD_MS
    before = wdts[-1].feeds
    timers[0].callback(timers[0])
    assert wdts[-1].feeds == before + 1


# ---- 動作モード(config.MODE)
def test_si5351_mode_does_nothing_and_keeps_ptt_off():
    if not _fake_hw.install():
        return
    import config
    import station
    from hw import watchdog

    _fake_hw.WDT.instances.clear()
    _fake_hw.Pin.reset()
    keep = config.MODE, config.CALLSIGN, config.TX_ENABLED
    # Must stay idle even when transmitting is allowed
    config.MODE, config.CALLSIGN, config.TX_ENABLED = "si5351", CALL, True
    _fake_hw.Pin.state[config.PIN_PTT] = 1  # リセット前の High が残っている場合
    wd = watchdog.Watchdog()
    wd.arm(watchdog.STARTUP_MS)
    shown = []
    from ui import notice

    keep_show = notice.show
    notice.show = lambda lines, color, scratch_bytes=0: shown.append(list(lines))
    try:
        station.run(duration_ms=300, wdt=wd)
    finally:
        notice.show = keep_show
        config.MODE, config.CALLSIGN, config.TX_ENABLED = keep
    assert _ptt_on_writes(config.PTT_ACTIVE_HIGH) == []
    assert _fake_hw.Pin.state[config.PIN_PTT] == (0 if config.PTT_ACTIVE_HIGH else 1)
    assert len(shown) == 1 and "NOT COMPLETE" in shown[0]  # 未完成の表示
    # 局は作られていない(受信・GNSS・SD・ビーコンのピンに触れていない)
    for pin in (config.PIN_BEACON_SW, config.PIN_STATUS_LED, config.PIN_SD_CS, config.PIN_AFSK_OUT):
        assert pin not in _fake_hw.Pin.history
    assert _fake_hw.WDT.instances[-1].feeds >= 2  # 待つ間もフィードする


def test_unknown_mode_is_an_error():
    if not _fake_hw.install():
        return
    import config
    import station

    keep = config.MODE
    config.MODE = "bogus"
    try:
        station.run(duration_ms=0)
        raise AssertionError("ValueError expected")
    except ValueError:
        pass
    finally:
        config.MODE = keep


# ---- 設定の変更(core.settings、BLE から)
def test_settings_change_applies_to_running_station_and_switch_still_rules():
    if not _fake_hw.install():
        return
    from test_settings import FakeFs

    import config
    from core.settings import Settings

    fs = FakeFs()  # フラッシュの /settings.json には書かない
    s = Settings(config, "/settings.json", opener=fs.open, remove=fs.remove, rename=fs.rename)
    st = _station()
    s.listeners.append(st.apply_settings)
    try:
        s.set({"callsign": CALL, "ssid": 9, "tx_enabled": True, "fx25_tx": True,
               "beacon_moving_s": 75, "beacon_still_s": 900, "beacon_move_m": 30})
        assert st.gate.callsign == CALL and st.gate.allowed is True
        assert st.call == CALL + "-9" and st.status.callsign == st.call
        assert st.stations.own_call == st.call
        assert st.tx.fx is not None
        assert (st.beacon.moving_s, st.beacon.still_s, st.beacon.move_m) == (75, 900, 30.0)
        # 設定で送信を許可しても、ビーコンスイッチがオフなら送らない
        _fake_hw.Pin.state[config.PIN_BEACON_SW] = 1 - config.BEACON_SW_ON
        st._beacon(0)
        assert _ptt_on_writes(config.PTT_ACTIVE_HIGH) == [] and st.status.tx_count == 0
        # スイッチをオンにすると送る(偽のピン)
        _fake_hw.Pin.state[config.PIN_BEACON_SW] = config.BEACON_SW_ON
        st._beacon(0)
        assert st.status.tx_count == 1 and st.tx.fx_used == 1
        assert len(_ptt_on_writes(config.PTT_ACTIVE_HIGH)) == 1
        # 送信の許可を取り消すと、次のビーコンは dry run
        s.set({"tx_enabled": False})
        assert st.gate.allowed is False and st.tx.fx is not None
        st.beacon.reset()
        st._beacon(0)
        assert st.status.tx_count == 2 and len(_ptt_on_writes(config.PTT_ACTIVE_HIGH)) == 1
    finally:
        s.reset()  # config を元に戻す(ほかのテストのため)
        st.close()


def test_station_feeds_remote_status_tx_and_stations():
    if not _fake_hw.install():
        return
    import time

    from test_remote import Link
    from test_settings import FakeFs

    import config
    from aprs.packet import parse_frame
    from core.remote import Remote
    from core.settings import Settings
    from tnc.ax25 import Frame

    fs = FakeFs()
    s = Settings(config, "/settings.json", opener=fs.open, remove=fs.remove, rename=fs.rename)
    st = _station()
    link = Link()
    st.remote = r = Remote(s, link.write, st.remote_status, st.remote_stations)
    r.attach()
    try:
        _fake_hw.Pin.state[config.PIN_BEACON_SW] = 1 - config.BEACON_SW_ON
        st._beacon(0)
        d = st.remote_status()
        assert d["sw"] is False and d["tx_ready"] is False and d["last_tx_s"] is None
        _fake_hw.Pin.state[config.PIN_BEACON_SW] = config.BEACON_SW_ON
        st._beacon(time.ticks_ms())  # ゲートが拒否する(既定): dry run
        d = st.remote_status()
        assert d["sw"] is True and d["tx_ready"] is False and d["tx_refuse"] == "callsign not set"
        assert d["last_tx_s"] == 0 and d["lat"] == 35.68
        assert "call" not in r._status()  # 未設定のときは省く(LCD の NOCALL は送らない)
        st.gate.callsign, st.gate.allowed = CALL, True
        assert st.remote_status()["tx_ready"] is True
        assert r._status()["call"] == CALL + "-7"
        # 受信: オブジェクトは src が送信局のまま、obj に名前。局の表では obj の行(架空の局)
        q = [bytes(Frame.parse("Q1BBB>APRS:;Q1OBJ    *092345z3550.00N/13955.00E>x").encode())]
        st.rx.get = lambda now_ms=None: q.pop() if q else None
        st._receive(0)
        st.stations.update(parse_frame(Frame.parse("Q1AAA-9>APRS:!3540.12N/13945.67E>x")), 0)
        r.handle(b'{"op": "stations.list", "id": 3}')
        r.poll(0)
        ms = link.take()
        kinds = [m["t"] for m in ms]
        assert "tx" in kinds and kinds.count("station") == 2 and "stations.end" in kinds
        tx = [m for m in ms if m["t"] == "tx"][0]
        assert tx["dry"] is True and tx["tnc2"].startswith(CALL + "-7>")
        stn, obj = [m for m in ms if m["t"] == "station"]
        assert stn["src"] == "Q1AAA-9" and stn["count"] == 1 and abs(stn["lat"] - 35.668) < 0.001
        assert "obj" not in stn and obj["src"] == "Q1OBJ" and obj["obj"] is True
        (rx,) = [m for m in ms if m["t"] == "rx"]
        assert rx["src"] == "Q1BBB" and rx["obj"] == "Q1OBJ" and rx["kind"] == "object"
        assert _ptt_on_writes(config.PTT_ACTIVE_HIGH) == []
    finally:
        st.close()


# ---- 手動のビーコン(config.BEACON_MODE、BLE の beacon.send = Station.send_now)
def _refused(st):
    """send_now() が断ったときの (code, msg, extra)"""
    r = st.send_now()
    assert isinstance(r, tuple), r
    code, msg, extra = r
    return str(code), str(msg), dict(extra or {})


def _manual(on=True):
    import config

    keep = config.BEACON_MODE
    config.BEACON_MODE = "manual" if on else "auto"
    return keep


def test_send_now_needs_the_switch():
    # スマホの指示でも、ビーコンスイッチがオフなら PTT を入れない(どちらのモードでも)
    if not _fake_hw.install():
        return
    import config

    for mode in ("auto", "manual"):
        keep = config.BEACON_MODE
        config.BEACON_MODE = mode
        try:
            st = _station(gate_on=True)
            _switch(st, False)
            assert _refused(st)[0] == "switch_off"
            assert _ptt_on_writes(config.PTT_ACTIVE_HIGH) == [] and st.status.tx_count == 0
            st.close()
        finally:
            config.BEACON_MODE = keep


def test_manual_mode_does_not_beacon_by_itself():
    if not _fake_hw.install():
        return
    import time

    import config

    keep = _manual()
    try:
        st = _station(gate_on=True)
        now = time.ticks_ms()
        for i in range(3):
            st._beacon(time.ticks_add(now, i * 1000_000))  # 間隔の規則なら送っているはずの時間
        assert st.status.tx_count == 0 and st.status.next_beacon_s is None
        assert st.status.beacon_on  # スイッチの状態は読む(LCD と status の sw)
        assert st.remote_status()["beacon_mode"] == "manual"
        assert _ptt_on_writes(config.PTT_ACTIVE_HIGH) == []
        assert st.send_now() == {"dry": False}
        assert st.status.tx_count == 1 and len(_ptt_on_writes(config.PTT_ACTIVE_HIGH)) == 1
        assert _ptt_is_off() and not st.status.tx_active
        st.close()
    finally:
        config.BEACON_MODE = keep


def test_send_now_needs_a_fix_and_time():
    if not _fake_hw.install():
        return
    import config

    st = _station(gate_on=True)
    st.status.fix = 0
    assert _refused(st)[0] == "no_fix"
    st.status.fix = 3
    st.gnss.rtc.last_ms = None  # 時刻が未確定
    assert _refused(st)[0] == "no_fix"
    assert _ptt_on_writes(config.PTT_ACTIVE_HIGH) == [] and st.status.tx_count == 0
    st.close()


def test_send_now_too_soon_survives_switch_toggle():
    if not _fake_hw.install():
        return
    import config

    keep = _manual()
    try:
        st = _station(gate_on=True)
        assert st.send_now() == {"dry": False}
        code, _msg, extra = _refused(st)
        assert code == "too_soon" and 0 < extra["wait_s"] <= config.BEACON_MANUAL_MIN_S
        _switch(st, False)  # スイッチを入れ直しても(beacon.reset())連打の制限は消えない
        st._beacon(0)
        _switch(st, True)
        st._beacon(0)
        assert _refused(st)[0] == "too_soon"
        assert st.status.tx_count == 1 and len(_ptt_on_writes(config.PTT_ACTIVE_HIGH)) == 1
        st._tx_ms = None  # 時間がたったことにする
        assert st.send_now() == {"dry": False} and st.status.tx_count == 2
        st.close()
    finally:
        config.BEACON_MODE = keep


def test_send_now_is_dry_when_gate_refuses():
    if not _fake_hw.install():
        return
    import config

    st = _station(gate_on=False)
    assert st.send_now() == {"dry": True}
    assert st.status.tx_count == 1 and _ptt_on_writes(config.PTT_ACTIVE_HIGH) == []
    st.close()


def test_send_now_in_auto_mode_restarts_the_interval():
    if not _fake_hw.install():
        return
    import time

    keep = _manual(False)
    try:
        st = _station(gate_on=True)
        now = time.ticks_ms()
        st._beacon(now)  # スイッチを入れて最初の 1 回
        assert st.status.tx_count == 1
        st._tx_ms = None  # 連打の制限を外す
        assert st.send_now() == {"dry": False} and st.status.tx_count == 2
        st._beacon(time.ticks_ms())
        assert st.status.tx_count == 2  # 送った時点から数え直す(すぐには送らない)
        assert st.status.next_beacon_s and st.status.next_beacon_s > 0
        st.close()
    finally:
        import config

        config.BEACON_MODE = keep


def test_send_now_ignores_restart_hold():
    # 再起動直後の hold は自動の送出だけ。人の操作(beacon.send)では送る
    if not _fake_hw.install():
        return
    import time

    from hw import restart

    restart.set_fails(1)
    try:
        st = _station(gate_on=True)
    finally:
        restart.set_fails(0)
    st._beacon(time.ticks_ms())
    assert st.status.tx_count == 0
    assert st.send_now() == {"dry": False} and st.status.tx_count == 1
    st.close()


def test_send_now_exception_drops_ptt_and_stops_the_station():
    # 送信中の例外は remote / BLE の except で消さず、step() から上げる(自動の送出と同じ)
    if not _fake_hw.install():
        return
    import time

    keep = _manual()
    try:
        st = _station(gate_on=True)
        finish = st.tx.finish

        def finish_broken():
            assert not _ptt_is_off()
            raise RuntimeError("dma")

        st.tx.finish = finish_broken
        code, msg, _extra = _refused(st)
        assert code == "internal" and "dma" in msg
        assert _ptt_is_off() and not st.status.tx_active
        st.tx.finish = finish
        try:
            st.step(time.ticks_ms())
            raise AssertionError("RuntimeError expected")
        except RuntimeError as e:
            assert str(e) == "dma"
        assert st._fatal is None
        st.close()
    finally:
        import config

        config.BEACON_MODE = keep


def test_beacon_mode_setting_applies_to_station():
    if not _fake_hw.install():
        return
    from test_settings import FakeFs

    import config
    from core.settings import Settings

    fs = FakeFs()
    s = Settings(config, "/settings.json", opener=fs.open, remove=fs.remove, rename=fs.rename)
    st = _station(gate_on=True)
    s.listeners.append(st.apply_settings)
    try:
        s.set({"beacon_mode": "manual"})
        assert st.status.beacon_mode == "manual"
        st._beacon(0)
        assert st.status.tx_count == 0
        s.set({"beacon_mode": "auto"})
        st._beacon(0)
        assert st.status.tx_count == 1  # 自動に戻すと規則どおり(前回が無ければすぐ)
    finally:
        s.reset()
        st.close()


# ---- APRS メッセージ(Station.msg_send、自動の ack)とチャネルの判定
def _feed(st, *tnc2):
    """受信したことにするフレーム(TNC2 の文字列)"""
    from tnc.ax25 import Frame

    q = [bytes(Frame.parse(t).encode()) for t in reversed(tnc2)]
    st.rx.get = lambda now_ms=None: q.pop() if q else None


def test_receive_table_error_still_answers_messages():
    # 局の表の更新で例外が出ても、自局宛のメッセージは受けて ack を返す(別の try)
    if not _fake_hw.install():
        return
    import time

    st = _station()
    try:
        def broken(*a, **kw):
            raise ValueError("table bug")

        st.stations.update = broken
        _feed(st, "Q1AAA>APRS::%-9s:hi{5" % st.msgs.own)
        st._receive(time.ticks_ms())
        assert st.status.rx_count == 1 and st.msgs.unread == 1
        assert [a[:2] for a in st.msgs._acks] == [["Q1AAA", "5"]]
    finally:
        st.close()


def test_remote_error_stops_only_ble():
    # スマホへのイベントで例外が出たら、BLE だけを止めて後で起動し直す。受信は続ける
    if not _fake_hw.install():
        return
    import time

    st = _station()
    try:
        class BrokenRemote:
            def rx(self, d):
                raise RuntimeError("remote bug")

        ble = _FakeBle()
        st.ble, st.remote = ble, BrokenRemote()
        _feed(st, "Q1AAA>APRS:>one", "Q1AAA>APRS:>two")
        st._receive(time.ticks_ms())
        assert st.status.rx_count == 2  # 2 つ目も受けた
        assert st.ble is None and st.remote is None and ble.stopped
        assert st._ble_retry is not None and st._ble_retry.at is not None
    finally:
        st.close()


def _no(st, to, text):
    """msg_send() が断ったときの (code, msg, extra)"""
    r = st.msg_send(to, text)
    assert isinstance(r, tuple), r
    return r


def test_msg_send_needs_switch_and_gate():
    if not _fake_hw.install():
        return
    import time

    import config

    st = _station()
    try:
        r = _no(st, "Q1AAA", "a|b")  # 値の検査が最初(スイッチがオンでなくても)
        assert r[0] == "invalid" and r[2] == {"key": "text"}
        _fake_hw.Pin.state[config.PIN_BEACON_SW] = 1 - config.BEACON_SW_ON
        assert _no(st, "Q1AAA", "hi")[0] == "switch_off"
        _fake_hw.Pin.state[config.PIN_BEACON_SW] = config.BEACON_SW_ON
        assert _no(st, "Q1AAA", "hi")[:2] == ("tx_not_ready", "callsign not set")
        assert _no(st, CALL + "-7", "hi")[0] == "tx_not_ready"  # ゲートが先
        st.gate.callsign, st.gate.allowed = CALL, True
        assert _no(st, CALL + "-7", "hi") == ("invalid", "own callsign", {"key": "to"})
        d = st.msg_send("Q1AAA", "hi")
        assert isinstance(d, dict) and d["state"] == "pending" and d["to"] == "Q1AAA"
        # 積んだあとでゲートが閉じたら送らない(dry run にもしない)
        st.gate.allowed = False
        st._messages(time.ticks_ms())
        assert st.status.tx_count == 0 and _ptt_on_writes(config.PTT_ACTIVE_HIGH) == []
        assert st.msgs.items[0].tries == 0
    finally:
        st.close()


def test_message_is_sent_when_channel_clear_and_retried():
    if not _fake_hw.install():
        return
    import time

    import config

    st = _station(gate_on=True)
    try:
        st.msg_send("Q1AAA-9", "Hello")
        now = time.ticks_ms()
        st.channel.update(-10, now)  # スケルチが開いている: 待つ
        st._messages(now)
        assert st.status.tx_count == 0 and _ptt_on_writes(config.PTT_ACTIVE_HIGH) == []
        st.channel.update(-60, time.ticks_add(now, 100))
        st.channel.update(-60, time.ticks_add(now, 600))  # テール 0.5 秒のあと空き
        st._messages(time.ticks_add(now, 600))
        assert st.status.tx_count == 1 and len(_ptt_on_writes(config.PTT_ACTIVE_HIGH)) == 1
        m = st.msgs.items[0]
        assert m.tries == 1 and m.state == "pending"
        st._messages(time.ticks_ms())  # 30 秒たつまで再送しない
        assert st.status.tx_count == 1
        # 相手の ack
        _feed(st, "Q1AAA-9>APRS,WIDE1-1*::%-9s:ack%s" % (CALL + "-7", m.no))
        st._receive(time.ticks_ms())
        assert m.state == "acked" and st.msgs.pending() == 0
    finally:
        st.close()


def test_incoming_message_gets_ack_only_with_switch_on():
    if not _fake_hw.install():
        return
    import time

    import config

    st = _station(gate_on=True)
    try:
        _fake_hw.Pin.state[config.PIN_BEACON_SW] = 1 - config.BEACON_SW_ON
        _feed(st, "Q1BBB>APRS,WIDE1-1*::%-9s:Hi there{42" % (CALL + "-7"))
        t = time.ticks_ms()
        st._receive(t)
        assert st.msgs.unread == 1 and st.status.message == ("Q1BBB", "Hi there")
        st._messages(time.ticks_add(t, 2000))
        assert st.status.tx_count == 0 and _ptt_on_writes(config.PTT_ACTIVE_HIGH) == []
        _fake_hw.Pin.state[config.PIN_BEACON_SW] = config.BEACON_SW_ON
        st._messages(time.ticks_add(t, 100))  # 受信の直後(0.5 秒)は待つ
        assert st.status.tx_count == 0
        st._messages(time.ticks_add(t, 600))
        assert st.status.tx_count == 1 and len(_ptt_on_writes(config.PTT_ACTIVE_HIGH)) == 1
        st._messages(time.ticks_add(time.ticks_ms(), 10_000))
        assert st.status.tx_count == 1  # ack は 1 回だけ
    finally:
        st.close()


def test_auto_beacon_waits_for_clear_channel_dry_run_does_not():
    if not _fake_hw.install():
        return
    import time

    import config

    st = _station()
    try:
        now = time.ticks_ms()
        st.channel.update(-10, now)
        st._beacon(now)  # ゲートが拒否: dry run は電波を出さないので待たない
        assert st.status.tx_count == 1
        st.gate.callsign, st.gate.allowed = CALL, True
        st.beacon.reset()
        st._beacon(now)
        assert st.status.tx_count == 1 and st.status.next_beacon_s == 0  # 使用中: 待つ
        st.channel.update(-60, time.ticks_add(now, 100))
        st.channel.update(-60, time.ticks_add(now, 600))
        st._beacon(time.ticks_add(now, 600))
        assert st.status.tx_count == 2 and len(_ptt_on_writes(config.PTT_ACTIVE_HIGH)) == 1
        # 送信の直後(MSG_GAP_MS)は、次の自動の送信をしない
        st.beacon.reset()
        st._beacon(time.ticks_ms())
        assert st.status.tx_count == 2
    finally:
        st.close()


def test_longest_message_fits_tx_buffer_with_fx25():
    if not _fake_hw.install():
        return
    import config
    from aprs.message import info
    from tnc.ax25 import Address, Frame

    st = _station()
    try:
        st.tx.set_fx25(True)
        f = Frame(Address(config.TOCALL), Address.parse("Q1AAAA-15"),
                  [Address.parse("WIDE1-1"), Address.parse("WIDE2-2")],
                  info("Q1BBBB-15", "x" * 67, "99999"))
        n = st.tx.build([bytes(f.encode())])  # 入りきらなければ ValueError
        ms = n * 1000 // config.FS
        assert st.tx.fx_used == 1 and ms < config.TX_BUFFER_MS, ms
        print("[test] longest message %d ms (FX.25)" % ms)
    finally:
        st.close()


def _days_ago(now, days):
    """now の days 日前の ticks_ms(実機の ticks_add は ±2^29 ms までしか受けないので刻む)"""
    import time

    t = now
    for _ in range(days):
        t = time.ticks_add(t, -86_400_000)
    return t


def test_station_still_transmits_after_a_week_of_listening():
    # 受信だけで 7 日動かしたあと: 前回の送信が ticks の一周で「ついさっき」に見えて、
    # 自動の送信(_channel_ok)も BLE の beacon.send(too_soon)も止まっていた
    if not _fake_hw.install():
        return
    import time

    keep = _manual()
    try:
        st = _station(gate_on=True)
        now = time.ticks_ms()
        t = _days_ago(now, 7)
        st._tx_ms = st._air_ms = t
        assert time.ticks_diff(now, t) < 0  # 片付けなければ未来に見える
        for _ in range(7 * 24):  # 局のループの _sweep(ここでは 1 時間ごと)で now まで進める
            t = time.ticks_add(t, 3600_000)
            st._sweep(t)
        assert st._channel_ok(time.ticks_ms())
        assert st.send_now() == {"dry": False}
        st.close()
    finally:
        import config

        config.BEACON_MODE = keep


class _FakeBle:
    authenticated = False
    conn = None
    advertising_held = False

    def __init__(self, fail=False):
        self.fail = fail
        self.stopped = False

    def poll(self, now):
        if self.fail:
            raise ValueError("boom")

    def stop(self):
        self.stopped = True



def test_ble_state_shows_the_advertising_hold():
    # ペアリングの失敗が続いて広告を止めている間は、LCD のアイコンを "hold" にする
    if not _fake_hw.install():
        return
    import time

    st = _station(gate_on=False)
    try:
        ble = _FakeBle()
        st.ble = ble
        assert st._ble_state() == "adv"
        ble.advertising_held = True
        st.step(time.ticks_ms())
        assert st._ble_state() == "hold" and st.status.ble == "hold"
    finally:
        st.close()


def test_ble_error_stops_ble_and_restarts_it_later():
    # BLE の処理のエラーで局は止めない。BLE は止めて、待ってから起動し直す(失敗のたびに待ちを倍に)
    if not _fake_hw.install():
        return
    import time

    import upkeep

    st = _station(gate_on=False)
    bad = _FakeBle(fail=True)
    st.ble = bad
    started = []

    def start_ble():
        started.append(1)
        st.ble = _FakeBle(fail=len(started) == 1)

    st.start_ble = start_ble
    t0 = time.ticks_ms()
    st.step(t0)
    assert st.ble is None and bad.stopped and started == []
    st.step(time.ticks_add(t0, upkeep.BLE_RETRY_MS - 1))
    assert started == []
    t1 = time.ticks_add(t0, upkeep.BLE_RETRY_MS)
    st.step(t1)
    assert started == [1] and st.ble is not None
    st.step(time.ticks_add(t1, 1))  # 起動し直した BLE もエラー: 次は倍の待ち
    assert st.ble is None
    t2 = time.ticks_add(t1, 1 + 2 * upkeep.BLE_RETRY_MS)
    st.step(time.ticks_add(t2, -1))
    assert started == [1]
    st.step(t2)
    assert started == [1, 1] and st.ble is not None
    st.step(time.ticks_add(t2, 1))
    assert st.ble is not None  # 今度は動き続ける
    st.close()
