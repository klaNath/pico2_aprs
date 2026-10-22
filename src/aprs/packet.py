"""APRS パケット(AX.25 UI フレームの情報フィールド)の解析。

対応する種別(kind):
    "position"  位置('!' '=' '/' '@'、非圧縮・圧縮)
    "mice"      Mic-E('`' "'")
    "object"    オブジェクト(';')
    "item"      アイテム(')')
    "message"   メッセージ / ack / rej / 告知(':')
    "status"    ステータス('>')
    "other"     上記以外(info はそのまま raw に残る)
'}'(サードパーティ)は中身のパケットを解析し、third_party に元の送信局の経路を残す。
"""

from aprs import mice
from aprs.position import (
    COMPRESSED_LEN,
    UNCOMPRESSED_LEN,
    decode_compressed,
    decode_course_speed,
    decode_uncompressed,
    extract_altitude,
)
from tnc.ax25 import Frame


def to_text(b):
    """bytes を表示用 str に。UTF-8 として不正なら非 ASCII を '?' に置き換える
    (MicroPython の decode は errors 引数を無視して例外を出すため)。"""
    try:
        return b.decode()
    except UnicodeError:
        return "".join(chr(c) if 0x20 <= c < 0x7F else "?" for c in b)


class Packet:
    __slots__ = (
        "src",  # 送信局 'JA1XYZ-9'
        "dst",  # 宛先 'APRS'(Mic-E では緯度が入っている)
        "path",  # 経路 ['WIDE1-1*', ...]
        "kind",
        "raw",  # 情報フィールド bytes
        "lat",  # 度(北が正)
        "lon",  # 度(東が正)
        "ambiguity",  # 位置あいまいさ(落とされた桁数)
        "table",  # シンボル表 '/' '\\' またはオーバーレイ文字
        "code",  # シンボル
        "course",  # 度 1-360 / None
        "speed",  # km/h / None
        "alt",  # m / None
        "comment",  # str
        "timestamp",  # 'DDHHMMz' など / None
        "messaging",  # メッセージ受信可能な局か('=' '@')
        "name",  # オブジェクト/アイテム名
        "alive",  # オブジェクト/アイテムが有効か(False = 削除)
        "addressee",  # メッセージの宛先
        "text",  # メッセージ本文 / ステータス本文
        "msgno",  # メッセージ番号 / None
        "response",  # 'ack' / 'rej'(msgno への応答)/ None
        "mice_msg",  # Mic-E のメッセージ種別 'En Route' など
        "third_party",  # '}' で包まれていた場合の外側ヘッダ / None
    )

    def __init__(self, src, dst, path, raw):
        self.src = src
        self.dst = dst
        self.path = path
        self.raw = raw
        self.kind = "other"
        # 型注釈は pyright 用(関数内の注釈は実行時に評価されない)
        self.lat: "float | None" = None
        self.lon: "float | None" = None
        self.ambiguity = 0
        self.table: "str | None" = None
        self.code: "str | None" = None
        self.course: "int | None" = None
        self.speed: "float | None" = None
        self.alt: "float | None" = None
        self.comment = ""
        self.timestamp: "str | None" = None
        self.messaging = False
        self.name: "str | None" = None
        self.alive = True
        self.addressee: "str | None" = None
        self.text: "str | None" = None
        self.msgno: "str | None" = None
        self.response: "str | None" = None
        self.mice_msg: "str | None" = None
        self.third_party: "str | None" = None

    @property
    def has_position(self):
        return self.lat is not None

    def __repr__(self):
        return "<Packet %s %s %s>" % (self.kind, self.src, self.raw)


def parse_frame(frame):
    """tnc.ax25.Frame -> Packet"""
    return parse(str(frame.src), frame.dst.call, [str(d) for d in frame.digis], frame.info)


