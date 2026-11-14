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
    rx.ring = FakeRing()
    rx.running = True
    rx.stopped = False  # Core1 が応答しない
    assert rx.stop(timeout_ms=20) is False


def _station_for_close(rx):
    import station

    st = station.Station.__new__(station.Station)
    st.tx, st.gate = _keyed_tx()
    st.rx = rx
    st.led = _fake_hw.Any()
    st.sd = _fake_hw.Any()
    return st


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
    st.call = CALL + "-7"
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
    assert st.status.tx_count == 0 and st.status.next_beacon_s > 0
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
    keep = _fake_hw.sys.modules["machine"].reset, config.CALLSIGN, config.TX_ENABLED
    _fake_hw.sys.modules["machine"].reset = lambda: resets.append(1)
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
        _fake_hw.sys.modules["machine"].reset, config.CALLSIGN, config.TX_ENABLED = keep


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
    config.MODE, config.CALLSIGN, config.TX_ENABLED = "si5351", CALL, True  # 許可していても動かさない
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
