# PIO のアセンブラ(jmp, mov など)は rp2.asm_pio が関数の中に注入するので未定義の名前の検査を外す
# ruff: noqa: F821
# pyright: reportUndefinedVariable=false
"""455 kHz の FM IF(リミッタの出力の方形波)をディジタル入力で受け、エッジの時刻で周波数を測る。

    python tools/dev.py run src/fm_if_test.py -t 30

信号: GP8(Pico の 11 番ピン)に 0-3.3 V の方形波。455 kHz、1.2 kHz のサインで ±3.5 kHz の FM。
仕組み: PIO が 2 クロックごとに減る数を持ち、立ち上がりエッジのたびにその値を FIFO に出す。
DMA で CAPTURE 個を取り込み、M エッジごとの「エッジ数 / 時間」で瞬時周波数を出す(逆数計数)。
時刻を測るので、量子化の誤差は各区間の両端の分だけで、区間の中では積み重ならない。
PIO の 1 周期のクロック数は 2 * (減った数) + 3(下の edge_ts のコメント)。
"""

import math
import time
from array import array

import rp2
from machine import Pin, freq

PIN = 8
CAPTURE = 9100  # 455 kHz で約 20 ms(1.2 kHz の変調で 24 周期)
M = 24  # 1 区間のエッジ数(約 52.7 us = 19 kHz、受信の 19.2 kHz と同じくらい)
MOD_HZ = 1200
_PIO0_RXF0 = 0x50200000 + 0x20
_DREQ_PIO0_RX0 = 4


@rp2.asm_pio(autopush=True, push_thresh=32)
def edge_ts():
    # x は 2 クロックごとに 1 減る。立ち上がりエッジで x を FIFO へ出す。
    # 1 周期 = High の待ち(2 クロック/回)+ High から Low へ 2 クロック
    #         + Low の待ち(2 クロック/回)+ 立ち上がりで 3 クロック(その中で 1 減る)
    #        = 2 * (減った数) + 3 クロック
    # x が 0 になると(約 57 秒に 1 回)待ちの状態を取り違えるが、この試験は 20 ms なので構わない
    mov(x, invert(null))
    wrap_target()
    label("hi")
    jmp(pin, "hi_dec")
    jmp("lo")
    label("hi_dec")
    jmp(x_dec, "hi")
    label("lo")
    jmp(pin, "rise")
    jmp(x_dec, "lo")
    label("rise")
    in_(x, 32)
    jmp(x_dec, "hi")
    wrap()


def capture(n, setup_pin=True):
    """GP8 の立ち上がりの時刻(PIO の減る数)を n 個取る。信号が無ければ None。
    setup_pin=False はピンの設定を変えない(自己試験で GP8 を PWM 出力にしたまま読む)"""
    pin = Pin(PIN, Pin.IN, Pin.PULL_DOWN) if setup_pin else Pin(PIN)  # 未接続のとき浮かないように
    buf = array("I", bytes(4 * n))
    sm = rp2.StateMachine(0, edge_ts, jmp_pin=pin, in_base=pin)
    dma = rp2.DMA()
    try:
        dma.config(read=_PIO0_RXF0, write=buf, count=n,
                   ctrl=dma.pack_ctrl(size=2, inc_read=False, inc_write=True,
                                      treq_sel=_DREQ_PIO0_RX0))
        dma.active(1)
        sm.active(1)
        t0 = time.ticks_ms()
        while dma.active():
            if time.ticks_diff(time.ticks_ms(), t0) > 500:
                return None
            time.sleep_ms(2)
        return buf
    finally:
        sm.active(0)
        dma.active(0)
        dma.close()
        rp2.PIO(0).remove_program(edge_ts)


def windows(ts, clk, m):
    """m エッジごとの区間 -> (中央の時刻 array('f') 秒, 瞬時周波数 array('f') Hz, 全体の秒)。
    区間の減った数は両端の時刻の差だけで出る(1 周期ずつのリストは作らない。float は
    1 個ずつヒープに確保されるので、9100 個のリストだと 100 KB を超える)"""
    n = (len(ts) - 1) // m
    t = array("f", bytes(4 * n))
    f = array("f", bytes(4 * n))
    acc = 0  # クロック数
    for w in range(n):
        cycles = 2 * ((ts[w * m] - ts[(w + 1) * m]) & 0xFFFFFFFF) + 3 * m
        t[w] = (acc + cycles / 2) / clk
        f[w] = m * clk / cycles
        acc += cycles
    return t, f, acc / clk


def fit(t, f, hz):
    """f に hz のサインを最小二乗で当てはめる -> (平均, 振幅, 残差の rms)"""
    n = len(f)
    fm = sum(f) / n
    a_c = a_s = 0.0
    for k in range(n):
        y = f[k] - fm
        a_c += y * math.cos(2 * math.pi * hz * t[k])
        a_s += y * math.sin(2 * math.pi * hz * t[k])
    a_c = 2 * a_c / n
    a_s = 2 * a_s / n
    r2 = 0.0
    for k in range(n):
        ph = 2 * math.pi * hz * t[k]
        r = f[k] - fm - a_c * math.cos(ph) - a_s * math.sin(ph)
        r2 += r * r
    return fm, math.sqrt(a_c * a_c + a_s * a_s), math.sqrt(r2 / n)


def main(setup_pin=True):
    clk = freq()
    print("sysclk %d Hz, GP%d, capture %d edges" % (clk, PIN, CAPTURE))
    ts = capture(CAPTURE, setup_pin)
    if ts is None:
        print("no signal on GP%d (0-3.3 V square wave expected)" % PIN)
        return
    dmin = dmax = (ts[0] - ts[1]) & 0xFFFFFFFF
    for i in range(2, len(ts)):
        d = (ts[i - 1] - ts[i]) & 0xFFFFFFFF
        dmin = min(dmin, d)
        dmax = max(dmax, d)
    t, f, span = windows(ts, clk, M)
    print("span %.3f ms, mean %.3f kHz, period min/max %.3f / %.3f us" % (
        span * 1e3, len(f) * M / span / 1e3, (2 * dmin + 3) / clk * 1e6,
        (2 * dmax + 3) / clk * 1e6))
    fm, amp, rms = fit(t, f, MOD_HZ)
    rate = len(f) / span
    snr = 20 * math.log10(amp / math.sqrt(2) / rms) if rms > 0 and amp > 0 else 0
    print("windows %d (M=%d, %.1f kHz), center %.3f kHz, deviation +/-%.3f kHz" % (
        len(f), M, rate / 1e3, fm / 1e3, amp / 1e3))
    print("fit residual %.1f Hz rms -> SNR %.1f dB (in %.1f kHz bandwidth)" % (
        rms, snr, rate / 2e3))
    # 区間の長さを変えたときの残差(量子化の雑音が区間の長さに反比例して減るかの確認)
    for m2 in (12, 48):
        t2, f2, _ = windows(ts, clk, m2)
        print("  M=%d: residual %.1f Hz rms" % (m2, fit(t2, f2, MOD_HZ)[2]))


if __name__ == "__main__":
    main()
