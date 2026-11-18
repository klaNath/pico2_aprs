"""core.remote(スマホからのコマンドの処理)のテスト。PC と実機の両方で動く。
送受信は偽物(BLE を使わない)、設定のファイルも偽物。コールサインと座標はすべて架空の例。"""

import json

from test_settings import Cfg, FakeFs

from core import remote as R
from core.remote import Remote
from core.settings import Settings

UTC = 1_790_000_000  # 2026-09-21T14:13:20Z


class Link:
    """偽の送信側。full にすると write が False を返す(BTstack のキューが満杯)"""

    def __init__(self):
        self.lines = []
        self.full = False

    def write(self, b):
        if self.full:
            return False
        assert b.endswith(b"\n") and b.count(b"\n") == 1
        self.lines.append(json.loads(b))
        return True

    def take(self):
        out, self.lines = self.lines, []
        return out


class Src:
    def __init__(self):
        self.status = {"utc_s": UTC, "fix": 3, "sats": 9, "lat": 35.6812362, "lon": 139.7671248,
                       "sw": False, "tx_enabled": False, "tx_ready": False, "call": "Q1TEST-7",
                       "last_tx_s": None, "lvl_db": -18.04}
        self.stations = []

    def status_fn(self):
        return dict(self.status)

    def stations_fn(self):
        return list(self.stations)


def make():
    fs = FakeFs()
    cfg = Cfg()
    st = Settings(cfg, "/settings.json", opener=fs.open, remove=fs.remove, rename=fs.rename)
    link = Link()
    src = Src()
    r = Remote(st, link.write, src.status_fn, src.stations_fn)
    r.attach()
    r.poll(0)  # 接続直後の status
    first = link.take()
    assert [m["t"] for m in first] == ["status"]
    return r, link, src, cfg, fs


def req(r, link, now=1, **kw):
    r.handle(json.dumps(kw).encode())
    r.poll(now)
    return link.take()


def test_nothing_is_sent_before_attach():
    fs = FakeFs()
    st = Settings(Cfg(), "/s", opener=fs.open, remove=fs.remove, rename=fs.rename)
    link = Link()
    src = Src()
    r = Remote(st, link.write, src.status_fn, src.stations_fn)
    r.handle(b'{"op":"hello","id":1}')
    r.rx({"tnc2": b"Q1AAA>APRS:>hi"})
    r.poll(0)
    assert link.lines == []


def test_hello_and_status():
    r, link, _src, _cfg, _fs = make()
    (m,) = req(r, link, op="hello", id=1)
    assert m == {"t": "hello", "id": 1, "proto": R.PROTO, "name": "pico2_aprs", "mode": "tnc"}
    (m,) = req(r, link, op="status", id=2)
    assert m["t"] == "status" and m["id"] == 2
    assert m["utc"] == "2026-09-21T14:13:20Z" and m["lat"] == 35.68124 and m["lvl_db"] == -18.0
    assert "last_tx_s" not in m  # None は省く
    assert m["dropped"] == 0


def test_settings_schema_get_set_reset():
    r, link, _src, cfg, fs = make()
    (m,) = req(r, link, op="settings.schema", id=1)
    assert m["t"] == "schema" and len(m["items"]) == len(R.schema())
    (m,) = req(r, link, op="settings.get", id=2)
    assert m["values"]["ssid"] == 7 and m["values"]["symbol"] == "/["
    (m,) = req(r, link, op="settings.set", id=3, values={"ssid": 9, "comment": "hello"})
    assert m["t"] == "settings" and m["id"] == 3 and m["values"]["ssid"] == 9
    assert sorted(m["changed"]) == ["comment", "ssid"]
    assert cfg.SSID == 9 and "/settings.json" in fs.files
    (m,) = req(r, link, op="settings.reset", id=4)
    assert m["values"]["ssid"] == 7 and cfg.SSID == 7 and fs.files == {}


