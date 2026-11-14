"""PTT の本物のピンと core.txgate を結びつける。ピンの割り当ては config のとおり。"""

import time

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


def halt_for_watchdog():
    """起動時に周波数源の出力を止められなかったときの動作(オーナー決定 2026-11-05)。
    何もせず待ち、フィードしないのでウォッチドッグのリセットで再起動を繰り返す。

    station.main が起動中(hw.watchdog.STARTUP_MS = 16 秒)のウォッチドッグを有効にしているので、
    ここでフィードしなければ 16 秒後にリセットされ、また同じ経路を通る(PTT は切れたまま、送信なし、
    受信と表示なし)。dev.py run/exec のマウント中はウォッチドッグを有効にしないので、そこでは永久に
    止まる(USB の Ctrl-C だけで抜ける)。例外にしないのは、station.main の異常時の経路(hw.restart の
    バックオフ、machine.reset)に入れないため。失敗回数も増やさない"""
    while True:
        time.sleep_ms(100)


def si5351_gate(make_freq, halt=halt_for_watchdog):
    """Si5351 を周波数源にするゲートを作る(局への組み込みはまだ。hw.si5351 の必須手順)。
    make_freq: I2C と Si5351B を作って返す関数。GP2 を Low にしてから呼ぶ。
    I2C のエラーで出力を止められなければ halt を呼ぶ(ゲートは返さないので送信しない)"""
    pin = _ptt_pin()  # I2C より先に確定する
    try:
        return TxGate(pin, config.PTT_ACTIVE_HIGH, callsign=config.CALLSIGN,
                      allowed=config.TX_ENABLED, freq=make_freq())
    except OSError as e:
        print("[tx] cannot stop Si5351 outputs:", e)
        halt()
        raise  # halt が戻ったとき(試験用)。送信できる状態では戻らない


def audio_only():
    """デモと試験用: PTT のピンを切った状態にして、音声だけを出すゲートを返す"""
    _ptt_pin()
    return AudioOnly()


def bench_gate():
    """tx_demo 用。config.BENCH_TX_HZ を明示したときだけ PTT を入れるゲートを返す。
    このときもコールサインと config.TX_ENABLED が要る。明示がなければ音声だけ"""
    return audio_only() if config.BENCH_TX_HZ is None else _gate(FixedFreq(config.BENCH_TX_HZ))
