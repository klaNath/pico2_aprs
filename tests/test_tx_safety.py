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
