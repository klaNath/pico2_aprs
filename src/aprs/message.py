"""APRS メッセージ(情報フィールドが ':' のもの)の組み立てと検査(APRS 1.0.1 第 14 章)。

    :ADDRESSEE:text{NO      メッセージ(宛先は空白で 9 文字に詰める。番号は 1-5 文字)
    :ADDRESSEE:ackNO        受け取った(ack)
    :ADDRESSEE:rejNO        断った(rej)

解析は aprs.packet(kind "message")。ここは送る側だけ。値が不正なら MessageError(key, msg)。
"""

TO_MAX = 9
TEXT_MAX = 67
NO_MAX = 5
_TEXT_BAD = "|~{"  # 本文に使えない文字(APRS 1.0.1)


class MessageError(ValueError):
    """送るメッセージの値が不正。key: "to" / "text" / "no"、msg: 英語の短い説明"""

    def __init__(self, key, msg):
        super().__init__("%s: %s" % (key, msg))
        self.key = key
        self.msg = msg


def _alnum_upper(c):
    return "A" <= c <= "Z" or "0" <= c <= "9"


def check_to(to):
    """宛先(コールサインと SSID)-> 大文字の str。英数字と '-'、1-9 文字。小文字は大文字にする"""
    if not isinstance(to, str):
        raise MessageError("to", "string required")
    s = to.upper()
    if not 1 <= len(s) <= TO_MAX or not all(_alnum_upper(c) or c == "-" for c in s):
        raise MessageError("to", "1-%d letters, digits or '-'" % TO_MAX)
    return s


def check_text(text):
    """本文 -> str。印字できる ASCII 1-67 文字で、'|' '~' '{' を含まない"""
    if not isinstance(text, str):
        raise MessageError("text", "string required")
    if not 1 <= len(text) <= TEXT_MAX or not all(
            " " <= c <= "~" and c not in _TEXT_BAD for c in text):
        raise MessageError("text", "1-%d printable ASCII chars without | ~ {" % TEXT_MAX)
    return text


def check_no(no):
    """メッセージ番号 -> str。英数字 1-5 文字"""
    if not isinstance(no, str) or not 1 <= len(no) <= NO_MAX or not all(
            _alnum_upper(c) or "a" <= c <= "z" for c in no):
        raise MessageError("no", "1-%d letters or digits" % NO_MAX)
    return no


def _head(to):
    return b":" + to.encode() + b" " * (TO_MAX - len(to)) + b":"


def info(to, text, no=None):
    """メッセージの情報フィールド(bytes)。no を省くと番号なし(ack を求めない)"""
    b = _head(check_to(to)) + check_text(text).encode()
    if no is not None:
        b += b"{" + check_no(no).encode()
    return b


def ack_info(to, no):
    return _head(check_to(to)) + b"ack" + check_no(no).encode()


def rej_info(to, no):
    return _head(check_to(to)) + b"rej" + check_no(no).encode()
