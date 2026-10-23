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
