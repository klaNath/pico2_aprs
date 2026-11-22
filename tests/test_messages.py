"""core.messages(送信待ちの列、再送、ack、受信箱)と core.msgfile(SD への保存)のテスト。
PC と実機の両方で動く。ファイル操作は偽物。コールサインはすべて架空の例(Q で始まる)。"""

from test_stationfile import FakeFs

from aprs.message import MessageError
from aprs.packet import parse
from core.messages import SEEN_MS, Messages, QueueFull
from core.msgfile import MsgFile, format_line, parse_line

OWN = "Q1TEST-7"
UTC = 1794700000


def make(**kw):
    log = []
    m = Messages(OWN, on_change=lambda msg, why: log.append((why, msg.no, msg.state)), **kw)
    return m, log


def P(src, info):
    return parse(src, "APRS", ["WIDE1-1*"], info)


def _send_all(m, now):
    """今送れるものを全部送ったことにする -> 送った情報フィールドのリスト"""
    out = []
    while True:
        item = m.next_tx(now)
        if item is None:
            return out
        out.append(item[0])
        m.sent(item[1], now)


def test_send_retries_then_fails():
    m, log = make(retry_s=(30, 60))
    msg = m.send("q1aaa-9", "Hello", 1000, UTC)
    assert (msg.no, msg.peer, msg.state, msg.tries) == ("1", "Q1AAA-9", "pending", 0)
    assert m.pending() == 1 and log == [("new", "1", "pending")]
    assert _send_all(m, 1000) == [b":Q1AAA-9  :Hello{1"]
    assert msg.tries == 1 and m.next_tx(30_999) is None
    m.poll(30_999)
    assert _send_all(m, 31_000) == [b":Q1AAA-9  :Hello{1"] and msg.tries == 2
    m.poll(90_999)
    assert msg.state == "pending" and m.next_tx(91_000) is None  # 回数を使い切った: 送らない
    m.poll(91_000)
    assert msg.state == "failed" and m.pending() == 0
    assert [w for w, _n, _s in log] == ["new", "tx", "tx", "state"]


def test_due_message_waits_without_counting():
    # 送れない間(スイッチがオフなど)は next_tx を呼ばない: 回数は増えず、ずっと待つ
    m, _ = make(retry_s=(30,))
    msg = m.send("Q1AAA", "x", 0)
    for t in (0, 100_000, 500_000):
        m.poll(t)
    assert msg.state == "pending" and msg.tries == 0
    assert _send_all(m, 500_000) == [b":Q1AAA    :x{1"] and msg.tries == 1


def test_ack_and_rej_match_sender_and_number():
    m, log = make()
    a = m.send("Q1AAA-9", "one", 0)
    b = m.send("Q1BBB", "two", 0)
    _send_all(m, 0)
    m.on_packet(P("Q1CCC", b":Q1TEST-7 :ack1"), 10)  # 別の局からの ack は無視
    m.on_packet(P("Q1AAA-9", b":Q1OTHER  :ack1"), 10)  # 宛先が自局でない
    assert a.state == "pending"
    m.on_packet(P("Q1AAA-9", b":q1test-7 :ack1"), 10)  # 宛先の大文字小文字は区別しない
    m.on_packet(P("Q1BBB", b":Q1TEST-7 :rej2"), 10)
    assert (a.state, b.state) == ("acked", "rejected") and m.pending() == 0
    assert m.next_tx(10**6) is None
    n = len(log)
    m.on_packet(P("Q1AAA-9", b":Q1TEST-7 :ack1"), 20)  # 2 回目の ack: 変化なし
    assert len(log) == n


def test_late_ack_after_failed_and_reply_ack():
    m, _ = make(retry_s=(30,))
    a = m.send("Q1AAA", "late", 0)
    b = m.send("Q1BBB", "reply", 0)
    _send_all(m, 0)
    m.poll(30_000)
    assert a.state == b.state == "failed"
    m.on_packet(P("Q1AAA", b":Q1TEST-7 :ack1"), 40_000)
    assert a.state == "acked"
    # reply-ack: 相手の新しいメッセージ {7} が、こちらの 2 への ack を兼ねる
    m.on_packet(P("Q1BBB", b":Q1TEST-7 :sure{7}2"), 40_000)
    assert b.state == "acked" and m.items[0].text == "sure" and m.unread == 1


