"""BLE の層: 広告、Nordic UART Service(NUS)、ペアリング(パスキー)、行の組み立てと分割。

仕様は docs/ble-protocol.md §2〜§5。メッセージの中身は core.remote が処理する(この層は知らない)。
BLE の部品(bluetooth.BLE)とファイル操作は引数で差し替えられるので、PC でもテストできる。

    ble = Ble(remote, on_passkey=show_on_lcd)
    ble.start()          広告を始める
    ble.poll(now)        局のループ(step())から呼ぶ。受けた行を remote に渡し、送り待ちを流す
    ble.stop()

- IRQ(bluetooth.irq のハンドラ)では解析や応答はしない。受けたバイトを行の組み立て用の
  バッファに足して、完成した行を持つだけ。処理は poll() で行う
  (局のループは同期的で、ウォッチドッグが 3 秒)
- 認証された接続(暗号化 + MITM のパスキー)になるまで、書き込まれたバイトは捨て、通知も送らない
- つながってから AUTH_TIMEOUT_MS たっても認証されなければ、本機から切る(接続中は広告を止める
  ので、放っておかれた接続があるとほかの端末から見つからない)。切断のイベントで広告を再開する
- 本機の入出力能力は DisplayOnly。パスキー(6 桁)は BTstack が作り、IRQ_PASSKEY_ACTION の
  3 つ目の値で届く。それを on_passkey(passkey) に渡す(LCD に出す)。終わったら on_passkey(None)
- ボンディングの鍵は bonds_path(JSON)に置く。ペアリングは稀なので、変わったときだけ書く
  (フラッシュへの書き込みは受信を止めるので、poll() の中で、通信がない間に 1 回だけ)

ファームウェアの前提: MicroPython 1.29 の BTstack 版はペアリングの要求を処理しない。
hw/micropython/btstack-pairing.patch を当てたファームウェアが必要(hw/micropython/README.md)。
パッチ済みの BTstack は、ボンドの鍵と ER/IR を IRQ_GET_SECRET/IRQ_SET_SECRET(種類 0x10、
key はタグ 4 バイト)で読み書きする。既存のボンドを読むので、irq() は active(True) より前に結ぶ。
"""

import binascii
import json
import os
import time

import bluetooth  # pyright: ignore[reportMissingImports]
from micropython import const

from core import remote as _remote

try:
    from typing import Any  # noqa: F401  (型注釈用。MicroPython には無い)
except ImportError:
    pass

NUS = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
NUS_RX = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"  # アプリ -> 本機(Write)
NUS_TX = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"  # 本機 -> アプリ(Notify)

# bluetooth.irq のイベント(MicroPython のドキュメントの番号)
IRQ_CENTRAL_CONNECT = const(1)
IRQ_CENTRAL_DISCONNECT = const(2)
IRQ_GATTS_WRITE = const(3)
IRQ_MTU_EXCHANGED = const(21)
IRQ_ENCRYPTION_UPDATE = const(28)
IRQ_GET_SECRET = const(29)
IRQ_SET_SECRET = const(30)
IRQ_PASSKEY_ACTION = const(31)

PASSKEY_ACTION_NONE = const(0)
PASSKEY_ACTION_DISPLAY = const(3)
IO_DISPLAY_ONLY = const(0)

# 特性のフラグ(aioble と同じ値)
FLAG_WRITE_NO_RESPONSE = const(0x0004)
FLAG_WRITE = const(0x0008)
FLAG_NOTIFY = const(0x0010)
FLAG_WRITE_AUTHENTICATED = const(0x2000)  # 暗号化 + MITM のペアリングが済んだ接続だけ書ける

