"""送信の安全(PTT の扱い)のテスト。偽のハードウェアで PC 上で動かす。実機では何もしない
(実機で PTT を動かさないため。tests/_fake_hw.py)。"""

import _fake_hw


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

    keep = config.BENCH_TX_HZ
    try:
        config.BENCH_TX_HZ = None
        assert isinstance(txctl.bench_gate(), AudioOnly)
        config.BENCH_TX_HZ = 145_000_000
        assert not isinstance(txctl.bench_gate(), AudioOnly)
    finally:
        config.BENCH_TX_HZ = keep


class FakeRing:
    def stop(self):
        pass

    def close(self):
        pass


def _keyed_tx():
    """本物の AfskTx + 偽の DMA。PTT の状態は偽の Pin(GP2)で見る"""
    import config
    from hw import txctl
    from hw.afsk_tx import AfskTx

    _fake_hw.Pin.reset()
    _fake_hw.DMA.stuck = False
    gate = txctl.station_gate()
    return AfskTx(config.PIN_AFSK_OUT, gate, config.FS, config.PWM_TOP, max_ms=1000), gate


def _ptt_is_off():
    import config

    return _fake_hw.Pin.state[config.PIN_PTT] == (0 if config.PTT_ACTIVE_HIGH else 1)


def test_stuck_dma_times_out_and_drops_ptt():
    if not _fake_hw.install():
        return
    from core.txgate import TxTimeout

    tx, gate = _keyed_tx()
    tx.settle_ms = 0
    _fake_hw.DMA.stuck = True
    try:
        tx.start(n=20)
        assert not _ptt_is_off()
        for _ in range(200):  # 波形 1 ms + 余裕 200 ms で抜けるはず
            tx.finish()
            __import__("time").sleep_ms(5)
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

    from core.txgate import TxGate

    gate = TxGate(_fake_hw.Pin(99), max_ms=20)
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
