#!/usr/bin/env python3
"""AFSK 復調器のベンチマーク(PC 上で実行。デバイス不要)

    python tools/afsk_bench.py                    既定の条件セットを 19200 / 13200 Hz で評価
    python tools/afsk_bench.py --fs 19200 -n 300  サンプリング周波数とフレーム数を指定
    python tools/afsk_bench.py --only hum         名前に 'hum' を含む条件だけ
    python tools/afsk_bench.py --wav out/         各条件の音声を WAV(int16)でも書き出す

試験信号は変調器(tnc.afsk_mod)とは独立に float で合成する(連続位相 FSK、
ボーレート・トーン周波数のずれを任意に与えられる)。劣化は次の順に加える:
    FSK → 無線機のエンファシス(1 次フィルタ) → 50Hz ハム(と高調波) → 白色雑音
        → ADC(12 bit、中点 2048、DC ずれ、クリップ、量子化)
SNR は 3 kHz 帯域あたりで定義する(サンプリング周波数が違っても雑音の密度は同じ)。
各フレームの前後に雑音だけの区間を置き、フレームごとに初期位相・プリアンブル長を変える。
"""

from __future__ import annotations

import argparse
import math
import multiprocessing as mp
import random
import struct
import sys
import time
import wave
from array import array
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "src")]

import host_test  # noqa: E402

host_test.install_shims()

from modem.demod import Demodulator, biquad  # noqa: E402
from tnc.ax25 import Address, DecodeError, Frame  # noqa: E402
from tnc.hdlc import Deframer  # noqa: E402

AMP = 400  # 信号の振幅(ADC カウント、尖頭値)。12 bit の約 -14 dBFS
MID = 2048

# 条件: 名前 -> 劣化パラメータ
CONDITIONS: dict[str, dict] = {
    "clean": {},
    "deemph(space-5dB)": {"emph": "de"},
    "preemph(space+5dB)": {"emph": "pre"},
    "snr20": {"snr": 20},
    "snr15": {"snr": 15},
    "snr12": {"snr": 12},
    "snr10": {"snr": 10},
    "snr8": {"snr": 8},
    "snr6": {"snr": 6},
    "hum50 0dB": {"hum": 0},
    "hum50 +10dB": {"hum": 10},
    "hum50 +14dB(near clip)": {"hum": 14},
    "hum50+harm 0dB": {"hum": 0, "harm": True},
    "hum50+harm +6dB": {"hum": 6, "harm": True},
    "hum50+harm +10dB(clip)": {"hum": 10, "harm": True},
    "hum50 +10dB snr12": {"hum": 10, "snr": 12},
    # ADC 前段にアナログ HPF(2 次バターワース 300Hz)を入れた場合
    "hum50 +20dB(clip)": {"hum": 20},
    "hum50 +20dB +hwHPF300": {"hum": 20, "hw_hpf": 300},
    "hum50+harm +10dB +hwHPF300": {"hum": 10, "harm": True, "hw_hpf": 300},
    "hum50+harm +20dB +hwHPF300": {"hum": 20, "harm": True, "hw_hpf": 300},
    "baud -1%": {"baud_err": -0.01},
    "baud +1%": {"baud_err": 0.01},
    "tone -40Hz": {"tone_off": -40},
    "tone +40Hz": {"tone_off": 40},
    "dc +800": {"dc": 800},
    "overdrive x3 (clip)": {"gain": 3.0, "dc": 0},
    "low level -20dB": {"gain": 0.1},
    "de+snr10": {"emph": "de", "snr": 10},
    "de+hum10": {"emph": "de", "hum": 10},
    "de+snr10+hum10": {"emph": "de", "snr": 10, "hum": 10},
    "pre+snr10+baud1%": {"emph": "pre", "snr": 10, "baud_err": 0.01},
}


# ------------------------------------------------------------------ 試験フレーム
def random_frame(rnd: random.Random) -> bytes:
    def call():
        pre = rnd.choice(["JA", "JE", "JF", "JG", "JH", "JR", "7K", "JJ"])
        return "%s%d%s" % (pre, rnd.randint(0, 9), "".join(rnd.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
                                                       for _ in range(rnd.randint(2, 3))))

    src = Address(call(), rnd.randint(0, 15))
    digis = [Address("WIDE1", 1), Address("WIDE2", rnd.randint(1, 2))][: rnd.randint(0, 2)]
    n = rnd.randint(20, 90)
    info = bytes(rnd.randint(0x20, 0x7E) for _ in range(n))
    return bytes(Frame(Address("APRS"), src, digis, info).encode())