def test_invalid_setting_changes_nothing():
    r, link, _src, cfg, fs = make()
    (m,) = req(r, link, op="settings.set", id=5, values={"ssid": 3, "beacon_still_s": 1})
    assert m["t"] == "err" and m["id"] == 5 and m["code"] == "invalid"
    assert m["key"] == "beacon_still_s" and m["msg"]
    assert cfg.SSID == 7 and fs.files == {}
    (m,) = req(r, link, op="settings.set", id=6)  # values が無い
    assert m["code"] == "invalid" and m["key"] == "values"


def test_io_error_is_reported():
    r, link, _src, cfg, fs = make()
    fs.fail = True
    (m,) = req(r, link, op="settings.set", id=7, values={"ssid": 3})
    assert m["t"] == "err" and m["code"] == "io" and cfg.SSID == 7


def test_bad_requests():
    r, link, _src, _cfg, _fs = make()
    r.handle(b"{not json")
    r.handle(b"[1, 2]")
    r.handle(b'{"op": "format.flash", "id": 9}')
    r.too_long()
    r.poll(1)
    ms = link.take()
    assert [m["code"] for m in ms] == ["bad_json", "bad_json", "unknown_op", "too_long"]
    assert "id" not in ms[0] and ms[2]["id"] == 9


def test_internal_error_does_not_raise():
    r, link, src, _cfg, _fs = make()

    def boom():
        raise RuntimeError("x")

    r.status_fn = boom
    r.handle(b'{"op": "status", "id": 1}')  # 例外は応答にして、局のループへは出さない
    r.status_fn = src.status_fn
    r.poll(1)
    (m,) = link.take()
    assert m["t"] == "err" and m["code"] == "internal" and m["id"] == 1


def test_rx_and_tx_events():
    r, link, _src, _cfg, _fs = make()
    r.rx({"tnc2": b"Q1AAA-9>APRS,WIDE1-1:>hi\x1c\xff", "src": "Q1AAA-9", "lvl_db": -20.06,
          "lat": 35.0, "lon": None, "utc_s": UTC})
    r.tx({"tnc2": b"Q1TEST-7>APRS:x", "dry": True, "utc_s": None})
    r.poll(1)
    a, b = link.take()
    assert a == {"t": "rx", "tnc2": "Q1AAA-9>APRS,WIDE1-1:>hi<0x1C><0xFF>", "src": "Q1AAA-9",
                 "lvl_db": -20.1, "lat": 35.0, "utc": "2026-09-21T14:13:20Z"}
    assert b == {"t": "tx", "tnc2": "Q1TEST-7>APRS:x", "dry": True}


def test_status_on_change_and_periodically():
    r, link, src, _cfg, _fs = make()
    r.poll(1000)
    assert link.take() == []  # 変化なし、5 秒たっていない
    src.status["sw"] = True  # ビーコンスイッチを入れた
    r.poll(1500)
    (m,) = link.take()
    assert m["t"] == "status" and m["sw"] is True and "id" not in m
    src.status["lvl_db"] = -3.0  # 音量の変化では送らない
    r.poll(2000)
    assert link.take() == []
    r.poll(1500 + R.STATUS_MS)
    (m,) = link.take()
    assert m["lvl_db"] == -3.0


def test_full_link_keeps_lines_and_drops_old_rx():
    r, link, _src, _cfg, _fs = make()
    link.full = True
    r.handle(b'{"op": "hello", "id": 1}')
    for i in range(R.EVENT_MAX + 3):
        r.rx({"tnc2": b"Q1AAA>APRS:%d" % i})
    r.poll(1)
    assert link.lines == []
    link.full = False
    r.poll(2)
    ms = link.take()
    assert ms[0]["t"] == "hello"  # 応答は捨てない
    rx = [m["tnc2"] for m in ms if m["t"] == "rx"]
    assert len(rx) == R.EVENT_MAX and rx[0] == "Q1AAA>APRS:3" and rx[-1].endswith(":10")
    (m,) = req(r, link, op="status", id=2)
    assert m["dropped"] == 3


