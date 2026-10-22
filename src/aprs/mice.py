"""Mic-E のデコードとエンコード。

参照: APRS Protocol Reference 1.0.1 第 10 章、APRS 1.2 の Mic-E 追補。
宛先アドレス(6 文字)に緯度・メッセージ種別・N/S・経度オフセット・E/W を、
情報フィールドに経度・速度・進行方向・シンボル・高度・コメントを入れる。
"""

from micropython import const

from aprs.position import AMBIGUITY_CENTER, KNOT_KMH, valid_table

# 標準メッセージ(A/B/C ビット = 7 - index)。index 7 は Emergency(000)
MESSAGES = (
    "Off Duty",
    "En Route",
    "In Service",
    "Returning",
    "Committed",
    "Special",
    "Priority",
    "Emergency",
)
MSG_EMERGENCY = const(7)

# 情報フィールドの先頭(データ種別)
CURRENT = const(0x60)  # '`' 現在の GPS 位置
OLD = const(0x27)  # "'" 古い位置

# シンボル表の直後に置かれうる機種/種別の文字(1.2 追補)
_TYPE_CHARS = (0x20, 0x3E, 0x5D, 0x60, 0x27)  # ' ' '>' ']' '`' "'"


def _dest_char(c):
    """宛先 1 文字 -> (数字 0-9 / -1 = 空白, メッセージビット 0/1/2=カスタム, フラグ 0/1) / None"""
    if 0x30 <= c <= 0x39:
        return c - 0x30, 0, 0
    if 0x41 <= c <= 0x4A:  # A-J カスタム 1
        return c - 0x41, 2, 0
    if c == 0x4B:  # K カスタム 1 + 空白
        return -1, 2, 0
    if c == 0x4C:  # L 0 + 空白
        return -1, 0, 0
    if 0x50 <= c <= 0x59:  # P-Y 標準 1
        return c - 0x50, 1, 1
    if c == 0x5A:  # Z 標準 1 + 空白
        return -1, 1, 1
    return None


def decode(dst, info):
    """dst: 宛先コールサイン(SSID なしの str)、info: 情報フィールド bytes。
    -> dict(lat, lon, ambiguity, speed, course, table, code, alt, message, comment, current)
    / 形式が違えば None。speed は km/h、alt は m、course は度(不明なら None)。"""
    d = dst.encode()
    if len(d) != 6 or len(info) < 9 or not valid_table(info[8]):
        return None
    digits = []
    bits = []
    flags = []
    for c in d:
        r = _dest_char(c)
        if r is None:
            return None
        digits.append(r[0])
        bits.append(r[1])
        flags.append(r[2])
    # 位置あいまいさ: 空白は右側から連続していなければならない
    amb = 0
    for k in range(5, -1, -1):
        if digits[k] >= 0:
            break
        amb += 1
    for k in range(6 - amb):
        if digits[k] < 0:
            return None
    dg = [x if x >= 0 else 0 for x in digits]
    lat_deg = dg[0] * 10 + dg[1]
    lat_min = dg[2] * 10 + dg[3]
    if lat_deg > 90 or lat_min >= 60:
        return None
    lat = lat_deg + (lat_min * 100 + dg[4] * 10 + dg[5] + AMBIGUITY_CENTER[amb]) / 6000
    if not flags[3]:
        lat = -lat

    # メッセージ種別
    std = [b == 1 for b in bits[:3]]
    cus = [b == 2 for b in bits[:3]]
    v = (bits[0] != 0) << 2 | (bits[1] != 0) << 1 | (bits[2] != 0)
    if v == 0:
        message = MESSAGES[MSG_EMERGENCY]
    elif any(std) and any(cus):
        message = "Unknown"
    elif any(cus):
        message = "Custom-%d" % (7 - v)
    else:
        message = MESSAGES[7 - v]

    # 経度
    lon_deg = info[1] - 28
    if flags[4]:
        lon_deg += 100
    if 180 <= lon_deg <= 189:
        lon_deg -= 80
    elif 190 <= lon_deg <= 199:
        lon_deg -= 190
    lon_min = info[2] - 28
    if lon_min >= 60:
        lon_min -= 60
    lon_hun = info[3] - 28
    if not (0 <= lon_deg <= 179 and 0 <= lon_min <= 59 and 0 <= lon_hun <= 99):
        return None
    if amb >= 4:
        lon_min = 0
    elif amb == 3:
        lon_min -= lon_min % 10
    if amb >= 2:
        lon_hun = 0
    elif amb == 1:
        lon_hun -= lon_hun % 10
    lon = lon_deg + (lon_min * 100 + lon_hun + AMBIGUITY_CENTER[amb]) / 6000
    if flags[5]:
        lon = -lon

    # 速度・進行方向
    sp = info[4] - 28
    dc = info[5] - 28
    se = info[6] - 28
    knots = sp * 10 + dc // 10
    if knots >= 800:
        knots -= 800
    course = (dc % 10) * 100 + se
    if course >= 400:
        course -= 400

    # 高度とコメント
    rest = info[9:]
    if rest and rest[0] in _TYPE_CHARS:
        rest = rest[1:]
    alt = None
    if len(rest) >= 4 and rest[3] == 0x7D:  # 'xxx}'
        a = 0
        ok = True
        for k in range(3):
            c = rest[k] - 33
            if not 0 <= c <= 90:
                ok = False
                break
            a = a * 91 + c
        if ok:
            alt = a - 10000
            rest = rest[4:]

    return {
        "lat": lat,
        "lon": lon,
        "ambiguity": amb,
        "speed": knots * KNOT_KMH,
        "course": course if 0 < course <= 360 else None,
        "table": chr(info[8]),
        "code": chr(info[7]),
        "alt": alt,
        "message": message,
        "comment": rest,
        "current": info[0] == CURRENT,
    }