def frame_bits(frame: bytes, pre_flags: int, post_flags: int) -> list[int]:
    """frame -> NRZI 前のデータビット列(フラグ + ビットスタッフィング済み、LSB first)"""
    bits: list[int] = []
    for _ in range(pre_flags):
        bits += [(0x7E >> k) & 1 for k in range(8)]
    ones = 0
    for b in frame:
        for k in range(8):
            bit = (b >> k) & 1
            bits.append(bit)
            ones = ones + 1 if bit else 0
            if ones == 5:
                bits.append(0)
                ones = 0
    for _ in range(post_flags):
        bits += [(0x7E >> k) & 1 for k in range(8)]
    return bits


# ------------------------------------------------------------------ 信号合成と劣化
def synth(frames: list[bytes], fs: int, c: dict, rnd: random.Random) -> array:
    baud = 1200 * (1 + c.get("baud_err", 0.0))
    fm = 1200 + c.get("tone_off", 0.0)
    fsp = 2200 + c.get("tone_off", 0.0)
    gain = c.get("gain", 1.0)
    out: list[float] = []

    def gap(seconds):
        out.extend([0.0] * int(seconds * fs))

    gap(0.05)
    spb = fs / baud
    for f in frames:
        bits = frame_bits(f, rnd.randint(8, 30), rnd.randint(2, 4))
        lv = 0
        ph = rnd.uniform(0, 2 * math.pi)
        t_end = 0.0  # このバーストの先頭からのビット終端時刻(サンプル単位)
        k = 0  # このバーストで出力したサンプル数
        for bit in bits:
            if bit == 0:  # NRZI: 0 = 遷移
                lv ^= 1
            dph = 2 * math.pi * (fm if lv else fsp) / fs
            t_end += spb
            while k < int(round(t_end)):
                out.append(math.sin(ph))
                ph += dph
                k += 1
        gap(rnd.uniform(0.05, 0.15))
    sig = [v * AMP * gain for v in out]

    # 無線機のエンファシス(1 次): de = 300Hz の 1 次 LPF(2200Hz が約 -5dB)、
    # pre = その逆特性に近い 1 次の高域強調。どちらも 1200Hz の振幅が変わらないよう正規化
    emph = c.get("emph")
    if emph == "de":
        a = math.exp(-2 * math.pi * 300 / fs)
        y = 0.0
        g = _gain_at(1200, fs, lambda z: (1 - a) / (1 - a / z))
        for i, v in enumerate(sig):
            y = (1 - a) * v + a * y
            sig[i] = y / g
    elif emph == "pre":
        a = math.exp(-2 * math.pi * 300 / fs)
        prev = 0.0
        g = _gain_at(1200, fs, lambda z: (1 - a / z) / (1 - a))
        for i, v in enumerate(sig):
            y = (v - a * prev) / (1 - a)
            prev = v
            sig[i] = y / g

    hum_db = c.get("hum")
    if hum_db is not None:
        ha = AMP * 10 ** (hum_db / 20)
        hp = rnd.uniform(0, 2 * math.pi)
        harm = [(1, 1.0)]
        if c.get("harm"):
            # 整流器由来のバズ: 50Hz の高調波(1/k で減衰)を 1kHz まで
            # 全体の実効値が基本波だけのときと同じになるよう正規化する
            harm = [(k, 1.0 / k) for k in range(1, 21)]
            norm = math.sqrt(sum(g * g for _, g in harm))
            harm = [(k, g / norm) for k, g in harm]
        w = 2 * math.pi * 50 / fs
        for i in range(len(sig)):
            sig[i] += ha * sum(g * math.sin(k * (w * i + hp)) for k, g in harm)

    snr = c.get("snr")
    if snr is not None:
        p_sig = (AMP * gain) ** 2 / 2
        sigma = math.sqrt(p_sig / 10 ** (snr / 10) * (fs / 2) / 3000)
        for i in range(len(sig)):
            sig[i] += rnd.gauss(0, sigma)

    hw = c.get("hw_hpf")
    if hw:
        # ADC 前段のアナログ HPF(2 次バターワース)。float の biquad で模擬する
        b0, b1, b2, a1, a2 = biquad("hp", hw, fs)
        x1 = x2 = y1 = y2 = 0.0
        for i, v in enumerate(sig):
            y = b0 * v + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
            x2, x1, y2, y1 = x1, v, y1, y
            sig[i] = y

    dc = c.get("dc", 0)
    res = array("H", bytes(2 * len(sig)))
    for i, v in enumerate(sig):
        q = int(round(MID + dc + v))
        res[i] = 0 if q < 0 else 4095 if q > 4095 else q
    return res


