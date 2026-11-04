"""PTT の本物のピンと core.txgate を結びつける。ピンの割り当ては config のとおり。"""

from machine import Pin

import config
from core.txgate import AudioOnly, TxGate


def _ptt_pin():
    return Pin(config.PIN_PTT, Pin.OUT, value=0 if config.PTT_ACTIVE_HIGH else 1)


def station_gate():
    """局の送信に使うゲート"""
    return TxGate(_ptt_pin(), config.PTT_ACTIVE_HIGH)


def audio_only():
    """デモと試験用: PTT のピンを切った状態にして、音声だけを出すゲートを返す"""
    _ptt_pin()
    return AudioOnly()


def bench_gate():
    """tx_demo 用。config.BENCH_TX_HZ を明示したときだけ PTT を入れる。それ以外は音声だけ"""
    return audio_only() if config.BENCH_TX_HZ is None else station_gate()