def _lat_digits(lat):
    """|lat| を 1/100 分単位に丸めて 'DDMMhh' の 6 桁(int のリスト)に"""
    t = int(abs(lat) * 6000 + 0.5)
    t = min(t, 90 * 6000)
    deg, r = divmod(t, 6000)
    mins, hun = divmod(r, 100)
    return [deg // 10, deg % 10, mins // 10, mins % 10, hun // 10, hun % 10]


def encode(lat, lon, speed=0.0, course=0, table="/", code=">", message=0, alt=None,
           comment=b"", current=True):
    """Mic-E にエンコードする。
    lat/lon: 度(北・東が正)、speed: km/h、course: 度(0 = 不明)、message: MESSAGES の index、
    alt: m(None なら付けない)、comment: bytes。
    -> (宛先コールサイン str 6 文字, 情報フィールド bytes)"""
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("lat/lon out of range")
    if not 0 <= message <= MSG_EMERGENCY:
        raise ValueError("bad message index")
    if not 0 <= course <= 360:
        raise ValueError("course out of range")

    # 宛先: 緯度の各桁 + A/B/C, N/S, 経度オフセット, W/E
    lo = int(abs(lon) * 6000 + 0.5)
    lo = min(lo, 180 * 6000 - 1)  # 180°ちょうどは表現できない
    lon_deg, r = divmod(lo, 6000)
    lon_min, lon_hun = divmod(r, 100)
    offset = lon_deg < 10 or lon_deg >= 100
    v = 7 - message if message < MSG_EMERGENCY else 0
    flags = (v >> 2 & 1, v >> 1 & 1, v & 1, lat >= 0, offset, lon < 0)
    dg = _lat_digits(lat)
    dst = bytearray(6)
    for k in range(6):
        dst[k] = (0x50 if flags[k] else 0x30) + dg[k]

    info = bytearray(9)
    info[0] = CURRENT if current else OLD
    if lon_deg < 10:
        info[1] = lon_deg + 118
    elif lon_deg < 100:
        info[1] = lon_deg + 28
    elif lon_deg < 110:
        info[1] = lon_deg + 8
    else:
        info[1] = lon_deg - 72
    info[2] = lon_min + 28 if lon_min >= 10 else lon_min + 88
    info[3] = lon_hun + 28

    knots = int(speed / KNOT_KMH + 0.5)
    knots = max(0, min(knots, 799))
    sp = knots // 10
    # SP は 0-19 なら +80(デコード時に 800 ノットを引く)して印字可能文字にする
    info[4] = sp + 108 if sp <= 19 else sp + 28
    # DC は +4(デコード時に 400 度を引く)して制御文字を避ける
    info[5] = (knots % 10) * 10 + course // 100 + 32
    info[6] = course % 100 + 28
    info[7] = ord(code)
    info[8] = ord(table)

    tail = bytearray()
    if alt is not None:
        a = int(alt + 10000.5)
        if not 0 <= a < 91**3:
            raise ValueError("altitude out of range")
        tail.append(a // 8281 + 33)
        tail.append(a // 91 % 91 + 33)
        tail.append(a % 91 + 33)
        tail.append(0x7D)
    elif comment and comment[0] in _TYPE_CHARS:
        tail.append(0x20)  # 先頭の種別文字として読み飛ばされないよう空白を挟む
    return dst.decode(), bytes(info + tail + comment)
