"""APRS の位置フォーマット(非圧縮 / 圧縮)と位置の後ろに付く拡張のデコード。

参照: APRS Protocol Reference 1.0.1 第 8 章(位置), 第 9 章(圧縮位置), 第 7 章(データ拡張)。
単位は内部で統一する: 速度 km/h, 高度 m, 進行方向 度(1-360)。
"""

from micropython import const

KNOT_KMH = 1.852
FOOT_M = 0.3048

UNCOMPRESSED_LEN = const(19)  # DDMM.mmN T DDDMM.mmW C
COMPRESSED_LEN = const(13)  # T YYYY XXXX C cs T


def _digit(c):
    return 48 <= c <= 57


def _num(b, start, end):
    """b[start:end] が全て数字なら int、そうでなければ None。
    (MicroPython の bytes には isdigit が無いので自前で見る)"""
    if start >= end or end > len(b):
        return None
    n = 0
    for i in range(start, end):
        c = b[i]
        if not _digit(c):
            return None
        n = n * 10 + c - 48
    return n


# 位置あいまいさ n 桁のときに足す値(1/100 分単位): 落とした桁の範囲の中心を位置とする
AMBIGUITY_CENTER = (0, 5, 50, 500, 3000)


def valid_table(c):
    """シンボル表の文字: '/' '\' またはオーバーレイ 0-9 A-Z"""
    return c == 0x2F or c == 0x5C or 0x30 <= c <= 0x39 or 0x41 <= c <= 0x5A


def _dm_to_deg(b, i, ndeg):
    """'DDMM.mm' / 'DDDMM.mm' を度に。空白(位置あいまいさ)の桁は、その範囲の中心とみなす。
    -> (度 float, 空白の数) / 失敗時 None"""
    v = bytearray(b[i : i + ndeg + 5])
    if len(v) != ndeg + 5 or v[ndeg + 2] != 0x2E:  # '.'
        return None
    amb = 0
    for k in range(len(v)):
        if k == ndeg + 2:
            continue
        if v[k] == 0x20:
            v[k] = 0x30
            amb += 1
        elif not _digit(v[k]):
            return None
    deg = _num(v, 0, ndeg)
    mins = _num(v, ndeg, ndeg + 2)
    hund = _num(v, ndeg + 3, ndeg + 5)
    if deg is None or mins is None or hund is None or mins >= 60 or amb > 4:
        return None
    return deg + (mins * 100 + hund + AMBIGUITY_CENTER[amb]) / 6000, amb


def decode_uncompressed(b, i):
    """b[i:] の 'DDMM.mmN/DDDMM.mmW>' -> (lat, lon, table, code, ambiguity) / None"""
    if len(b) < i + UNCOMPRESSED_LEN:
        return None
    lat = _dm_to_deg(b, i, 2)
    lon = _dm_to_deg(b, i + 9, 3)
    if lat is None or lon is None:
        return None
    ns = b[i + 7] & 0xDF  # 大文字化
    ew = b[i + 17] & 0xDF
    if ns not in (0x4E, 0x53) or ew not in (0x45, 0x57):  # N S / E W
        return None
    la = lat[0] if ns == 0x4E else -lat[0]
    lo = lon[0] if ew == 0x45 else -lon[0]
    if not (-90 <= la <= 90 and -180 <= lo <= 180) or not valid_table(b[i + 8]):
        return None
    return la, lo, chr(b[i + 8]), chr(b[i + 18]), max(lat[1], lon[1])


def _b91(b, i, n):
    v = 0
    for k in range(i, i + n):
        c = b[k] - 33
        if not 0 <= c <= 90:
            return None
        v = v * 91 + c
    return v


def decode_compressed(b, i):
    """b[i:] の圧縮位置 -> (lat, lon, table, code, course, speed_kmh, alt_m) / None
    course/speed/alt は含まれていなければ None。"""
    if len(b) < i + COMPRESSED_LEN:
        return None
    t = b[i]
    if 0x61 <= t <= 0x6A:  # a-j はオーバーレイ 0-9
        t -= 0x31
    if not valid_table(t):
        return None
    y = _b91(b, i + 1, 4)
    x = _b91(b, i + 5, 4)
    if y is None or x is None:
        return None
    lat = 90 - y / 380926
    lon = -180 + x / 190463
    course = speed = alt = None
    c = b[i + 10]
    s = b[i + 11]
    if c != 0x20:  # ' ' なら cs なし
        ctype = b[i + 12] - 33
        if ctype >= 0 and (ctype >> 3) & 3 == 2:  # NMEA ソースが GGA → cs は高度
            cs = (c - 33) * 91 + (s - 33)
            alt = (1.002**cs) * FOOT_M
        elif 0 <= c - 33 <= 89:
            course = (c - 33) * 4 or 360
            speed = (1.08 ** (s - 33) - 1) * KNOT_KMH
        # c == '{' は無線到達距離(未対応)
    return lat, lon, chr(t), chr(b[i + 9]), course, speed, alt


def decode_course_speed(b, i):
    """b[i:] の 'ddd/sss'(進行方向/ノット)-> (course, speed_kmh) / None
    course 0 は不明として None を返す。"""
    if len(b) < i + 7 or b[i + 3] != 0x2F:  # '/'
        return None
    crs = _num(b, i, i + 3)
    spd = _num(b, i + 4, i + 7)
    if crs is None or spd is None or crs > 360:
        return None
    return (crs or None), spd * KNOT_KMH


def extract_altitude(comment):
    """コメント中の '/A=nnnnnn'(フィート)を取り除き -> (comment, alt_m / None)"""
    j = comment.find(b"/A=")
    if j < 0 or len(comment) < j + 9:
        return comment, None
    neg = comment[j + 3] == 0x2D  # '-'
    v = _num(comment, j + 4 if neg else j + 3, j + 9)
    if v is None:
        return comment, None
    return comment[:j] + comment[j + 9 :], (-v if neg else v) * FOOT_M
