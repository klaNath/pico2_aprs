"""FX.25 送信: AX.25 フレームを Reed-Solomon で包み、NRZI のレベル列にする。

FX.25 は AX.25 の前に 64 bit の相関タグを置き、内側の AX.25(ビットスタッフ済み、前後のフラグ込み)を
RS の符号語に入れて、チェックバイトを後ろに付ける。内側は普通の HDLC なので、FX.25 に対応しない
受信機でも(誤りがなければ)従来どおり復号できる。

対応するモードは、チェックバイト 16 バイト(8 バイトまで訂正)の 3 種だけ:

    タグ  RS(n, k)       データ欄 k
    0x04  RS( 48,  32)   32 バイト
    0x03  RS( 80,  64)   64 バイト
    0x02  RS(144, 128)  128 バイト

RS(255,239) は扱わない。内側(フラグ込み・スタッフ後)が 128 バイトを超えるフレームは対象外で、
prepare() が 0 を返すので、呼び出し側は従来の AX.25 で送る。

仕様(Dire Wolf の fx25_init.c / fx25_send.c / fx25_rec.c から確認した点。仕様書の PDF は未確認):
- RS は GF(2^8)、原始多項式 0x11D、生成多項式の根は alpha^1 .. alpha^16(fcr = 1、prim = 1)
- 短縮符号は 255 バイトの符号語の中で [データ k バイト][ゼロ 239-k バイト][チェック 16 バイト]。
  ゼロはデータの「後ろ」(前ではない)。チェックは符号語の最後なので、ゼロも符号化に含める
- データ欄は 0x7E + スタッフ済みフレーム(FCS 込み)+ 0x7E で、残りは 0x7E のビット列で埋める
  (バイト境界で区切り直さず、閉じフラグの次のビットから続ける)。ビットは各バイトの LSB から
- タグは 64 bit 値の下位バイトから、各バイトは LSB から送る
- タグ・データ欄・チェックにはビットスタッフをしない。NRZI は前置フラグから続けて行う

メモリは作るときに 1 回だけ確保し、prepare() / write() では確保しない(MicroPython の small int
だけを使う)。RS の符号化は純 Python で、239 バイト分で約 50 ms(送信 1 回につき 1 回)。
自作(Dire Wolf のコードは写していない)。
"""

from micropython import const

_FLAG = const(0x7E)
_NROOTS = const(16)
_KMAX = const(128)
_RSK = const(239)  # RS(255,239) の符号語のデータ部の長さ(短縮の元)

# (タグ値, k) を k の小さい順に。タグの値は FX.25 仕様の相関タグ
_MODES = ((0x8F056EB4369660EE, 32), (0xC7DC0508F3D9B09E, 64), (0x26FF60A600CC8FDE, 128))


def _tag_bytes(v):
    b = bytearray(8)
    for i in range(8):
        b[i] = (v >> (8 * i)) & 0xFF  # 下位バイトから
    return bytes(b)


_TAGS = tuple(_tag_bytes(t) for t, _k in _MODES)


