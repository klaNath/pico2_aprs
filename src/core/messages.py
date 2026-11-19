"""APRS メッセージの送受信の管理: 送信待ちの列と再送、ack、受信箱。ハードウェアに依存しない。

仕様は docs/ble-protocol.md §7.6-§7.8, §8.8。局(station)の使い方:
    msgs = Messages("Q1TEST-7", on_change=...)   (コールサインは架空の例)
    msgs.send(to, text, now, utc_s)     スマホからの msg.send。積むだけ(MessageError / QueueFull)
    msgs.on_packet(p, now, utc_s)       受信したパケット(aprs.packet.Packet)
    msgs.poll(now)                      期限切れの処理(ループで毎回)
    item = msgs.next_tx(now)            今送るもの (info, key) / None。ack が先、次に期限の来た再送
    msgs.sent(key, now)                 送った(送れる条件と送信は局が受け持つ)
on_change(m, why): 項目が変わった。why は "new"(積んだ)/ "tx"(送った)/ "state"(状態が変わった)/
"rx"(自局宛を受けた)。局は BLE のイベント、LCD、SD の保存に使う。
"""

import time

from aprs.message import MessageError, ack_info, check_no, check_text, check_to, info

RETRY_S = (30, 60, 120, 240, 240)  # k 回目を送ってから次(最後は failed)までの秒。長さ = 最大の回数
PENDING_MAX = 4
KEEP = 16  # 覚えておく件数(受信と送信を合わせて。pending は消さない)
ACK_WAIT_MS = 60_000  # 送れない ack はこれだけたったら捨てる
SEEN_MAX = 32  # 重複の判定に覚えておく受信の数
NO_MAX = 99999

PENDING = "pending"
ENDED = ("acked", "rejected", "failed", "cancelled", "stopped")


class QueueFull(Exception):
    pass


class Msg:
    """1 通。out: 送る側(スマホから)なら True。peer: 送る側は宛先、受けた側は送信局"""

    __slots__ = ("out", "no", "peer", "text", "state", "tries", "utc_s", "next_ms")

    def __init__(self, out, no, peer, text, utc_s=None, state=None, tries=0):
        self.out = out
        self.no: "str | None" = no
        self.peer = peer
        self.text = text
        self.state: "str | None" = state
        self.tries = tries
        self.utc_s: "int | None" = utc_s
        self.next_ms = 0  # 送る側: 次に送る(最後の回のあとは failed にする)ticks_ms

    def to_dict(self):
        """BLE の msg(§8.8)の中身。None の項目は core.remote が省く"""
        d = {"dir": "out" if self.out else "in", "no": self.no, "text": self.text,
             "utc_s": self.utc_s}
        if self.out:
            d["to"] = self.peer
            d["state"] = self.state
            d["tries"] = self.tries
        else:
            d["src"] = self.peer
        return d

    def __repr__(self):
        return "<Msg %s %s %s %s>" % ("out" if self.out else "in", self.no, self.peer, self.state)