LINE_MAX = _remote.LINE_MAX  # 受ける 1 行の上限(バイト)
ATT_OVERHEAD = const(3)
DEFAULT_MTU = const(23)
MTU_WANTED = const(517)
RX_BUF = const(514)  # RX 特性のバッファ = 1 回の書き込みの上限(MTU_WANTED - ATT_OVERHEAD)
KEY_SIZE_MIN = const(16)  # 暗号化の鍵長(バイト)。LE Secure Connections は 16。短い鍵は認証しない
SAVE_RETRY_MS = const(30_000)  # 鍵をフラッシュに書けなかったときに、やり直すまで
# 1 回の通知の上限。リンク層の 1 パケット(データ長拡張 251 - L2CAP 4 - ATT 3)。
# MTU 517 で 514 バイトの通知を続けて送ると、Windows の GATT が止まった(2026-11-17。
# 応答が来なくなり、Bluetooth を入れ直すまで、つなぎ直しても通知の購読が Unreachable になる)
NOTIFY_MAX = const(244)
ADV_INTERVAL_US = const(100_000)
LINES_MAX = const(4)  # 処理待ちの行の上限(溢れたら、その行は捨てて too_long の代わりに busy)
MAX_SECRETS = const(24)  # 保存する鍵の数(1 台につき数個。4 台分)
BONDS_PATH = "/ble_bonds.json"
SAVE_DELAY_MS = const(2000)  # 鍵が変わってから書くまで(続けて変わるのをまとめる)
PASSKEY_MAX = const(1_000_000)
AUTH_TIMEOUT_MS = const(60_000)  # つながってから認証されるまでの上限(パスキーを打つ時間を含む)
# ペアリングに失敗したら切断する。続けて PAIR_FAIL_MAX 回失敗したら PAIR_HOLD_MS は広告しない
# (1 つの接続でペアリングを何度もやり直させると、そのたびにパスキーの全画面表示と P-256 の計算で
# 局が止まる。docs/ble-protocol.md §4)
PAIR_FAIL_MAX = const(3)
PAIR_HOLD_MS = const(60_000)
SECRET_TYPE_TLV = const(0x10)  # パッチ済み BTstack の TLV(key はタグ 4 バイト)
KEEP_TAGS = (b"SMER", b"SMIR")  # forget_all で残す(本機の ER/IR。ボンドではない)


def adv_payload(name=None, uuid_bytes=None):
    """広告データ(AD 構造の並び)。name があれば Complete Local Name、uuid_bytes があれば
    128 ビットのサービス UUID(リトルエンディアン)"""
    out = bytearray()
    if name is None:
        out += b"\x02\x01\x06"  # Flags: LE General Discoverable, BR/EDR not supported
    else:
        n = name.encode()
        out += bytes((len(n) + 1, 0x09)) + n
    if uuid_bytes is not None:
        out += bytes((len(uuid_bytes) + 1, 0x07)) + uuid_bytes
    return bytes(out)


def uuid_le(text):
    """'6E400001-...' -> 16 バイト(リトルエンディアン)"""
    h = text.replace("-", "")
    b = bytes(int(h[i:i + 2], 16) for i in range(0, 32, 2))
    return bytes(reversed(b))


class LineBuffer:
    """バイト列を '\\n' で区切って行にする。LINE_MAX を超えた行は捨てる(too_long に数える)"""

    def __init__(self, line_max=LINE_MAX, lines_max=LINES_MAX):
        self.line_max = line_max
        self.lines_max = lines_max
        self.buf = bytearray()
        self.lines = []
        self.skipping = False  # 長すぎた行の残りを読み捨てている
        self.too_long = 0  # 捨てた長すぎる行の数(poll で報告して 0 に戻す)
        self.busy = 0  # 処理待ちが満杯で捨てた行の数

    def feed(self, data):
        for c in data:
            if c == 10:  # '\n'
                if self.skipping:
                    self.skipping = False
                elif self.buf:
                    if len(self.lines) < self.lines_max:
                        self.lines.append(bytes(self.buf))
                    else:
                        self.busy += 1
                self.buf = bytearray()
            elif not self.skipping:
                if len(self.buf) >= self.line_max:
                    self.skipping = True
                    self.buf = bytearray()
                    self.too_long += 1
                elif c != 13:  # '\r' は無視(端末から試すとき)
                    self.buf.append(c)

    def clear(self):
        self.buf = bytearray()
        self.lines = []
        self.skipping = False
        self.too_long = 0
        self.busy = 0