def parse(src, dst, path, info):
    """src/dst: str(dst は SSID なしでよい)、path: str のリスト、info: bytes -> Packet
    解析できない部分があっても例外にはせず、kind="other" や comment に残す。"""
    p = Packet(src, dst, path, info)
    if not info:
        return p
    t = info[0]
    try:
        if t == 0x21 or t == 0x3D:  # '!' '='
            p.messaging = t == 0x3D
            _position(p, info, 1)
        elif t == 0x2F or t == 0x40:  # '/' '@'
            p.messaging = t == 0x40
            p.timestamp = to_text(info[1:8])
            _position(p, info, 8)
        elif t == 0x60 or t == 0x27:  # '`' "'"
            _mice(p, dst, info)
        elif t == 0x3B:  # ';'
            _object(p, info)
        elif t == 0x29:  # ')'
            _item(p, info)
        elif t == 0x3A:  # ':'
            _message(p, info)
        elif t == 0x3E:  # '>'
            _status(p, info)
        elif t == 0x7D:  # '}'
            return _third_party(p, info)
    except (IndexError, ValueError):
        p.kind = "other"
        p.lat = p.lon = None
    return p


def _position(p, info, i):
    """info[i:] の位置(非圧縮/圧縮)+ 拡張 + コメント"""
    c = info[i] if i < len(info) else 0
    if 0x30 <= c <= 0x39 or c == 0x20:  # 数字(または あいまいさの空白)で始まれば非圧縮
        r = decode_uncompressed(info, i)
        if r is None:
            return False
        p.lat, p.lon, p.table, p.code, p.ambiguity = r
        rest = info[i + UNCOMPRESSED_LEN :]
        cs = decode_course_speed(rest, 0)
        if cs is not None:
            p.course, p.speed = cs
            rest = rest[7:]
    else:
        r = decode_compressed(info, i)
        if r is None:
            return False
        p.lat, p.lon, p.table, p.code, p.course, p.speed, p.alt = r
        rest = info[i + COMPRESSED_LEN :]
    rest, alt = extract_altitude(rest)
    if alt is not None:
        p.alt = alt
    p.comment = to_text(rest)
    if p.kind == "other":
        p.kind = "position"
    return True


def _mice(p, dst, info):
    r = mice.decode(dst, info)
    if r is None:
        return
    p.kind = "mice"
    p.lat = r["lat"]
    p.lon = r["lon"]
    p.ambiguity = r["ambiguity"]
    p.speed = r["speed"]
    p.course = r["course"]
    p.table = r["table"]
    p.code = r["code"]
    p.alt = r["alt"]
    p.mice_msg = r["message"]
    p.comment = to_text(r["comment"])


def _object(p, info):
    # ;NNNNNNNNN*DDHHMMzPOSITION
    if len(info) < 18 or info[10] not in (0x2A, 0x5F):  # '*' '_'
        return
    p.name = to_text(info[1:10]).rstrip()
    p.alive = info[10] == 0x2A
    p.timestamp = to_text(info[11:18])
    p.kind = "object"
    if not _position(p, info, 18):
        p.kind = "other"


def _item(p, info):
    # )NAME!POSITION(名前は 3-9 文字、'!' = 有効 / '_' = 削除)
    for k in range(4, min(len(info), 11)):
        c = info[k]
        if c == 0x21 or c == 0x5F:
            p.name = to_text(info[1:k])
            p.alive = c == 0x21
            p.kind = "item"
            if not _position(p, info, k + 1):
                p.kind = "other"
            return


def _message(p, info):
    # :ADDRESSEE:text{id   (宛先は空白で 9 文字に詰められる)
    if len(info) < 11 or info[10] != 0x3A:
        return
    p.kind = "message"
    p.addressee = to_text(info[1:10]).rstrip()
    body = info[11:]
    head = body[:3]
    if (head == b"ack" or head == b"rej") and b"{" not in body and 1 <= len(body) - 3 <= 5:
        p.response = head.decode()
        p.msgno = to_text(body[3:])
        return
    j = body.rfind(b"{")
    if j >= 0:
        p.msgno = to_text(body[j + 1 :])
        body = body[:j]
    p.text = to_text(body)


def _status(p, info):
    body = info[1:]
    # 先頭が 'DDHHMMz' のタイムスタンプなら分ける
    if len(body) >= 7 and body[6] == 0x7A and all(0x30 <= c <= 0x39 for c in body[:6]):
        p.timestamp = to_text(body[:7])
        body = body[7:]
    p.kind = "status"
    p.text = to_text(body)


def _third_party(p, info):
    try:
        inner = Frame.parse(bytes(info[1:]))
    except ValueError:
        return p
    q = parse_frame(inner)
    q.third_party = "%s>%s,%s" % (p.src, p.dst, ",".join(p.path)) if p.path else "%s>%s" % (
        p.src,
        p.dst,
    )
    return q
