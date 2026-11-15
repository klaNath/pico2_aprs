"""FX.25 送信(modem.fx25)のテスト。PC と実機の両方で動く。コールサインはすべて架空の例。

・タグの値と、内側のフレームの大きさによるモード選択
・RS の符号語(データ + ゼロ + チェック)のシンドロームがすべて 0(テスト側で独立に書いた GF 演算)
・レベル列 → NRZI 復号 → タグ・データ欄・チェックの配置
・内側が従来の AX.25 として Deframer で復号できる(FX.25 に対応しない受信機との互換)
・モデム(変調 → 復調)を通しても同じ
Dire Wolf など他の実装との相互運用は、ここでは確かめていない(実機で要確認)。
"""

from array import array

from modem.demod import Demodulator
from modem.fx25 import Fx25Tx
from tnc.afsk_mod import Modulator
from tnc.ax25 import Frame
from tnc.hdlc import Deframer, encode_levels, levels_size

# FX.25 仕様の相関タグ(64 bit 値)。k -> 値
TAG = {32: 0x8F056EB4369660EE, 64: 0xC7DC0508F3D9B09E, 128: 0x26FF60A600CC8FDE}


def _gmul(a, b):
    """GF(2^8)、多項式 0x11D の掛け算(シフトと XOR。fx25.py とは別の実装)"""
    r = 0
    while b:
        if b & 1:
            r ^= a
        a <<= 1
        if a & 0x100:
            a ^= 0x11D
        b >>= 1
    return r


def _syndromes(cw):
    out = []
    root = 2  # alpha
    x = 1
    for _ in range(16):
        x = _gmul(x, root)  # alpha^1 .. alpha^16
        a = 0
        for c in cw:
            a = _gmul(a, x) ^ c
        out.append(a)
    return out


def _bits(lv, n):
    bits = bytearray(n)
    prev = 0
    for i in range(n):
        bits[i] = 1 if lv[i] == prev else 0
        prev = lv[i]
    return bits


def _pack(bits, start, nbytes):
    """ビット列(LSB 先頭)をバイトに戻す"""
    out = bytearray(nbytes)
    for i in range(nbytes * 8):
        if bits[start + i]:
            out[i >> 3] |= 1 << (i & 7)
    return out


def _zeros(length):
    return b"\x00" * length  # ビットスタッフが起きない(1 が無い)


def _encode(fx, frame, pre=3, post=2):
    size = fx.prepare(frame)
    assert size > 0
    lv = bytearray(fx.levels_size(pre, post))
    n, _ = fx.write(lv, pre, post, 0)
    assert n == len(lv)
    return lv, n, size


def test_mode_selection_by_inner_length():
    fx = Fx25Tx()
    # ゼロだけのフレームは 1 も立たないのでスタッフがなく、データ欄は L + 2 バイト(両端のフラグ)
    cases = ((18, 8 + 32 + 16), (30, 8 + 32 + 16), (31, 8 + 64 + 16), (62, 8 + 64 + 16),
             (63, 8 + 128 + 16), (126, 8 + 128 + 16), (127, 0), (200, 0))
    for length, size in cases:
        assert fx.prepare(_zeros(length)) == size, length


def test_stuffing_counts_toward_the_limit():
    fx = Fx25Tx()
    # 0xFF は 5 個ごとにスタッフが入る(1 バイト 8 -> 9.6 bit)。100 バイトで 960 bit = 120 バイト
    assert fx.prepare(b"\xff" * 100) == 8 + 128 + 16
    assert fx.prepare(b"\xff" * 107) == 0  # 1027 bit = 129 バイト以上


def test_tag_bytes_on_the_wire():
    fx = Fx25Tx()
    for k, tag in TAG.items():
        length = {32: 20, 64: 50, 128: 100}[k]
        lv, n, _ = _encode(fx, _zeros(length), pre=3, post=2)
        bits = _bits(lv, n)
        got = _pack(bits, 3 * 8, 8)  # 前置フラグ 3 個の後がタグ
        assert int.from_bytes(got, "little") == tag, k  # 下位バイトから送る
        assert _pack(bits, 0, 3) == b"\x7e\x7e\x7e"


