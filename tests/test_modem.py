"""AFSK モデム(tnc.afsk_mod / tnc.hdlc / modem.demod)のテスト。PC と実機の両方で動く。

劣化条件での性能評価は tools/afsk_bench.py(PC)で行う。ここでは
・送受信のループバックで正しく復号できること
・HDLC のビットスタッフィング/デフレーマが正しいこと
・雑音を入れても壊れない(バッファを溢れさせない)こと
・実機でリアルタイムに間に合い、処理中にメモリ確保をしないこと(test_hw_*)
を確認する。
"""

import gc
import time
from array import array

from modem.demod import PROFILE_HUM, PROFILE_NOISE, Demodulator
from tnc.afsk_mod import Modulator
from tnc.ax25 import Frame
from tnc.hdlc import Deframer, encode_levels, levels_size

TEXTS = (
    "JA1XYZ-9>APRS,WIDE1-1:!3540.12N/13945.67E>test",
    "JA1ABC>SU4PX7,WIDE2-1:`CJ\x1fn4v>/\"4?}Pico2",
    "7K1DEF-15>APRS:>" + "\x7f" * 20,  # ビットスタッフィングが多い
)


def _frames():
    return [bytes(Frame.parse(t).encode()) for t in TEXTS]


def _modulate(frames, fs):
    mod = Modulator(fs, mid=2048, amp=500)
    lv = bytearray(sum(levels_size(len(f), 16, 2) for f in frames))
    n = 0
    last = 0
    for f in frames:
        k, last = encode_levels(f, memoryview(lv)[n:], 16, 2, last)
        n += k
    out = array("H", bytes(2 * (mod.samples_size(n) + fs // 5)))
    pos = mod.silence(out, 0, fs // 10)
    pos = mod.modulate(lv, n, out, pos)
    pos = mod.silence(out, pos, fs // 10)
    return out, pos


def _demod_all(samples, n, fs, block=512):
    dem = Demodulator(fs, max_block=block)
    dfr = Deframer(4)
    got = []
    mv = memoryview(samples)
    for off in range(0, n, block):
        k = min(block, n - off)
        nb = dem.process(mv[off : off + k], k)
        dfr.feed(dem.bits, nb)
        while True:
            m = dfr.get()
            if m is None:
                break
            got.append(bytes(m))
            dfr.done()
    return got


def _loopback(fs):
    frames = _frames()
    samples, n = _modulate(frames, fs)
    assert _demod_all(samples, n, fs) == frames


def test_loopback_19200():
    _loopback(19200)


def test_loopback_13200():
    _loopback(13200)


def test_hdlc_levels_to_deframer():
    # モデムを通さず、NRZI レベル → データビット → デフレーマ
    frames = _frames()
    for f in frames:
        lv = bytearray(levels_size(len(f), 3, 2))
        n, _ = encode_levels(f, lv, 3, 2)
        bits = bytearray(n)
        prev = 0
        for i in range(n):
            bits[i] = 1 if lv[i] == prev else 0
            prev = lv[i]
        d = Deframer(2)
        assert d.feed(bits, n) == 1
        m = d.get()
        assert m is not None and bytes(m) == f


def test_deframer_overrun():
    f = _frames()[0]
    lv = bytearray(levels_size(len(f), 2, 1) * 3)
    n = 0
    last = 0
    for _ in range(3):
        k, last = encode_levels(f, memoryview(lv)[n:], 2, 1, last)
        n += k
    bits = bytearray(n)
    prev = 0
    for i in range(n):
        bits[i] = 1 if lv[i] == prev else 0
        prev = lv[i]
    d = Deframer(2)  # スロット 2 = 同時に保持できるのは 1 フレーム
    assert d.feed(bits, n) == 1
    assert d.overruns == 2


def test_noise_does_not_overflow():
    # 擬似乱数の雑音(LCG)。PLL が速く回ってもビットバッファを溢れさせないこと
    fs = 19200
    dem = Demodulator(fs, max_block=512)
    buf = array("H", bytes(1024))
    s = 12345
    total = 0
    for _ in range(20):
        for i in range(512):
            s = (s * 1103515245 + 12345) & 0x7FFFFFFF
            buf[i] = 2048 + ((s >> 16) & 0x3FF) - 512
        nb = dem.process(buf, 512)
        assert 0 <= nb <= len(dem.bits)
        total += nb
    assert total > 0


def test_hw_demod_realtime_and_no_alloc():
    # 2 つのプロファイルを並列に動かす想定(19200Hz)。合計で 1 コアの 40% 未満、確保ゼロ
    fs = 19200
    frames = _frames()
    samples, n = _modulate(frames, fs)
    dems = [Demodulator(fs, max_block=512, **PROFILE_NOISE),
            Demodulator(fs, max_block=512, **PROFILE_HUM)]
    blk = array("H", bytes(1024))
    for i in range(512):
        blk[i] = samples[i + 2000]
    for dem in dems:
        dem.process(blk, 512)
    gc.collect()
    a0 = gc.mem_alloc()
    t = time.ticks_us()
    for _ in range(20):
        for dem in dems:
            dem.process(blk, 512)
    dt = time.ticks_diff(time.ticks_us(), t)
    alloc = gc.mem_alloc() - a0
    us = dt / (20 * 512)
    budget = 1e6 / fs
    print("2 demods %.2f us/sample (%.0f%% of %.1f us), alloc %d bytes"
          % (us, 100 * us / budget, budget, alloc))
    assert us < 0.4 * budget
    assert alloc == 0
