"""スマホ(BLE)からのコマンドの処理。1 行 1 つの JSON を受けて、応答とイベントを作る。

プロトコルは docs/ble-control.md §5。BLE には依存しない(送受信は hw.ble、テストでは偽物)。
すべて局のループ(step())から呼ぶ。IRQ からは呼ばない。

    remote = Remote(settings, write, status_fn, stations_fn)
    remote.attach() / detach()   認証された接続ができた / 切れた(接続していない間は何も送らない)
    remote.handle(line)          受けた 1 行(\\n を除いた bytes)
    remote.rx(d) / remote.tx(d)  イベント(局から)
    remote.poll(now)             送り待ちを write に流す。定期の status と、変化したときの status

write(bytes) -> bool: 1 行(\\n 付き)を送る。送れない(送信のキューが満杯)なら False を返し、
remote が持ち続けて次の poll で送り直す。
イベント(rx / tx / 定期の status)の送り待ちは EVENT_MAX 行まで。溢れたら古い rx から捨て、
数を status の dropped に入れる。応答は捨てない(stations.list は送れた分だけ次を作る)。
"""

import json
import sys
import time

from core.logger import iso_utc
from core.settings import SettingsError, schema

PROTO = 1
STATUS_MS = 5000  # 定期の status の間隔
EVENT_MAX = 8  # イベントの送り待ちの上限(行)
LINE_MAX = 512  # 受ける 1 行の上限(バイト。hw.ble もこの値で切る)
# この項目が変わったら、定期を待たずに status を送る
CHANGE_KEYS = ("fix", "sw", "tx_ready", "tx_enabled", "call")


def printable(b):
    """bytes -> str。印字できる ASCII 以外のバイトは <0xHH>(TNC2 の情報欄用)"""
    out = []
    for c in b:
        out.append(chr(c) if 0x20 <= c <= 0x7E else "<0x%02X>" % c)
    return "".join(out)


_ROUND = {"lat": 5, "lon": 5, "lvl_db": 1}  # 小数の桁(JSON を短くする)


def _fields(out, d):
    """局からの dict を送る形にして out に足す: None は省く、utc_s は ISO 8601 の utc、
    tnc2 の bytes は printable() の str、座標と音量は丸める"""
    for k, v in d.items():
        if v is None:
            continue
        if k == "tnc2":
            out[k] = v if isinstance(v, str) else printable(v)
        elif k == "utc_s":
            out["utc"] = iso_utc(v)
        elif k in _ROUND:
            out[k] = round(v, _ROUND[k])
        else:
            out[k] = v
    return out


def encode(obj):
    s = json.dumps(obj)
    return s.encode() + b"\n"


