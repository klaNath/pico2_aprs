"""NMEA 0183 の解析(GNSS モジュールの種類に依存しない部分)。

読む文: RMC(時刻・日付・位置・速度・進行方向)、GGA(測位品質・衛星数・高度)、GSA(2D/3D)。
送信元の識別子(GP/GN/GL/GA/BD など)は問わない。チェックサムが合わない行は捨てる。
u-blox M10 のように NMEA 4.1 以降で欄が増えていても、使う欄の位置は変わらないので読める。

UART からのバイト列は LineReader で 1 行ずつに区切る(固定バッファ、1 行ごとの確保は最小限)。
"""

KNOT_KMH = 1.852


def checksum_ok(line):
    """'$....*hh'(bytes / str、改行は除いたもの)のチェックサムを確かめる"""
    if isinstance(line, str):
        line = line.encode()
    if len(line) < 4 or line[0] != 0x24:  # '$'
        return False
    star = line.rfind(b"*")
    if star < 0 or star + 3 > len(line):
        return False
    c = 0
    for b in line[1:star]:
        c ^= b
    try:
        return c == int(line[star + 1 : star + 3], 16)
    except ValueError:
        return False


def _dm(field, hemi, deg_digits):
    """'4807.038', 'N' -> 48.1173(度)。空なら None"""
    if not field or not hemi:
        return None
    try:
        deg = int(field[:deg_digits])
        mins = float(field[deg_digits:])
    except ValueError:
        return None
    v = deg + mins / 60
    return -v if hemi in ("S", "W") else v


def _f(s):
    try:
        return float(s) if s else None
    except ValueError:
        return None


def _days_from_civil(y, m, d):
    """グレゴリオ暦の日付 -> 1970-01-01 からの日数(Howard Hinnant のアルゴリズム)"""
    y -= m <= 2
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


class Fix:
    """GNSS から得た最新の値。RMC/GGA/GSA を受けるたびに更新する。"""

    def __init__(self):
        self.valid = False  # RMC の状態 'A'
        self.fix = 0  # 0 = なし、2 = 2D、3 = 3D(GSA)。GSA が無ければ GGA の品質から推定
        self.sats = 0
        self.hdop = None
        self.lat = None
        self.lon = None
        self.alt = None  # m(GGA、平均海面)
        self.speed = None  # km/h
        self.course = None  # 度
        self.utc_s = None  # 1970-01-01 からの UTC 秒(RMC の日付と時刻がそろったとき)
        self.updated = 0  # 更新のたびに増える

    def feed(self, line):
        """1 行(bytes / str、改行なし)を解析する。解析した文の種類('RMC' など)/ None"""
        if isinstance(line, (bytes, bytearray)):
            try:
                line = line.decode()
            except UnicodeError:
                return None
        line = line.strip()
        if not checksum_ok(line):
            return None
        f = line[1 : line.rfind("*")].split(",")
        kind = f[0][2:] if len(f[0]) == 5 else f[0]
        try:
            if kind == "RMC" and len(f) >= 10:
                self._rmc(f)
            elif kind == "GGA" and len(f) >= 10:
                self._gga(f)
            elif kind == "GSA" and len(f) >= 3:
                self._gsa(f)
            else:
                return None
        except (ValueError, IndexError):
            return None
        self.updated += 1
        return kind

    def _rmc(self, f):
        self.valid = f[2] == "A"
        if self.valid:
            self.lat = _dm(f[3], f[4], 2)
            self.lon = _dm(f[5], f[6], 3)
            kn = _f(f[7])
            self.speed = None if kn is None else kn * KNOT_KMH
            self.course = _f(f[8])
        t, d = f[1], f[9]
        if len(t) >= 6 and len(d) == 6:
            days = _days_from_civil(2000 + int(d[4:6]), int(d[2:4]), int(d[0:2]))
            self.utc_s = days * 86400 + int(t[0:2]) * 3600 + int(t[2:4]) * 60 + int(t[4:6])

    def _gga(self, f):
        q = int(f[6]) if f[6] else 0
        self.sats = int(f[7]) if f[7] else 0
        self.hdop = _f(f[8])
        if q > 0:
            self.alt = _f(f[9])
            self.lat = _dm(f[2], f[3], 2)
            self.lon = _dm(f[4], f[5], 3)
            if self.fix < 2:
                self.fix = 3 if self.alt is not None else 2
        else:
            self.fix = 0

    def _gsa(self, f):
        # NMEA 4.1 以降も 3 番目(添字 2)が 1/2/3 の測位状態
        m = int(f[2]) if f[2] else 1
        self.fix = m if m in (2, 3) else 0


class LineReader:
    """UART などから来るバイト列を '\\n' で区切って 1 行ずつ返す。長すぎる行は捨てる。"""

    def __init__(self, maxlen=96):
        self.buf = bytearray(maxlen)
        self.n = 0
        self.overflow = False

    def feed(self, data):
        """data(bytes)を足し、完成した行のリストを返す(改行は除く)"""
        out = []
        buf = self.buf
        for b in data:
            if b == 0x0A:  # '\n'
                if not self.overflow and self.n:
                    n = self.n - 1 if buf[self.n - 1] == 0x0D else self.n
                    out.append(bytes(buf[:n]))
                self.n = 0
                self.overflow = False
            elif self.n < len(buf):
                buf[self.n] = b
                self.n += 1
            else:
                self.overflow = True
        return out
