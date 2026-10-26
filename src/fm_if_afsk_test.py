# PIO のアセンブラ(jmp, mov など)は rp2.asm_pio が関数の中に注入するので未定義の名前の検査を外す
# ruff: noqa: F821
# pyright: reportUndefinedVariable=false
"""455 kHz の FM IF(方形波)を GP8 で受け、AFSK のパケットまで復号する試験。

    python tools/dev.py run src/fm_if_afsk_test.py -t 60

配線(試験用の Pico 2):
    GP3 -> LPF(680 ohm / 33 nF x 2)-> 結合コンデンサ -> 信号発生器の外部変調入力
    信号発生器の 455 kHz FM 出力(0-3.3 V の方形波)-> 1 kohm -> GP8。GND を共通にする
流れ: PIO が立ち上がりエッジ M 個の区間の長さを数えて出す -> DMA で取り込む。その間に GP3 から
試験フレームを送る -> 区間の長さから瞬時周波数を出し、19.2 kHz ちょうどの間隔に並べ直して
(線形補間)ADC と同じ形の値にする -> 局と同じ 2 つの復調器とデフレーマで復号し、送ったものと比べる。
発生器なしで確かめる:
    selftest()      AFSK の音声から FM のエッジの時刻をソフトで作る(PIO と同じ量子化)
    selftest_pio()  GP8 を PWM で駆動して、間引き付きの PIO の区間の長さを確かめる
"""

import time
from array import array

import rp2
from machine import Pin, freq

import config
from modem.demod import PROFILE_HUM, PROFILE_NOISE, Demodulator
from tnc.afsk_mod import Modulator
from tnc.ax25 import DecodeError, Frame
from tnc.hdlc import Deframer, encode_levels, levels_size

PIN = 8
M = 24  # 時刻を出す間隔(エッジ数)。455 kHz で約 52.7 us = 19 kHz
FS = 19200  # 並べ直す音声のサンプリング周波数(局の受信と同じ)
GAIN = 0.15  # 周波数のずれ(Hz)-> ADC の値。偏移 +/-3 kHz で +/-450
MID = 2048
BLOCK = 512
_PIO0_RXF0 = 0x50200000 + 0x20
_DREQ_PIO0_RX0 = 4
TEXTS = (
    "JK1BBJ-7>APZPI2,WIDE1-1:>FM IF AFSK test 1",
    "JK1BBJ-7>APZPI2,WIDE1-1:!3546.06N/14001.91E[FM IF AFSK test 2 0123456789",
    "JK1BBJ-7>APZPI2:>FM IF AFSK test 3 ABCDEFGHIJKLMNOPQRSTUVWXYZ",
)


@rp2.asm_pio(autopush=True, push_thresh=32)
def edge_ts_div():
    # 立ち上がりエッジ M 個(OSR の値 + 1)ごとに、その区間で x が減った数を ~x として出す。
    # x はループ 1 回(2 クロック)ごとに 1 減り、出すたびに 0xFFFFFFFF に戻す。区間の端はいつも
    # エッジを見つけた瞬間なので、区間を足すと全体の時間にちょうどなる(誤差が積み重ならない)。
    # 1 区間のクロック数: ループ 2 * (減った数)、High から Low へ 2 * M、普通の立ち上がり
    # 2 * (M - 1)、出す立ち上がり 5 → 2 * (減った数) + 4 * M + 3(window_cycles)
    # 減った数は 1 区間で約 3850 なので、DMA は FIFO の下位 16 bit だけを取る(0xFFFF - 減った数)
    pull(block)
    wrap_target()
    mov(x, invert(null))
    mov(y, osr)
    label("hi")
    jmp(pin, "hi_dec")
    jmp("lo")
    label("hi_dec")
    jmp(x_dec, "hi")
    label("lo")
    jmp(pin, "rise")
    jmp(x_dec, "lo")
    label("rise")
    jmp(y_dec, "hi")
    in_(x, 32)
    wrap()


def window_cycles(d, m):
    """M エッジの区間で x が減った数 -> 区間のクロック数(edge_ts_div のコメント)"""
    return 2 * d + 4 * m + 3


class Decoder:
    """19.2 kHz の値を 512 個ずつ 2 つの復調器とデフレーマに流し、フレームを集める"""

    def __init__(self):
        self.chains = [(Demodulator(FS, max_block=BLOCK, **p), Deframer(8))
                       for p in (PROFILE_NOISE, PROFILE_HUM)]
        self.buf = array("H", bytes(2 * BLOCK))
        self.n = 0
        self.frames = [[], []]  # 復調器ごとの FCS が正しいフレーム
        self.bad = 0
        self.samples = 0

    def put(self, v):
        self.buf[self.n] = v
        self.n += 1
        if self.n == BLOCK:
            self.flush()

    def flush(self):
        if not self.n:
            return
        for k, (dem, dfr) in enumerate(self.chains):
            nb = dem.process(self.buf, self.n, 0)
            dfr.feed(dem.bits, nb)
            while True:
                m = dfr.get()
                if m is None:
                    break
                b = bytes(m)
                dfr.done()
                try:
                    Frame.decode(b)
                    self.frames[k].append(b)
                except DecodeError:
                    self.bad += 1
        self.samples += self.n
        self.n = 0


