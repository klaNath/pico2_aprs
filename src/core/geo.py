"""位置の計算と表示用の整形(距離・方位・16 方位・度分表記・地図の対数スケール)。"""

import math

EARTH_R_KM = 6371.0
COMPASS16 = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
             "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")


def distance_km(lat1, lon1, lat2, lon2):
    """大円距離(haversine)。数十 km 程度でも単精度 float で十分な精度が出る形にしてある。"""
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R_KM * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1, lon1, lat2, lon2):
    """1 から見た 2 の方位(真北 0、時計回り、0 <= x < 360)"""
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(y, x)) % 360


def compass16(deg):
    return COMPASS16[int((deg % 360) / 22.5 + 0.5) % 16]


def fmt_dm(value, pos, neg, deg_digits):
    """度 -> 'N35 40.12' 形式(度と分の間は空白。8x8 フォントに度記号が無いため)"""
    h = pos if value >= 0 else neg
    t = int(abs(value) * 6000 + 0.5)  # 1/100 分
    d, r = divmod(t, 6000)
    return "%s%0*d %02d.%02d" % (h, deg_digits, d, r // 100, r % 100)


def fmt_latlon(lat, lon):
    """'N35 40.12 E139 46.03'(20 文字)"""
    return fmt_dm(lat, "N", "S", 2) + " " + fmt_dm(lon, "E", "W", 3)


def gridlocator(lat, lon):
    """緯度・経度(十進度、南緯・西経は負)-> Maidenhead Grid Locator 8 文字('PM95VS27')"""
    x = max((lon + 180.0) / 20.0, 0.0)
    y = max((lat + 90.0) / 10.0, 0.0)
    out = []
    for base in (18, 10, 24, 10):  # field(A-R)、square(0-9)、subsquare(A-X)、extended square(0-9)
        # 端(北緯 90 度、東経 180 度)や単精度 float の丸めで範囲を超えたら最後の区画に入れる
        ix, iy = min(int(x), base - 1), min(int(y), base - 1)
        if base == 10:
            out.append(chr(48 + ix) + chr(48 + iy))
        else:
            out.append(chr(65 + ix) + chr(65 + iy))
        nxt = 10 if base != 10 else 24
        x = (x - ix) * nxt
        y = (y - iy) * nxt
    return "".join(out)


def fmt_distance(km):
    if km < 10:
        return "%.1fkm" % km
    return "%dkm" % int(km + 0.5)


def log_radius(km, r_max, d_min=0.5, d_max=100.0):
    """地図の対数スケール: d_min 以下は中心付近、d_max で r_max。d_max を超えたら r_max。"""
    if km <= d_min:
        return 0.0
    if km >= d_max:
        return float(r_max)
    return r_max * math.log(km / d_min) / math.log(d_max / d_min)


def map_xy(km, deg, cx, cy, r_max, d_min=0.5, d_max=100.0):
    """距離と方位 -> 画面座標(北が上)。"""
    r = log_radius(km, r_max, d_min, d_max)
    a = math.radians(deg)
    return int(cx + r * math.sin(a) + 0.5), int(cy - r * math.cos(a) + 0.5)


JST_OFFSET_S = 9 * 3600


def days_from_civil(y, m, d):
    """グレゴリオ暦の日付 -> 1970-01-01 からの日数(Howard Hinnant のアルゴリズム)"""
    y -= m <= 2
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def utc_seconds(year, month, day, hour, minute, second):
    """UTC の日時 -> 1970-01-01 からの秒(MicroPython の rp2 の time.time() と同じ起点)"""
    return days_from_civil(year, month, day) * 86400 + hour * 3600 + minute * 60 + second


def fmt_jst(epoch_utc_s):
    """UTC の秒(MicroPython の time.time() と同じ起点)-> 'HH:MM:SS'(JST)"""
    t = (int(epoch_utc_s) + JST_OFFSET_S) % 86400
    return "%02d:%02d:%02d" % (t // 3600, t // 60 % 60, t % 60)


def fmt_age(sec):
    """経過時間 -> '12s' / '5m' / '2h' / '3d'"""
    if sec < 60:
        return "%ds" % sec
    if sec < 3600:
        return "%dm" % (sec // 60)
    if sec < 86400:
        return "%dh" % (sec // 3600)
    return "%dd" % (sec // 86400)