class Messages:
    def __init__(self, own_call, retry_s=RETRY_S, pending_max=PENDING_MAX, keep=KEEP,
                 ack_wait_ms=ACK_WAIT_MS, first_no=1, on_change=None):
        self.own = own_call.upper()
        self.retry_s = retry_s
        self.pending_max = pending_max
        self.keep = keep
        self.ack_wait_ms = ack_wait_ms
        self.next_no = first_no
        self.on_change = on_change or (lambda m, why: None)
        self.items = []  # Msg、新しい順
        self.unread = 0  # 自局宛を受けた数(mark_read() まで)
        self.version = 0  # 項目が変わるたびに増える(SD の保存の判定)
        self._acks = []  # 送る ack: [宛先, 番号, 捨てる ticks_ms]
        self._seen = []  # 受けたメッセージの (送信局, 番号 または 本文)

    def set_call(self, own_call):
        self.own = own_call.upper()

    def pending(self):
        return sum(1 for m in self.items if m.out and m.state == PENDING)

    def mark_read(self):
        self.unread = 0

    # ---- 送る側
    def send(self, to, text, now, utc_s=None):
        """メッセージを積む。値が不正なら MessageError、満杯なら QueueFull。積んだ Msg を返す"""
        to = check_to(to)
        text = check_text(text)
        if to == self.own:
            raise MessageError("to", "own callsign")
        if self.pending() >= self.pending_max:
            raise QueueFull()
        m = Msg(True, self._new_no(), to, text, utc_s, PENDING)
        m.next_ms = now
        self._add(m)
        self._changed(m, "new")
        return m

    def cancel(self, no):
        """再送をやめる。終わっていたらそのまま返す。無ければ None"""
        no = check_no(no)
        for m in self.items:
            if m.out and m.no == no:
                if m.state == PENDING:
                    m.state = "cancelled"
                    self._changed(m, "state")
                return m
        return None

    def _new_no(self):
        no = self.next_no
        self.next_no = no % NO_MAX + 1
        return str(no)

    # ---- 受信
    def on_packet(self, p, now, utc_s=None):
        """受信したパケット。自局宛のメッセージ・ack・rej だけを扱う"""
        if p.kind != "message" or p.addressee is None or p.addressee.upper() != self.own:
            return
        src = p.src.upper()
        if p.response is not None:
            self._answered(src, p.msgno, p.response == "ack")
            return
        if p.replyack:
            self._answered(src, p.replyack, True)
        if p.msgno:
            try:
                no = check_no(p.msgno)
            except MessageError:
                no = None  # 番号の形が不正: ack を作れない
            if no is not None and not any(a[0] == src and a[1] == no for a in self._acks):
                self._acks.append([src, no, time.ticks_add(now, self.ack_wait_ms)])
        key = (src, p.msgno if p.msgno else p.text)
        if key in self._seen:  # 相手の再送(こちらの ack が届かなかった)。ack は上で積んだ
            return
        self._seen.append(key)
        if len(self._seen) > SEEN_MAX:
            self._seen.pop(0)
        m = Msg(False, p.msgno or None, src, p.text or "", utc_s)
        self.unread += 1
        self._add(m)
        self._changed(m, "rx")

    def _answered(self, src, no, ok):
        for m in self.items:
            if m.out and m.peer == src and m.no == no:
                if m.state == PENDING or m.state == "failed":
                    m.state = "acked" if ok else "rejected"
                    self._changed(m, "state")
                return

    # ---- 送るものの取り出し
    def poll(self, now):
        """送れなかった ack を捨て、最後の回のあと待っても ack が来なければ failed にする"""
        self._acks = [a for a in self._acks if time.ticks_diff(now, a[2]) < 0]
        for m in self.items:
            if not (m.out and m.state == PENDING) or time.ticks_diff(now, m.next_ms) < 0:
                continue
            if m.tries >= len(self.retry_s):
                m.state = "failed"
                self._changed(m, "state")
            else:
                m.next_ms = now  # 送る時刻を過ぎて待っている(ticks_ms の一周で未来に見えないように)

    def next_tx(self, now):
        """今送るもの (情報フィールド bytes, key) / None。ack を先に、次に古いメッセージから"""
        if self._acks:
            a = self._acks[0]
            return ack_info(a[0], a[1]), a
        for m in reversed(self.items):
            if (m.out and m.state == PENDING and m.tries < len(self.retry_s)
                    and time.ticks_diff(now, m.next_ms) >= 0):
                return info(m.peer, m.text, m.no), m
        return None

    def sent(self, key, now):
        """next_tx() の key を送った"""
        if isinstance(key, Msg):
            key.tries += 1
            key.next_ms = time.ticks_add(now, self.retry_s[key.tries - 1] * 1000)
            self._changed(key, "tx")
        elif key in self._acks:
            self._acks.remove(key)

    # ---- 保存からの復元(core.msgfile)
    def restore(self, olds, next_no):
        """前回の起動の項目(新しい順)を今の項目の後ろに足す。pending だったものは stopped。
        番号は、この起動でまだ送っていなければ前回の続きから"""
        if not any(m.out for m in self.items) or next_no > self.next_no:
            self.next_no = next_no
        for m in olds:
            if m.out and m.state == PENDING:
                m.state = "stopped"
            self.items.append(m)
            if not m.out:
                self._seen.append((m.peer, m.no if m.no else m.text))
        self._seen = self._seen[-SEEN_MAX:]
        self._trim()
        self.version += 1

    # ---- 内部
    def _add(self, m):
        self.items.insert(0, m)
        self._trim()

    def _trim(self):
        i = len(self.items) - 1
        while len(self.items) > self.keep and i >= 0:
            m = self.items[i]
            if not (m.out and m.state == PENDING):
                del self.items[i]
            i -= 1

    def _changed(self, m, why):
        self.version += 1
        self.on_change(m, why)