def test_incoming_message_is_acked_and_deduplicated():
    m, log = make()
    p = P("Q1AAA-9", b":Q1TEST-7 :Hi there{42")
    m.on_packet(p, 0, UTC)
    assert m.unread == 1 and m.items[0].to_dict() == {
        "dir": "in", "no": "42", "text": "Hi there", "utc_s": UTC, "src": "Q1AAA-9"}
    assert log == [("rx", "42", None)]
    m.on_packet(p, 100)  # 相手の再送(こちらの ack が届いていない): ack は 1 つだけ待つ
    assert m.unread == 1 and len(m.items) == 1
    assert _send_all(m, 200) == [b":Q1AAA-9  :ack42"]
    m.on_packet(p, 300)  # 送ったあとにまた来た: もう一度 ack を返す。受信箱には入れない
    assert _send_all(m, 400) == [b":Q1AAA-9  :ack42"] and m.unread == 1
    m.mark_read()
    assert m.unread == 0



def test_duplicates_expire_and_need_the_same_text():
    # 相手が番号を 1 からやり直したとき、新しいメッセージを黙って捨てない
    m, _ = make()
    m.on_packet(P("Q1AAA-9", b":Q1TEST-7 :first{1"), 0)
    m.on_packet(P("Q1AAA-9", b":Q1TEST-7 :other text{1"), 1000)  # 番号が同じでも本文が違う
    assert len(m.items) == 2
    m.on_packet(P("Q1AAA-9", b":Q1TEST-7 :first{1"), SEEN_MS - 1)  # 期間内の再送: 重複
    assert len(m.items) == 2
    m.poll(SEEN_MS)  # 古いものを忘れる
    assert len(m._seen) == 1
    m.on_packet(P("Q1AAA-9", b":Q1TEST-7 :first{1"), SEEN_MS + 1)  # 期間の後: 新しいメッセージ
    assert len(m.items) == 3 and m.unread == 3


def test_message_without_number_is_not_acked():
    m, _ = make()
    m.on_packet(P("Q1AAA", b":Q1TEST-7 :no number"), 0)
    m.on_packet(P("Q1AAA", b":Q1TEST-7 :no number"), 10)  # 同じ本文は重複
    m.on_packet(P("Q1AAA", b":Q1OTHER  :not mine{1"), 10)
    m.on_packet(P("Q1AAA", b":BLN1     :bulletin"), 10)
    assert len(m.items) == 1 and m.items[0].no is None and m.next_tx(20) is None


def test_ack_that_cannot_be_sent_expires():
    m, _ = make(ack_wait_ms=60_000)
    m.on_packet(P("Q1AAA", b":Q1TEST-7 :x{1"), 0)
    m.poll(59_999)
    assert m.next_tx(59_999) is not None
    m.poll(60_000)
    assert m.next_tx(60_000) is None


def test_acks_go_before_messages():
    m, _ = make()
    m.send("Q1BBB", "mine", 0)
    m.on_packet(P("Q1AAA", b":Q1TEST-7 :x{9"), 0)
    assert _send_all(m, 0) == [b":Q1AAA    :ack9", b":Q1BBB    :mine{1"]


def test_send_checks_and_queue_limit():
    m, _ = make(pending_max=2)
    for args in (("Q1TEST-7", "x"), ("Q1AAA", ""), ("Q1AAA", "a{b"), ("bad call", "x")):
        try:
            m.send(args[0], args[1], 0)
            raise AssertionError("MessageError expected")
        except MessageError:
            pass
    m.send("Q1AAA", "1", 0)
    m.send("Q1AAA", "2", 0)
    try:
        m.send("Q1AAA", "3", 0)
        raise AssertionError("QueueFull expected")
    except QueueFull:
        pass
    m.cancel("1")
    assert m.items[1].state == "cancelled" and m.pending() == 1
    m.send("Q1AAA", "3", 0)  # 空いた
    c = m.cancel("1")
    assert m.cancel("99") is None and c is not None and c.state == "cancelled"


def test_numbers_wrap_and_list_keeps_pending():
    m, _ = make(keep=3, first_no=99999)
    a = m.send("Q1AAA", "a", 0)
    assert a.no == "99999" and m.send("Q1AAA", "b", 0).no == "1"
    for k in range(5):
        m.on_packet(P("Q1BBB", b":Q1TEST-7 :in %d{%d" % (k, k + 1)), 0)
    # 3 件まで。pending は消さない(古くても残る)
    assert len(m.items) == 3 and m.items[-1] is a and m.items[-2].text == "b"
    assert m.items[0].text == "in 4"


