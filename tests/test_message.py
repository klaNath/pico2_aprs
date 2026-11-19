"""aprs.message(メッセージの組み立てと検査)と、aprs.packet のメッセージの解析のテスト。
PC と実機の両方で動く。コールサインはすべて架空の例(Q で始まる、割り当ての無いプレフィックス)。"""

from aprs import message as M
from aprs.packet import parse


def _err(fn, *a):
    try:
        fn(*a)
    except M.MessageError as e:
        return e.key
    raise AssertionError("MessageError expected")


def test_info_pads_addressee_and_adds_number():
    assert M.info("Q1AAA-9", "Hello", "12") == b":Q1AAA-9  :Hello{12"
    assert M.info("q1aaa", "Hi") == b":Q1AAA    :Hi"  # 小文字は大文字に、番号なし
    assert M.ack_info("Q1AAA-9", "12") == b":Q1AAA-9  :ack12"
    assert M.rej_info("Q1AAA-12", "AB1") == b":Q1AAA-12 :rejAB1"


def test_limits():
    assert M.check_to("Q1AAAA-15") == "Q1AAAA-15"  # 9 文字
    assert _err(M.check_to, "Q1AAAAA-15") == "to"  # 10 文字
    assert _err(M.check_to, "") == "to" and _err(M.check_to, "Q1 AA") == "to"
    assert _err(M.check_to, None) == "to"
    assert M.check_text("x" * 67) == "x" * 67
    assert _err(M.check_text, "x" * 68) == "text" and _err(M.check_text, "") == "text"
    for bad in ("a|b", "a~b", "a{b", "tab\there", "あ"):  # 禁止文字、制御文字、ASCII 以外
        assert _err(M.check_text, bad) == "text", bad
    assert M.check_text(" spaces kept ") == " spaces kept "
    assert M.check_no("1") == "1" and M.check_no("ab12Z") == "ab12Z"
    assert _err(M.check_no, "123456") == "no" and _err(M.check_no, "1-2") == "no"
    assert _err(M.check_no, 12) == "no"


def test_round_trip_through_parser():
    p = parse("Q1TEST-7", "APZPI2", ["WIDE1-1"], M.info("Q1AAA-9", "Hello there", "345"))
    assert p.kind == "message" and p.addressee == "Q1AAA-9"
    assert (p.text, p.msgno, p.response, p.replyack) == ("Hello there", "345", None, None)
    a = parse("Q1AAA-9", "APRS", [], M.ack_info("Q1TEST-7", "345"))
    assert (a.addressee, a.response, a.msgno) == ("Q1TEST-7", "ack", "345")


def test_reply_ack_forms():
    # APRS 1.1: 'text{MM}AA' は新しいメッセージ MM で、こちらの AA への ack を兼ねる
    p = parse("Q1AAA", "APRS", [], b":Q1TEST-7 :Thanks{12}AB")
    assert (p.text, p.msgno, p.replyack) == ("Thanks", "12", "AB")
    q = parse("Q1AAA", "APRS", [], b":Q1TEST-7 :Hi{12}")  # 対応を示すだけ(ack は無し)
    assert (q.msgno, q.replyack) == ("12", None)
    r = parse("Q1AAA", "APRS", [], b":Q1TEST-7 :ack12}AB")
    assert (r.response, r.msgno) == ("ack", "12")
    s = parse("Q1AAA", "APRS", [], b":Q1TEST-7 :ack7\r\n")  # 末尾の改行は落とす
    assert (s.response, s.msgno) == ("ack", "7")
    t = parse("Q1AAA", "APRS", [], b":Q1TEST-7 :acknowledged")  # 番号が長すぎる: 本文
    assert t.response is None and t.text == "acknowledged"
