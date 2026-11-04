"""PTT の本物のピンと core.txgate を結びつける。ピンの割り当ては config のとおり。"""

from machine import Pin

import config
from core.txgate import AudioOnly, FixedFreq, TxGate, UnsetFreq


def _ptt_pin():
    return Pin(config.PIN_PTT, Pin.OUT, value=0 if config.PTT_ACTIVE_HIGH else 1)


def _gate(freq):
    return TxGate(_ptt_pin(), config.PTT_ACTIVE_HIGH, callsign=config.CALLSIGN,
                  allowed=config.TX_ENABLED, freq=freq)


def station_gate():
    """局の送信に使うゲート。Si5351 のドライバができるまで周波数は未確定なので、常に拒否する"""
    return _gate(UnsetFreq())


def audio_only():
    """デモと試験用: PTT のピンを切った状態にして、音声だけを出すゲートを返す"""
    _ptt_pin()
    return AudioOnly()


def bench_gate():
    """tx_demo 用。config.BENCH_TX_HZ を明示したときだけ PTT を入れるゲートを返す。
    このときもコールサインと config.TX_ENABLED が要る。明示がなければ音声だけ"""
    return audio_only() if config.BENCH_TX_HZ is None else _gate(FixedFreq(config.BENCH_TX_HZ))