def test_set_call_changes_what_is_mine():
    m, _ = make()
    m.set_call("Q1TEST-9")
    m.on_packet(P("Q1AAA", b":Q1TEST-7 :old call{1"), 0)
    assert m.items == []
    m.on_packet(P("Q1AAA", b":Q1TEST-9 :new call{1"), 0)
    assert len(m.items) == 1


# ---- core.msgfile
def test_line_round_trip():
    m, _ = make()
    m.on_packet(P("Q1AAA", b":Q1TEST-7 :back\\slash \xe3\x81\x82{5"), 0, UTC)  # UTF-8 の本文
    out = m.send("Q1BBB-1", "two  spaces", 0)
    for x in (m.items[1], out):
        line = format_line(x)
        assert line.endswith(b"\n") and line.count(b"\n") == 1
        y = parse_line(line)
        assert (y.out, y.no, y.peer, y.text, y.state, y.tries, y.utc_s) == (
            x.out, x.no, x.peer, x.text, x.state, x.tries, x.utc_s)
    for bad in (b"", b"in 1\n", b"sideways 1 Q1AAA - 0 - x\n"):
        try:
            parse_line(bad)
            raise AssertionError("ValueError expected")
        except ValueError:
            pass


def test_file_save_and_restore():
    fs = FakeFs()
    m, _ = make()
    f = MsgFile("/sd/messages.txt", save_ms=5000, opener=fs.open, remove=fs.remove,
                rename=fs.rename)
    f.poll(m, 0)  # ファイルが無い: 読み出したことにする
    assert f.loaded and m.items == []
    m.send("Q1AAA", "acked one", 0)
    _send_all(m, 0)
    m.on_packet(P("Q1AAA", b":Q1TEST-7 :ack1"), 0)
    m.send("Q1BBB", "still pending", 0)
    m.on_packet(P("Q1CCC", b":Q1TEST-7 :hello{3"), 0, UTC)
    f.poll(m, 100)  # 読み出しの直後は書き直す
    assert b"next 3" in fs.files["/sd/messages.txt"]
    m.send("Q1BBB", "later", 200)
    f.poll(m, 300)  # 前回の保存から 5 秒たっていない
    assert b"later" not in fs.files["/sd/messages.txt"]
    f.close(m, 400)  # 終了の前は書く
    assert b"later" in fs.files["/sd/messages.txt"]
    # 次の起動
    m2, _ = make(first_no=500)
    f2 = MsgFile("/sd/messages.txt", opener=fs.open, remove=fs.remove, rename=fs.rename)
    f2.poll(m2, 0)
    assert [(x.out, x.peer, x.state) for x in m2.items] == [
        (True, "Q1BBB", "stopped"), (False, "Q1CCC", None), (True, "Q1BBB", "stopped"),
        (True, "Q1AAA", "acked")]
    assert m2.pending() == 0 and m2.next_tx(10**6) is None  # 前回の pending は送り直さない
    assert m2.send("Q1AAA", "x", 0).no == "4"  # 番号は続きから
    m2.on_packet(P("Q1CCC", b":Q1TEST-7 :hello{3"), 0)  # 前回受けたもの: 重複(ack は返す)
    item = m2.next_tx(0)
    assert m2.unread == 0 and item is not None and item[0] == b":Q1CCC    :ack3"
    m2.poll(SEEN_MS)  # 復元から SEEN_MS の後は覚えていない
    m2.on_packet(P("Q1CCC", b":Q1TEST-7 :hello{3"), SEEN_MS)
    assert m2.unread == 1


def test_restore_after_sending_keeps_higher_number():
    fs = FakeFs()
    fs.files["/sd/messages.txt"] = b"# pico2_aprs messages v1 next 3\nout 2 Q1AAA acked 1 - x\n"
    m, _ = make(first_no=700)
    m.send("Q1BBB", "before mount", 0)  # SD を読む前に送った(700)
    MsgFile("/sd/messages.txt", opener=fs.open, remove=fs.remove, rename=fs.rename).poll(m, 0)
    assert m.next_no == 701 and [x.no for x in m.items] == ["700", "2"]


def test_restore_ignores_a_broken_next_number():
    # messages.txt の next が壊れていても、番号は 1 から NO_MAX に収める
    for bad in (b"0", b"-5", b"100000", b"x"):
        fs = FakeFs()
        fs.files["/sd/messages.txt"] = b"# pico2_aprs messages v1 next " + bad + b"\n"
        m, _ = make()
        MsgFile("/sd/messages.txt", opener=fs.open, remove=fs.remove, rename=fs.rename).poll(m, 0)
        assert m.next_no == 1, bad
