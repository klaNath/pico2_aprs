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

    def run_interrupted():
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
    # コールサインと許可をそろえても、周波数が未確定なら入らない
    st.gate.callsign, st.gate.allowed = CALL, True
    st._beacon(0)
    assert _ptt_on_writes(config.PTT_ACTIVE_HIGH) == []
    assert st.status.tx_count == 1 and st._refused == "frequency not set"  # dry run として数える
    st.close()


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

    def __init__(self, log, lock=True, fail_on=False):
        self.log = log
        self.lock = lock
        self.fail_on = fail_on

    def tx_hz(self):
        return 145_000_000

    def locked(self):
        self.log.append("locked?")
        return self.lock

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
    assert log == ["locked?", "clk_on", "ptt_on"]
    del log[:]
    gate.unkey()
    assert log == ["ptt_off", "clk_off"]


def test_gate_refuses_when_not_locked():
    from core.txgate import TxGate, TxRefused

    log = []
    gate = TxGate(LogPin(log), callsign=CALL, allowed=True, freq=LogFreq(log, lock=False))
    assert gate.refuse_reason() == "frequency source not locked"
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


def test_unset_freq_is_not_locked():
    from core.txgate import UnsetFreq

    assert UnsetFreq().tx_hz() is None and UnsetFreq().locked() is False