def _gain_at(f, fs, h):
    z = complex(math.cos(2 * math.pi * f / fs), math.sin(2 * math.pi * f / fs))
    return abs(h(z))


# ------------------------------------------------------------------ 評価
def run_condition(args) -> tuple[str, int, int, int, int, float]:
    name, fs, nframes, seed, wav_dir, params = args
    c = CONDITIONS[name]
    rnd = random.Random(seed)
    frames = [random_frame(rnd) for _ in range(nframes)]
    samples = synth(frames, fs, c, rnd)
    if wav_dir:
        write_wav(Path(wav_dir) / ("%d_%s.wav" % (fs, _safe(name))), samples, fs)
    # params["dual"] があれば、その設定の復調器も並列に動かし、どちらかで復号できれば成功
    dual = params.get("dual")
    base = {k: v for k, v in params.items() if k != "dual"}
    chains = [(Demodulator(fs, **base), Deframer(16))]
    if dual:
        chains.append((Demodulator(fs, **dict(base, **dual)), Deframer(16)))
    sent = set(frames)
    ok = bad_fcs = 0
    got: set[bytes] = set()
    t0 = time.perf_counter()
    blk = chains[0][0].max_block
    for off in range(0, len(samples), blk):
        part = samples[off : off + blk]
        for dem, dfr in chains:
            nb = dem.process(part)
            dfr.feed(dem.bits, nb)
            while (m := dfr.get()) is not None:
                b = bytes(m)
                dfr.done()
                try:
                    Frame.decode(b)
                except DecodeError:
                    bad_fcs += 1
                    continue
                if b in sent and b not in got:
                    got.add(b)
                    ok += 1
    dt = time.perf_counter() - t0
    return name, fs, nframes, ok, bad_fcs, dt


def _safe(name):
    # '+' と '-' を区別する(baud +1% / -1% が同じファイル名にならないように)
    name = name.replace("+", "p").replace("-", "m")
    return "".join(ch if ch.isalnum() else "_" for ch in name)


def write_wav(path: Path, samples: array, fs: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(fs)
        w.writeframes(b"".join(struct.pack("<h", (v - MID) * 16) for v in samples))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    ap = argparse.ArgumentParser()
    ap.add_argument("--fs", type=int, nargs="+", default=[19200, 13200])
    ap.add_argument("-n", "--frames", type=int, default=200)
    ap.add_argument("--only", default="")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--wav", default="")
    ap.add_argument("-p", "--param", action="append", default=[],
                    help="復調器のパラメータ(例: -p pll_shift=3 -p agc_ms=40)")
    ap.add_argument("-d", "--dual", action="append", default=[],
                    help="2 台目の復調器を並列に動かすときの差分パラメータ(例: -d hpf2_hz=1000)")
    a = ap.parse_args()
    params: dict = {k: int(v) for k, v in (x.split("=") for x in a.param)}
    if a.dual:
        params["dual"] = {k: int(v) for k, v in (x.split("=") for x in a.dual)}

    names = [n for n in CONDITIONS if a.only in n]
    jobs = [(n, fs, a.frames, a.seed + i, a.wav, params)
            for fs in a.fs for i, n in enumerate(names)]
    with mp.Pool() as pool:
        results = pool.map(run_condition, jobs)
    table: dict[str, dict[int, tuple[int, int, int]]] = {}
    for name, fs, n, ok, bad, _dt in results:
        table.setdefault(name, {})[fs] = (ok, n, bad)
    head = "%-26s" % "condition" + "".join("%18s" % ("%d Hz" % fs) for fs in a.fs)
    print(head)
    print("-" * len(head))
    for name in names:
        row = "%-26s" % name
        for fs in a.fs:
            ok, n, bad = table[name][fs]
            row += "%18s" % ("%d/%d (%.0f%%)" % (ok, n, 100 * ok / n))
        print(row)
    print("\n(各条件 %d フレーム。数値は正しく復号できたフレーム数。パラメータ: %s)"
          % (a.frames, params or "既定"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