def test_rs_codeword_syndromes_are_zero():
    fx = Fx25Tx()
    frames = [_zeros(20), b"\xff" * 40, bytes(range(1, 50)),
              bytes((i * 37 + 5) & 0xFF for i in range(110))]
    for f in frames:
        lv, n, _ = _encode(fx, f)
        bits = _bits(lv, n)
        nblock = (n - 5 * 8) // 8 - 8  # タグ以外のバイト数(データ + チェック 16)
        assert nblock in (48, 80, 144)
        kk = nblock - 16
        block = _pack(bits, 3 * 8 + 64, nblock)
        # 符号語 255 バイト = データ k + ゼロ(239 - k)+ チェック 16
        cw = bytes(block[:kk]) + b"\x00" * (239 - kk) + bytes(block[kk:])
        assert len(cw) == 255
        assert not any(_syndromes(cw))
        bad = bytearray(cw)
        bad[3] ^= 0x10
        assert any(_syndromes(bad))


def test_data_field_layout():
    fx = Fx25Tx()
    f = _zeros(20)  # データ欄 22 バイト、k = 32
    lv, n, _ = _encode(fx, f)
    bits = _bits(lv, n)
    data = _pack(bits, 3 * 8 + 64, 32)
    assert data[0] == 0x7E and data[1:21] == f and data[21] == 0x7E
    assert data[22:] == b"\x7e" * 10  # 余りは 0x7E で埋める(閉じフラグがバイト境界なら揃っている)


def test_fill_continues_flag_bits_from_unaligned_end():
    fx = Fx25Tx()
    f = b"\xff" * 20  # スタッフが入り、閉じフラグがバイト境界に来ない
    lv, n, _ = _encode(fx, f)
    bits = _bits(lv, n)
    data_bits = bits[3 * 8 + 64 : 3 * 8 + 64 + 32 * 8]
    # 先頭フラグ 8 bit の後ろのスタッフ済みの長さ: 160 bit + 32 個のスタッフ
    nb = 8 + 160 + 32
    assert bytes(data_bits[nb : nb + 8]) == bytes(((0x7E >> j) & 1) for j in range(8))
    for j in range(nb, 32 * 8):  # 閉じフラグの後も 0x7E のビット列が途切れず続く
        assert data_bits[j] == (0x7E >> ((j - nb) & 7)) & 1, j


def test_inner_frame_decodes_as_plain_ax25():
    # FX.25 に対応しない受信機の見え方: 全体を従来のデフレーマに通すと内側のフレームが出る
    fx = Fx25Tx()
    texts = ("JA1XYZ-9>APRS,WIDE1-1:!3540.12N/13945.67E>test",
             "JA1ABC>SU4PX7,WIDE2-1:`CJ\x1fn4v>/\"4?}Pico2",
             "7K1DEF-15>APRS:>" + "\x7f" * 20)
    frames = [bytes(Frame.parse(t).encode()) for t in texts] + [b"\xff" * 80, _zeros(30)]
    for f in frames:
        lv, n, _ = _encode(fx, f)
        bits = _bits(lv, n)
        d = Deframer(2)
        assert d.feed(bits, n) >= 1, f
        m = d.get()
        assert m is not None and bytes(m) == f


def test_loopback_through_the_modem():
    fx = Fx25Tx()
    fs = 19200
    mod = Modulator(fs, mid=2048, amp=500)
    frames = [bytes(Frame.parse("JA1XYZ-9>APRS,WIDE1-1:!3540.12N/13945.67E>test").encode()),
              b"\xff" * 60]
    lv = bytearray(4000)
    n = 0
    last = 0
    for i, f in enumerate(frames):
        assert fx.prepare(f)
        k, last = fx.write(memoryview(lv)[n:], 16 if i == 0 else 2, 2, last)
        n += k
    out = array("H", bytes(2 * (mod.samples_size(n) + fs // 5)))
    pos = mod.silence(out, 0, fs // 10)
    pos = mod.modulate(lv, n, out, pos)
    pos = mod.silence(out, pos, fs // 10)
    dem = Demodulator(fs, max_block=512)
    dfr = Deframer(8)
    got = []
    mv = memoryview(out)
    for off in range(0, pos, 512):
        k = min(512, pos - off)
        nb = dem.process(mv[off : off + k], k)
        dfr.feed(dem.bits, nb)
        while True:
            m = dfr.get()
            if m is None:
                break
            got.append(bytes(m))
            dfr.done()
    assert frames[0] in got and frames[1] in got


def test_oversize_frame_falls_back_to_plain():
    fx = Fx25Tx()
    f = _zeros(140)
    assert fx.prepare(f) == 0
    try:
        fx.write(bytearray(4000), 3, 2)
        raise AssertionError("ValueError expected")
    except ValueError:
        pass
    # 従来の経路はそのまま使える
    lv = bytearray(levels_size(len(f), 3, 2))
    n, _ = encode_levels(f, lv, 3, 2)
    assert n <= len(lv)