def convert(ts, n, clk, m, dec):
    """区間の値(PIO の出力の下位 16 bit、n 個)-> 19.2 kHz ちょうどの間隔の値を dec.put() へ。
    (中心周波数, 偏移の最大) を返す。最初の 1 個は PIO を始めた途中からの区間なので捨てる。
    時刻は半クロック単位の整数で扱う(単精度の float だと秒単位の時刻で数十クロックずれる)"""
    # 1 回目: 中心周波数(全区間の平均)と、平均からの最大のずれ
    tot = 0
    for j in range(1, n):
        tot += window_cycles(0xFFFF - ts[j], m)
    fc = (n - 1) * m * clk / tot
    step2 = 2 * clk // FS  # 出力 1 サンプルの半クロック数(150 MHz / 19.2 kHz で 15625)
    t2 = 0  # 区間の端の半クロック
    prev_c = prev_f = None
    out2 = 0
    dev = 0.0
    for j in range(1, n):
        cyc = window_cycles(0xFFFF - ts[j], m)
        c2 = t2 + cyc  # 区間の中央(半クロック)
        f = m * clk / cyc
        t2 += 2 * cyc
        d = f - fc
        if d > dev or -d > dev:
            dev = abs(d)
        if prev_c is None:
            prev_c, prev_f, out2 = c2, f, c2
            continue
        while out2 <= c2:
            v = prev_f + (f - prev_f) * (out2 - prev_c) / (c2 - prev_c)
            s = int(MID + (v - fc) * GAIN)
            dec.put(0 if s < 0 else 4095 if s > 4095 else s)
            out2 += step2
        prev_c, prev_f = c2, f
    dec.flush()
    return fc, dev


def report(sent, dec, fc, dev):
    print("audio %d samples (%.2f s), IF center %.3f kHz, max deviation %.2f kHz" % (
        dec.samples, dec.samples / FS, fc / 1e3, dev / 1e3))
    names = ("NOISE", "HUM")
    for k in range(2):
        got = sum(1 for b in sent if b in dec.frames[k])
        print("  %-5s decoded %d / %d sent (other good %d)" % (
            names[k], got, len(sent), len(dec.frames[k]) - got))
    union = sum(1 for b in sent if b in dec.frames[0] or b in dec.frames[1])
    print("RESULT decoded %d / %d (either demodulator), bad FCS %d" % (union, len(sent), dec.bad))


def _frames():
    return [bytes(Frame.parse(t).encode()) for t in TEXTS]


def run_capture(n, setup_pin, during):
    """GP8 の区間の値を n 個取り込む。取り込み中に during() を呼ぶ。信号が無ければ None"""
    pin = Pin(PIN, Pin.IN, Pin.PULL_DOWN) if setup_pin else Pin(PIN)
    buf = array("H", bytes(2 * n))
    sm = rp2.StateMachine(0, edge_ts_div, jmp_pin=pin, in_base=pin)
    dma = rp2.DMA()
    try:
        dma.config(read=_PIO0_RXF0, write=buf, count=n,
                   ctrl=dma.pack_ctrl(size=1, inc_read=False, inc_write=True,
                                      treq_sel=_DREQ_PIO0_RX0))  # 16 bit: FIFO の下位半分
        dma.active(1)
        sm.put(M - 1)
        sm.active(1)
        time.sleep_ms(50)
        if dma.count == n:  # type: ignore[attr-defined]  (実機にある)
            return None  # 50 ms で 1 個も来ない
        if during is not None:
            during()
        t0 = time.ticks_ms()
        while dma.active():
            if time.ticks_diff(time.ticks_ms(), t0) > 5000:
                return None
            time.sleep_ms(5)
        return buf
    finally:
        sm.active(0)
        dma.active(0)
        dma.close()
        rp2.PIO(0).remove_program(edge_ts_div)