class Ble:
    def __init__(self, remote, on_passkey=None, bonds_path=BONDS_PATH, ble=None, opener=open,
                 remove=os.remove, rename=os.rename, log=None):
        """remote: core.remote.Remote(write は start() で結ぶ)。on_passkey(passkey または None):
        LCD にパスキーを出す/消す。ble: bluetooth.BLE のようなもの(試験用)"""
        self.remote = remote
        self.on_passkey = on_passkey
        self.bonds_path = bonds_path
        self.tmp = bonds_path + ".tmp"
        self._ble: "Any" = ble
        self._open, self._remove, self._rename = opener, remove, rename
        self.log = log or (lambda *a: None)
        self.name = None
        self.conn = None  # 接続中のハンドル
        self.connected_ms: "Any" = 0  # 接続した時刻(ticks_ms。スタブでは専用の型)
        self._kicked = False  # 認証の時間切れで切断を要求した(切断のイベントを待っている)
        self._pair_failed = False  # この接続でペアリングに失敗した(IRQ が印を付け、poll が切断する)
        self.pair_fails = 0  # 続けて失敗した回数(認証できたら 0)
        self._adv_hold_ms = None  # この ticks_ms まで広告しない / None
        self.authenticated = False
        self.mtu = DEFAULT_MTU
        self.key_size = 0  # 最後の暗号化の更新の鍵長(バイト)
        self.passkey = None
        self.lines = LineBuffer()
        self._todo = []  # 取り出したがまだ渡していない行(一覧の応答の途中は待たせる)
        self.dropped_writes = 0  # 認証前に書かれて捨てたバイト数
        self.secrets = []  # [(種類, key, value)]
        self._secrets_dirty_ms = None
        self._secrets_gen = 0  # 鍵が変わるたびに増やす(保存中の変更を取りこぼさない)
        self.irq_errors = 0  # IRQ の中で起きて握りつぶした例外の数
        self._out = []  # 送り残しのかたまり(bytes)
        self._h_tx = None
        self._h_rx = None
        self.running = False

    # ---- 起動と停止
    def start(self):
        b = self._ble
        if b is None:
            b = self._ble = bluetooth.BLE()
        self._load_secrets()
        b.irq(self._irq)  # active(True) の中で BTstack がボンドと ER/IR を読むので、先に結ぶ
        b.active(True)
        b.config(gap_name="PicoAPRS", mtu=MTU_WANTED)
        b.config(bond=True, mitm=True, le_secure=True, io=IO_DISPLAY_ONLY)
        rx = (bluetooth.UUID(NUS_RX),
              FLAG_WRITE | FLAG_WRITE_NO_RESPONSE | FLAG_WRITE_AUTHENTICATED)
        tx = (bluetooth.UUID(NUS_TX), FLAG_NOTIFY)
        ((self._h_tx, self._h_rx),) = b.gatts_register_services(((bluetooth.UUID(NUS), (tx, rx)),))
        b.gatts_set_buffer(self._h_rx, RX_BUF)
        mac = b.config("mac")[1]
        self.name = "PicoAPRS-%02X%02X" % (mac[4], mac[5])
        self.remote.write = self.write
        self.running = True
        self._adv_hold_ms = None
        self.pair_fails = 0
        self._advertise()
        self.log("[ble] advertising as %s" % self.name)

    def stop(self):
        was_running = self.running
        self.running = False  # 止める途中の切断イベントで広告を再開しない
        if self.conn is not None:
            try:
                self._ble.gap_disconnect(self.conn)
            except OSError:
                pass
        self._set_connected(None)
        if self._ble is not None and was_running:
            self._ble.gap_advertise(None)
            self._ble.active(False)
        self._save_secrets(force=True)

    def _advertise(self):
        self._ble.gap_advertise(
            ADV_INTERVAL_US, adv_data=adv_payload(None, uuid_le(NUS)),
            resp_data=adv_payload(self.name))

    # ---- IRQ(解析や応答はしない)
    def _irq(self, event, data):
        # ハンドラが例外を出すと、MicroPython はハンドラを外してしまう(以後ボンドの保存も
        # 接続もできない)。ここで止める
        try:
            return self._irq_body(event, data)
        except Exception as e:  # noqa: BLE001
            self.irq_errors += 1
            try:
                self.log("[ble] irq %d error: %r" % (event, e))
            except Exception:  # noqa: BLE001
                pass
            return None

    def _irq_body(self, event, data):
        if event == IRQ_CENTRAL_CONNECT:
            self._set_connected(data[0])
        elif event == IRQ_CENTRAL_DISCONNECT:
            self._set_connected(None)
            if self.running and self._adv_hold_ms is None:
                self._advertise()
        elif event == IRQ_GATTS_WRITE:
            conn, handle = data[0], data[1]
            if handle == self._h_rx and conn == self.conn:
                chunk = self._ble.gatts_read(self._h_rx)
                if self.authenticated:
                    self.lines.feed(chunk)
                else:
                    self.dropped_writes += len(chunk)  # 認証前: 黙って捨てる
        elif event == IRQ_MTU_EXCHANGED:
            if data[0] == self.conn:
                self.mtu = data[1]
        elif event == IRQ_ENCRYPTION_UPDATE:
            conn, encrypted, authenticated, key_size = data[0], data[1], data[2], data[4]
            if conn == self.conn:
                self.key_size = key_size
                ok = bool(encrypted and authenticated) and key_size >= KEY_SIZE_MIN
                self._set_authenticated(ok)
                if not ok:
                    self._clear_passkey()  # パスキーの入力違いなどで失敗した
                    self._pair_failed = True
        elif event == IRQ_PASSKEY_ACTION:
            conn, action = data[0], data[1]
            if action == PASSKEY_ACTION_DISPLAY and conn == self.conn:
                self.passkey = data[2] % PASSKEY_MAX  # BTstack が作った値(パッチ済みファームウェア)
                self._call_on_passkey(self.passkey)
            elif action == PASSKEY_ACTION_NONE:
                self._clear_passkey()  # ペアリングが取り消された・失敗した
                if conn == self.conn and not self.authenticated:
                    self._pair_failed = True
        elif event == IRQ_GET_SECRET:
            return self._get_secret(data[0], data[1], data[2])
        elif event == IRQ_SET_SECRET:
            return self._set_secret(data[0], data[1], data[2])
        return None

    def _set_connected(self, conn):
        was = self.conn
        self.conn = conn
        self.connected_ms = time.ticks_ms()
        self._kicked = False
        self._pair_failed = False
        self.mtu = DEFAULT_MTU
        self.lines.clear()
        self._todo = []
        self._out = []
        self._set_authenticated(False)
        self._clear_passkey()  # ペアリングの途中で切れたときも、LCD のパスキーを消す
        if conn is None:
            if was is not None:
                self.log("[ble] disconnected")
        else:
            self.log("[ble] connected")

    def _set_authenticated(self, ok):
        if ok == self.authenticated:
            return
        self.authenticated = ok
        if ok:
            self.pair_fails = 0
            self._clear_passkey()
            self.lines.clear()
            self.remote.attach()
            self.log("[ble] authenticated (key %d)" % self.key_size)
        else:
            self.remote.detach()

    def _clear_passkey(self):
        if self.passkey is not None:
            self.passkey = None
            self._call_on_passkey(None)

    def _call_on_passkey(self, passkey):
        """LCD の描画などで例外が出ても、BLE の状態の更新は続ける"""
        if self.on_passkey is None:
            return
        try:
            self.on_passkey(passkey)
        except Exception as e:  # noqa: BLE001
            self.irq_errors += 1
            self.log("[ble] on_passkey error: %r" % (e,))

    # ---- 鍵(ボンディング)
    def _get_secret(self, sec_type, index, key):
        if key is None:
            i = 0
            for t, _k, v in self.secrets:
                if t == sec_type:
                    if i == index:
                        return v
                    i += 1
            return None
        key = bytes(key)
        for t, k, v in self.secrets:
            if t == sec_type and k == key:
                return v
        return None

    def _set_secret(self, sec_type, key, value):
        key = bytes(key)
        for i, (t, k, _v) in enumerate(self.secrets):
            if t == sec_type and k == key:
                if value is None:
                    del self.secrets[i]
                else:
                    self.secrets[i] = (t, k, bytes(value))
                self._mark_dirty()
                return True
        if value is None:
            return False
        if len(self.secrets) >= MAX_SECRETS:
            i = self._evict_index()
            if i is None:
                return False  # 消せるものがない(本機の ER/IR だけ): 保存しない
            del self.secrets[i]
        self.secrets.append((sec_type, key, bytes(value)))
        self._mark_dirty()
        return True

    def _evict_index(self):
        """上限のときに消す鍵。本機の ER/IR は消さない(消すとすべてのボンドが使えなくなる)。
        通知の設定(BTstack の CCCD、タグ BTC)を古いものから先に消し、なければほかの古いもの"""
        other = None
        for i, (t, k, _v) in enumerate(self.secrets):
            if t == SECRET_TYPE_TLV and k in KEEP_TAGS:
                continue
            if t == SECRET_TYPE_TLV and k[:3] == b"BTC":
                return i
            if other is None:
                other = i
        return other

    def _mark_dirty(self):
        self._secrets_gen += 1
        self._secrets_dirty_ms = time.ticks_ms()

    def _load_secrets(self):
        self.secrets = []
        for p in (self.bonds_path, self.tmp):
            try:
                f = self._open(p, "rb")
                try:
                    doc = json.loads(f.read())
                finally:
                    f.close()
                self.secrets = [(t, binascii.unhexlify(k), binascii.unhexlify(v))
                                for t, k, v in doc["secrets"]][-MAX_SECRETS:]
                return
            except (OSError, ValueError, KeyError, TypeError):
                continue

    def _save_secrets(self, force=False, now=None, raise_errors=False):
        if self._secrets_dirty_ms is None:
            return
        if not force:
            now = time.ticks_ms() if now is None else now
            if time.ticks_diff(now, self._secrets_dirty_ms) < SAVE_DELAY_MS:
                return
        gen = self._secrets_gen  # 書いている間に IRQ で変わったら、もう一度書く
        doc = {"version": 1, "secrets": [
            (t, binascii.hexlify(k).decode(), binascii.hexlify(v).decode())
            for t, k, v in list(self.secrets)]}
        try:
            self._write_bonds(json.dumps(doc).encode())
        except OSError as e:
            if raise_errors:  # forget_all(USB から): 消せなかったことを知らせる
                raise
            # フラッシュに書けない(満杯など): BLE は止めず、SAVE_RETRY_MS あとにやり直す。
            # 書けるまでは、電源を切るとこの間にできたボンドが消える
            self.log("[ble] bonds not saved (%r), retry in %d s" % (e, SAVE_RETRY_MS // 1000))
            if not force:
                t = time.ticks_ms() if now is None else now
                self._secrets_dirty_ms = time.ticks_add(t, SAVE_RETRY_MS - SAVE_DELAY_MS)
            return
        if self._secrets_gen == gen:
            self._secrets_dirty_ms = None
        self.log("[ble] bonds saved (%d)" % len(doc["secrets"]))

    def _write_bonds(self, data):
        f = self._open(self.tmp, "wb")
        try:
            f.write(data)
        finally:
            f.close()
        try:
            self._remove(self.bonds_path)
        except OSError:
            pass
        self._rename(self.tmp, self.bonds_path)

    def forget_all(self):
        """ボンドをすべて消す。BTstack は起動時に読んだ表を持っているので、BLE を止めている
        ときだけ消せる(動いていれば False)。ER/IR(本機の鍵)は残す"""
        if self.running:
            return False
        self.secrets = [s for s in self.secrets if s[0] == SECRET_TYPE_TLV and s[1] in KEEP_TAGS]
        self._mark_dirty()
        self._save_secrets(force=True, raise_errors=True)
        try:
            self._remove(self.tmp)
        except OSError:
            pass
        return True

    # ---- 送信(remote.write として使う)
    def write(self, data):
        """1 行(\\n 付き)を通知で送る。MTU - 3 バイト(NOTIFY_MAX まで)ずつに区切る。
        True: 受け付けた(送りきれなかった分は poll() で送る)/ False: 前の送り残しが詰まっている"""
        if not self.authenticated or self.conn is None:
            return True  # 接続していない: 捨てる(remote も送らないはず)
        self._flush_out()
        if self._out:
            return False
        n = min(self.mtu - ATT_OVERHEAD, NOTIFY_MAX)
        self._out = [data[i:i + n] for i in range(0, len(data), n)]
        self._flush_out()
        return True

    def _flush_out(self):
        # 切断の IRQ(_set_connected)は Python のコードの途中でも割り込み、self._out を新しい
        # リストに替える。手元の参照で回し、替わったら(切れたら)やめる
        out = self._out
        while out and out is self._out and self.conn is not None:
            try:
                self._ble.gatts_notify(self.conn, self._h_tx, out[0])
            except OSError:  # BTstack のキューが満杯。次の poll で送り直す
                return
            out.pop(0)

    @property
    def advertising_held(self):
        """ペアリングの失敗が続いて、広告を止めている(PAIR_HOLD_MS の間。LCD のアイコン)"""
        return self._adv_hold_ms is not None and self.conn is None

    def _check_pairing(self, now):
        """ペアリングに失敗した接続を切る。続けて失敗したら、しばらく広告しない"""
        if self._pair_failed and self.conn is not None and not self._kicked:
            self._pair_failed = False
            self._kicked = True  # 切断のイベントが来るまで、もう一度は要求しない
            self.pair_fails += 1
            if self.pair_fails >= PAIR_FAIL_MAX:
                self._adv_hold_ms = time.ticks_add(now, PAIR_HOLD_MS)
                # BTstack は広告を有効にしたままなら、切断のあと自分で広告を再開する(hci.c の
                # hci_update_advertisements_enabled_for_current_roles)。明示的に止める(2026-11-23 に
                # 実機で、止めている間に Android からつながった)
                self._ble.gap_advertise(None)
                self.log("[ble] pairing failed %d times, not advertising for %d s"
                         % (self.pair_fails, PAIR_HOLD_MS // 1000))
            else:
                self.log("[ble] pairing failed, disconnecting")
            try:
                self._ble.gap_disconnect(self.conn)
            except OSError:
                pass
        hold = self._adv_hold_ms
        if hold is not None and self.conn is None and time.ticks_diff(now, hold) >= 0:
            self._adv_hold_ms = None
            self.pair_fails = 0
            self._advertise()
            self.log("[ble] advertising again")

    def _check_auth_timeout(self, now):
        conn = self.conn
        if conn is None or self._kicked:
            return
        if time.ticks_diff(now, self.connected_ms) < AUTH_TIMEOUT_MS:
            return
        self._kicked = True  # 切断のイベントが来るまで、もう一度は要求しない
        self.log("[ble] not authenticated in %d s, disconnecting" % (AUTH_TIMEOUT_MS // 1000))
        try:
            self._ble.gap_disconnect(conn)
        except OSError:
            pass

    # ---- 局のループから
    def poll(self, now):
        if not self.running:
            return
        self._save_secrets(now=now)
        self._check_pairing(now)
        if not self.authenticated:
            self._check_auth_timeout(now)
            return
        self._flush_out()
        ln = self.lines
        # handle()(送信や設定の保存で時間がかかる)の途中で切断の IRQ が来ると、ln.lines は
        # 新しい空のリストに替わる(_set_connected -> clear)。先に _todo へ取り出してから回し、
        # 認証が外れたら(切れたら)残りは捨てる(前の接続の要求を、次の接続で実行しない)。
        # 一覧を返している間は次の行を渡さず、_todo に残して次の poll で続ける(応答の順を保つ)
        if not self._todo and ln.lines:  # 行が無いときは何も確保しない(毎回のループ)
            self._todo, ln.lines = ln.lines, []
        todo = self._todo
        while todo and self.remote.idle:
            if not self.authenticated or todo is not self._todo:
                return
            self.remote.handle(todo.pop(0))
        if ln.too_long:
            ln.too_long = 0
            self.remote.too_long()
        if ln.busy:
            ln.busy = 0
            self.remote.busy()
        self.remote.poll(now)