class Fx25Tx:
    """prepare(frame) でデータ欄を作り、write(...) でレベル列にする。"""

    def __init__(self):
        exp = bytearray(512)
        log = bytearray(256)
        x = 1
        for i in range(255):
            exp[i] = x
            log[x] = i
            x <<= 1
            if x & 0x100:
                x ^= 0x11D
        for i in range(255, 512):
            exp[i] = exp[i - 255]
        # 生成多項式 g(x) = prod_{i=0..15} (x - alpha^(1+i))(最高次の係数 1 は省く)
        g = [1]
        for i in range(_NROOTS):
            r = exp[1 + i]
            ng = [0] * (len(g) + 1)
            for j, c in enumerate(g):
                ng[j] ^= c
                if c:
                    ng[j + 1] ^= exp[log[c] + log[r]]
            g = ng
        self._exp = exp
        self._log = log
        self._gl = bytearray(log[c] for c in g[1:])  # 0 の係数は g に現れない(RS の性質)
        self.data = bytearray(_KMAX + _NROOTS)  # データ欄 + チェック
        self._par = bytearray(_NROOTS)
        self._lv = 0  # write() 中の NRZI の状態
        self.nbit = 0  # データ欄に入ったビット数(閉じフラグまで)
        self.mode = -1  # _MODES の添字。-1 = 未準備 / 対象外

    # --- 内側の AX.25 を作る ---

    def prepare(self, frame):
        """frame(FCS 込み)をデータ欄に詰める。FX.25 で送れれば、タグ+ブロックのバイト数
        (8 + k + 16)を返す。内側が 128 バイトを超えれば 0(従来の AX.25 で送る)。
        write() の前に呼ぶ。"""
        d = self.data
        for i in range(len(d)):
            d[i] = 0
        self.mode = -1
        limit = _KMAX * 8
        d[0] = _FLAG
        nb = 8
        ones = 0
        for b in frame:
            for _ in range(8):
                if nb + 8 > limit:  # 閉じフラグが入らない(書き込み位置を limit 未満に保つ)
                    return 0
                if b & 1:
                    d[nb >> 3] |= 1 << (nb & 7)
                    nb += 1
                    ones += 1
                    if ones == 5:
                        nb += 1  # スタッフの 0
                        ones = 0
                else:
                    nb += 1
                    ones = 0
                b >>= 1
        if nb + 8 > limit:
            return 0
        for j in range(8):  # 閉じフラグ(バイト境界とは限らない)
            if (_FLAG >> j) & 1:
                d[nb >> 3] |= 1 << (nb & 7)
            nb += 1
        self.nbit = nb
        dlen = (nb + 7) >> 3
        mode = 0
        while _MODES[mode][1] < dlen:
            mode += 1
        k = _MODES[mode][1]
        j = 0
        for pos in range(nb, k * 8):  # 残りは 0x7E のビット列(閉じフラグの次のビットから続ける)
            if (_FLAG >> (j & 7)) & 1:
                d[pos >> 3] |= 1 << (pos & 7)
            j += 1
        self.mode = mode
        return 8 + k + _NROOTS

    # --- RS ---

    def _encode(self):
        """データ欄 k バイト + ゼロ(239 - k)バイトのチェックを data[k:k+16] に書く"""
        k = _MODES[self.mode][1]
        d = self.data
        exp = self._exp
        log = self._log
        gl = self._gl
        par = self._par
        for j in range(_NROOTS):
            par[j] = 0
        last = _NROOTS - 1
        for i in range(_RSK):
            fb = (d[i] if i < k else 0) ^ par[0]
            if fb:
                lf = log[fb]
                for j in range(last):
                    par[j] = par[j + 1] ^ exp[gl[j] + lf]
                par[last] = exp[gl[last] + lf]
            else:
                for j in range(last):
                    par[j] = par[j + 1]
                par[last] = 0
        for j in range(_NROOTS):
            d[k + j] = par[j]

    # --- レベル列 ---

    def levels_size(self, pre_flags, post_flags):
        """write() が書くレベル数(prepare() が 0 以外を返した後に有効)"""
        return (pre_flags + post_flags) * 8 + (8 + _MODES[self.mode][1] + _NROOTS) * 8

    def write(self, out, pre_flags, post_flags, level=0):
        """前置フラグ + タグ + データ欄 + チェック + 後置フラグを NRZI のレベルで out へ書く
        (1 バイト 1 ビット)。-> (書いたレベル数, 最後のレベル)。out は levels_size() 以上"""
        if self.mode < 0:
            raise ValueError("prepare() first")
        self._encode()
        k = _MODES[self.mode][1]
        self._lv = level
        n = 0
        for _ in range(pre_flags):
            n = self._put(out, n, _FLAG)
        for b in _TAGS[self.mode]:
            n = self._put(out, n, b)
        d = self.data
        for i in range(k + _NROOTS):
            n = self._put(out, n, d[i])
        for _ in range(post_flags):
            n = self._put(out, n, _FLAG)
        return n, self._lv

    def _put(self, out, n, b):
        """1 バイトを LSB から NRZI(0 = 反転、1 = そのまま)で書く。ビットスタッフはしない。
        NRZI の状態は self._lv。戻り値は小さい整数だけ(タプルを作らない)"""
        lv = self._lv
        for _ in range(8):
            if not b & 1:
                lv ^= 1
            out[n] = lv
            n += 1
            b >>= 1
        self._lv = lv
        return n