def main(capture_s=3.8):
    """発生器をつないだ試験: GP3 から 3 フレーム送り、GP8 の FM から復号する"""
    from hw.afsk_tx import AfskTx

    clk = freq()
    sent = _frames()
    n = int(capture_s * 455_000 / M)
    print("sysclk %d Hz, capture %d windows (%.1f s, %d KB), M=%d" % (
        clk, n, capture_s, 2 * n // 1024, M))
    tx = AfskTx(config.PIN_AFSK_OUT, config.PIN_PTT, config.FS, config.PWM_TOP,
                config.TX_AMPLITUDE, config.TX_DELAY_MS, config.TX_TAIL_MS,
                config.PTT_ACTIVE_HIGH, max_ms=1000)

    def send_all():
        for f in sent:
            tx.start(n=tx.build([f]))
            while not tx.finish():
                time.sleep_ms(5)
            time.sleep_ms(150)

    try:
        ts = run_capture(n, True, send_all)
    finally:
        tx.close()
    if ts is None:
        print("no signal on GP%d (0-3.3 V square wave expected)" % PIN)
        return
    dec = Decoder()
    fc, dev = convert(ts, n, clk, M, dec)
    report(sent, dec, fc, dev)


def synth_ts(audio, n_audio, clk, m, fc, hz_per_count):
    """AFSK の音声(ADC と同じ形)で fc を FM 変調した方形波を、edge_ts_div と同じ形の値にする。
    区間の端は 2 クロック単位で切り捨てる(PIO のループと同じ量子化)。位相は小数部だけ、
    時刻は半クロックの整数で持つ。最初の 1 個は捨てられる区間なので 0 にしておく"""
    half = 2 * clk // FS  # 音声 1 サンプルの半クロック数
    out = array("H", bytes(2 * (int(n_audio / FS * (fc + 6000) / m) + 2)))
    ph = 0.0  # IF の位相の小数部(サンプルの頭)
    e = 0  # ここまでのエッジ数
    nxt = m  # 次に区間を閉じるエッジの番号
    j = 1
    g_prev = 0  # 前の区間の端(2 クロック単位)
    for i in range(n_audio):
        inc = (fc + (audio[i] - MID) * hz_per_count) / FS  # このサンプルで進む IF の周期数
        c = int(ph + inc)
        while nxt <= e + c and j < len(out):
            hc = i * half + int(((nxt - e) - ph) / inc * half)  # エッジの時刻(半クロック)
            g = hc // 4
            # window_cycles = 2 * d + 4M + 3 が 2 * (g - g_prev) になるように(1 クロックの差は無視)
            out[j] = 0xFFFF - (g - g_prev - 2 * m - 1)
            g_prev = g
            j += 1
            nxt += m
        e += c
        ph = ph + inc - c
    return out, j


def selftest():
    """発生器なし: 送るのと同じ AFSK の音声から FM の時刻を作り、変換と復号を確かめる"""
    clk = freq()
    sent = _frames()[:1]
    mod = Modulator(FS, mid=MID, amp=500)
    lv = bytearray(levels_size(len(sent[0]), 40, 4))
    k, _ = encode_levels(sent[0], memoryview(lv), 40, 4, 0)
    audio = array("H", bytes(2 * (mod.samples_size(k) + FS // 5)))
    pos = mod.silence(audio, 0, FS // 10)
    pos = mod.modulate(lv, k, audio, pos)
    pos = mod.silence(audio, pos, FS // 10)
    t0 = time.ticks_ms()
    ts, n = synth_ts(audio, pos, clk, M, 455_000.0, 6.0)  # 振幅 500 で +/-3 kHz
    print("selftest: %d audio samples -> %d windows in %d ms" % (
        pos, n, time.ticks_diff(time.ticks_ms(), t0)))
    del audio
    dec = Decoder()
    t0 = time.ticks_ms()
    fc, dev = convert(ts, n, clk, M, dec)
    print("convert + decode %d ms" % time.ticks_diff(time.ticks_ms(), t0))
    report(sent, dec, fc, dev)


def selftest_pio():
    """発生器なし: GP8 を PWM(454.545 kHz)で駆動して、間引き付きの PIO の時刻を確かめる"""
    from machine import PWM

    clk = freq()
    pwm = PWM(Pin(PIN), freq=455_000, duty_u16=32768)
    try:
        ts = run_capture(2000, False, None)
    finally:
        pwm.deinit()
        Pin(PIN, Pin.IN, Pin.PULL_DOWN)
    if ts is None:
        print("selftest_pio: no edges")
        return
    cyc = [window_cycles(0xFFFF - ts[j], M) for j in range(1, len(ts))]
    f = M * clk * len(cyc) / sum(cyc)
    print("selftest_pio: %d windows, %.3f kHz (PWM 454.545 kHz = 330 clocks x %d = %d), "
          "window cycles min/max %d / %d" % (len(cyc), f / 1e3, M, 330 * M, min(cyc), max(cyc)))


if __name__ == "__main__":
    main()