def test_stations_list_streams_and_survives_full_link():
    r, link, src, _cfg, _fs = make()
    src.stations = [{"src": "Q1AA%d" % i, "last_s": i * 10, "count": 1, "lat": 35.0 + i,
                     "lon": 139.0, "lvl_db": None} for i in range(5)]
    r.handle(b'{"op": "stations.list", "id": 4}')
    sent = [0]
    w = link.write

    def flaky(b):  # 2 行ごとに満杯になる
        sent[0] += 1
        return False if sent[0] % 3 == 0 else w(b)

    r.write = flaky
    for i in range(10):
        r.poll(1 + i)
    ms = link.take()
    assert [m["t"] for m in ms] == ["station"] * 5 + ["stations.end"]
    assert [m["src"] for m in ms[:5]] == ["Q1AA0", "Q1AA1", "Q1AA2", "Q1AA3", "Q1AA4"]
    assert all(m["id"] == 4 for m in ms) and ms[-1]["n"] == 5
    assert "lvl_db" not in ms[0]


def test_detach_clears_queues():
    r, link, _src, _cfg, _fs = make()
    link.full = True
    r.handle(b'{"op": "hello", "id": 1}')
    r.rx({"tnc2": b"Q1AAA>APRS:x"})
    r.detach()
    link.full = False
    r.poll(1)
    assert link.lines == []
    r.attach()
    r.poll(2)
    assert [m["t"] for m in link.take()] == ["status"]


def test_disconnect_inside_write_does_not_raise():
    """送っている途中(write の中)で切断の IRQ が来て detach() されても、例外にしない"""
    for op in ("hello", None):  # 応答の送り待ち / イベント(status)の送り待ち
        r, link, _src, _cfg, _fs = make()

        def write(b, r=r, link=link):
            link.lines.append(json.loads(b))
            r.detach()  # IRQ が write の中で走った
            return True

        r.write = write
        if op:
            r.handle(b'{"op": "hello", "id": 1}')
        r.poll(R.STATUS_MS + 1)  # status も送り待ちにする
        assert len(link.take()) == 1 and not r.attached


def test_printable():
    assert R.printable(b"ab\x00~\x7f") == "ab<0x00>~<0x7F>"


class Sender:
    """偽の send_fn(Station.send_now の代わり)。result を返し、呼ばれた数を数える"""

    def __init__(self, result):
        self.result = result
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.result


def test_beacon_send_without_send_fn_is_unknown():
    r, link, _src, _cfg, _fs = make()  # send_fn を渡していない(既存の呼び出し)
    (m,) = req(r, link, op="beacon.send", id=3)
    assert m["t"] == "err" and m["code"] == "unknown_op" and m["id"] == 3


def test_beacon_send_reply_then_tx_event():
    r, link, _src, _cfg, _fs = make()
    sent = []

    def send():  # 本物と同じく、送り終えてから戻る(その途中で tx イベントを積む)
        sent.append(1)
        r.tx({"tnc2": b"Q1TEST-7>APRS:x", "dry": False})
        return {"dry": False}

    r.send_fn = send
    ms = req(r, link, op="beacon.send", id=4)
    assert sent == [1]
    assert [m["t"] for m in ms] == ["beacon", "tx"]  # 応答が先(応答は送り待ちの先頭から出る)
    assert ms[0] == {"t": "beacon", "id": 4, "dry": False}


def test_beacon_send_dry_run():
    r, link, _src, _cfg, _fs = make()
    r.send_fn = Sender({"dry": True})
    (m,) = req(r, link, op="beacon.send", id=5)
    assert m == {"t": "beacon", "id": 5, "dry": True}


def test_beacon_send_refusals():
    r, link, _src, _cfg, _fs = make()
    for res, extra in ((("switch_off", "beacon switch is off", None), {}),
                       (("no_fix", "no position fix or time yet", None), {}),
                       (("too_soon", "wait 4 s", {"wait_s": 4}), {"wait_s": 4}),
                       (("frame_too_long", "x", None), {})):
        r.send_fn = s = Sender(res)
        (m,) = req(r, link, op="beacon.send", id=6)
        assert s.calls == 1
        assert m == dict({"t": "err", "id": 6, "code": res[0], "msg": res[1]}, **extra)


def test_status_change_on_beacon_mode():
    r, link, src, _cfg, _fs = make()
    src.status["beacon_mode"] = "manual"
    r.poll(1000)
    (m,) = link.take()
    assert m["t"] == "status" and m["beacon_mode"] == "manual"