class Remote:
    def __init__(self, settings, write, status_fn, stations_fn, mode="tnc", log=None):
        """settings: core.settings.Settings。status_fn(): status の中身の dict。
        stations_fn(): 局の表の要約(dict)を新しい順に返す iterable"""
        self.settings = settings
        self.write = write
        self.status_fn = status_fn
        self.stations_fn = stations_fn
        self.mode = mode
        self.log = log or (lambda *a: None)
        self.attached = False
        self.dropped = 0
        self._resp = []  # 応答の送り待ち(bytes)
        self._events = []  # イベントの送り待ち: (種類, bytes)
        self._iter = None  # stations.list の残り: (id, iterator, 送った数)
        self._status_ms = None
        self._last = None  # 前回送った status の CHANGE_KEYS の値

    # ---- 接続
    def attach(self):
        self.attached = True
        self.dropped = 0
        self._resp = []
        self._events = []
        self._iter = None
        self._status_ms = None  # 最初の poll で status を送る
        self._last = None

    def detach(self):
        self.attached = False
        self._resp = []
        self._events = []
        self._iter = None

    # ---- 受信
    def handle(self, line):
        """受けた 1 行を処理する(応答は送り待ちに積む。送るのは poll)"""
        if not self.attached:
            return
        try:
            req = json.loads(line)
        except (ValueError, TypeError):
            self._reply(None, "err", code="bad_json")
            return
        if not isinstance(req, dict):
            self._reply(None, "err", code="bad_json")
            return
        rid = req.get("id")
        if not (isinstance(rid, int) and not isinstance(rid, bool)):
            rid = None
        op = req.get("op")
        try:
            self._dispatch(rid, op, req)
        except SettingsError as e:
            self._reply(rid, "err", code="invalid", key=e.key, msg=e.msg)
        except OSError as e:  # フラッシュに書けない
            self._reply(rid, "err", code="io", msg=str(e))
        except Exception as e:  # 局は止めない
            sys.print_exception(e)
            self._reply(rid, "err", code="internal", msg="%s: %s" % (type(e).__name__, e))

    def too_long(self):
        """LINE_MAX を超えた行を捨てた(hw.ble から)"""
        if self.attached:
            self._reply(None, "err", code="too_long", msg="max %d bytes" % LINE_MAX)

    def _dispatch(self, rid, op, req):
        st = self.settings
        if op == "hello":
            self._reply(rid, "hello", proto=PROTO, name="pico2_aprs", mode=self.mode)
        elif op == "status":
            self._reply(rid, "status", **self._status())
        elif op == "settings.schema":
            self._reply(rid, "schema", items=schema())
        elif op == "settings.get":
            self._reply(rid, "settings", values=st.values())
        elif op == "settings.set":
            changed = st.set(req.get("values"))
            self.log("[remote] settings.set %s" % (", ".join(changed) or "(no change)"))
            self._reply(rid, "settings", values=st.values(), changed=changed)
        elif op == "settings.reset":
            changed = st.reset()
            self.log("[remote] settings.reset")
            self._reply(rid, "settings", values=st.values(), changed=changed)
        elif op == "stations.list":
            self._iter = (rid, iter(self.stations_fn()), 0)
        else:
            self._reply(rid, "err", code="unknown_op", msg=str(op)[:32])

    def _reply(self, rid, t, **kw):
        d = {"t": t}
        if rid is not None:
            d["id"] = rid
        d.update(kw)
        self._resp.append(encode(d))

    # ---- イベント
    def _event(self, kind, d):
        if not self.attached:
            return
        if len(self._events) >= EVENT_MAX:
            for i, (k, _b) in enumerate(self._events):  # 古い rx から捨てる
                if k == "rx":
                    del self._events[i]
                    break
            else:
                del self._events[0]
            self.dropped += 1
        self._events.append((kind, encode(d)))

    def rx(self, d):
        """受信したパケット。d: tnc2(bytes または str)、src、lvl_db、lat、lon、utc_s"""
        self._event("rx", self._packet("rx", d))

    def tx(self, d):
        """自局のビーコン(dry run を含む)。d: tnc2、dry、utc_s"""
        self._event("tx", self._packet("tx", d))

    def _packet(self, t, d):
        return _fields({"t": t}, d)

    def _status(self):
        d = _fields({}, self.status_fn())
        d["dropped"] = self.dropped
        return d

    # ---- 送信
    def poll(self, now):
        if not self.attached:
            return
        if self._status_ms is None or time.ticks_diff(now, self._status_ms) >= STATUS_MS:
            self._push_status(now)
        else:
            s = self._status()
            key = tuple(s.get(k) for k in CHANGE_KEYS)
            if key != self._last:
                self._push_status(now, s)
        self._flush()

    def _push_status(self, now, s=None):
        s = s if s is not None else self._status()
        self._last = tuple(s.get(k) for k in CHANGE_KEYS)
        self._status_ms = now
        s["t"] = "status"
        # status は 1 つあれば足りる: 送り待ちの古い status は捨てる
        self._events = [e for e in self._events if e[0] != "status"]
        self._event("status", s)

    def _flush(self):
        while True:
            if self._resp:
                if not self.write(self._resp[0]):
                    return
                self._resp.pop(0)
                continue
            if self._iter is not None:
                rid, it, n = self._iter
                try:
                    d = next(it)
                except StopIteration:
                    self._iter = None
                    self._reply(rid, "stations.end", n=n)
                    continue
                self._iter = (rid, it, n + 1)
                p = self._packet("station", d)
                if rid is not None:
                    p["id"] = rid
                self._resp.append(encode(p))
                continue
            if self._events:
                if not self.write(self._events[0][1]):
                    return
                self._events.pop(0)
                continue
            return
