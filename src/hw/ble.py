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
- 本機の入出力能力は DisplayOnly。パスキー(6 桁)を作って on_passkey(passkey) に渡す(LCD に出す)。
  終わったら on_passkey(None)
- ボンディングの鍵は bonds_path(JSON)に置く。ペアリングは稀なので、変わったときだけ書く
  (フラッシュへの書き込みは受信を止めるので、poll() の中で、通信がない間に 1 回だけ)
"""

import binascii
import json
import os
import struct
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

PASSKEY_ACTION_DISPLAY = const(3)
IO_DISPLAY_ONLY = const(0)

# 特性のフラグ(aioble と同じ値)
FLAG_WRITE_NO_RESPONSE = const(0x0004)
FLAG_WRITE = const(0x0008)
FLAG_NOTIFY = const(0x0010)
FLAG_WRITE_AUTHENTICATED = const(0x2000)  # 暗号化 + MITM のペアリングが済んだ接続だけ書ける

LINE_MAX = _remote.LINE_MAX  # 受ける 1 行の上限(バイト)
RX_BUF = const(512)  # RX 特性のバッファ(1 回の書き込みの上限は MTU - 3)
ATT_OVERHEAD = const(3)
DEFAULT_MTU = const(23)
MTU_WANTED = const(517)
ADV_INTERVAL_US = const(100_000)
LINES_MAX = const(4)  # 処理待ちの行の上限(溢れたら、その行は捨てて too_long の代わりに busy)
MAX_SECRETS = const(24)  # 保存する鍵の数(1 台につき数個。4 台分)
BONDS_PATH = "/ble_bonds.json"
SAVE_DELAY_MS = const(2000)  # 鍵が変わってから書くまで(続けて変わるのをまとめる)
PASSKEY_MAX = const(1_000_000)


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
                 remove=os.remove, rename=os.rename, urandom=os.urandom, log=None):
        """remote: core.remote.Remote(write は start() で結ぶ)。on_passkey(passkey または None):
        LCD にパスキーを出す/消す。ble: bluetooth.BLE のようなもの(試験用)"""
        self.remote = remote
        self.on_passkey = on_passkey
        self.bonds_path = bonds_path
        self.tmp = bonds_path + ".tmp"
        self._ble: "Any" = ble
        self._open, self._remove, self._rename = opener, remove, rename
        self._urandom = urandom
        self.log = log or (lambda *a: None)
        self.name = None
        self.conn = None  # 接続中のハンドル
        self.authenticated = False
        self.mtu = DEFAULT_MTU
        self.passkey = None
        self.lines = LineBuffer()
        self.dropped_writes = 0  # 認証前に書かれて捨てたバイト数
        self.secrets = []  # [(種類, key, value)]
        self._secrets_dirty_ms = None
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
        b.active(True)
        b.irq(self._irq)
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
        self._advertise()
        self.log("[ble] advertising as %s" % self.name)

    def stop(self):
        if self.conn is not None:
            try:
                self._ble.gap_disconnect(self.conn)
            except OSError:
                pass
        self._set_connected(None)
        if self._ble is not None and self.running:
            self._ble.gap_advertise(None)
            self._ble.active(False)
        self.running = False
        self._save_secrets(force=True)

    def _advertise(self):
        self._ble.gap_advertise(
            ADV_INTERVAL_US, adv_data=adv_payload(None, uuid_le(NUS)),
            resp_data=adv_payload(self.name))

    # ---- IRQ(解析や応答はしない)
    def _irq(self, event, data):
        if event == IRQ_CENTRAL_CONNECT:
            self._set_connected(data[0])
        elif event == IRQ_CENTRAL_DISCONNECT:
            self._set_connected(None)
            if self.running:
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
            conn, encrypted, authenticated = data[0], data[1], data[2]
            if conn == self.conn:
                self._set_authenticated(bool(encrypted and authenticated))
        elif event == IRQ_PASSKEY_ACTION:
            conn, action = data[0], data[1]
            if action == PASSKEY_ACTION_DISPLAY:
                self.passkey = struct.unpack("<I", self._urandom(4))[0] % PASSKEY_MAX
                self._ble.gap_passkey(conn, action, self.passkey)
                if self.on_passkey is not None:
                    self.on_passkey(self.passkey)
        elif event == IRQ_GET_SECRET:
            return self._get_secret(data[0], data[1], data[2])
        elif event == IRQ_SET_SECRET:
            return self._set_secret(data[0], data[1], data[2])
        return None

    def _set_connected(self, conn):
        was = self.conn
        self.conn = conn
        self.mtu = DEFAULT_MTU
        self.lines.clear()
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
            self._clear_passkey()
            self.lines.clear()
            self.remote.attach()
            self.log("[ble] authenticated")
        else:
            self.remote.detach()

    def _clear_passkey(self):
        if self.passkey is not None:
            self.passkey = None
            if self.on_passkey is not None:
                self.on_passkey(None)

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
                self._secrets_dirty_ms = time.ticks_ms()
                return True
        if value is None:
            return False
        if len(self.secrets) >= MAX_SECRETS:
            del self.secrets[0]  # 古いものから消す
        self.secrets.append((sec_type, key, bytes(value)))
        self._secrets_dirty_ms = time.ticks_ms()
        return True

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

    def _save_secrets(self, force=False, now=None):
        if self._secrets_dirty_ms is None:
            return
        if not force:
            now = time.ticks_ms() if now is None else now
            if time.ticks_diff(now, self._secrets_dirty_ms) < SAVE_DELAY_MS:
                return
        doc = {"version": 1, "secrets": [
            (t, binascii.hexlify(k).decode(), binascii.hexlify(v).decode())
            for t, k, v in self.secrets]}
        f = self._open(self.tmp, "wb")
        try:
            f.write(json.dumps(doc).encode())
        finally:
            f.close()
        try:
            self._remove(self.bonds_path)
        except OSError:
            pass
        self._rename(self.tmp, self.bonds_path)
        self._secrets_dirty_ms = None
        self.log("[ble] bonds saved (%d)" % len(self.secrets))

    def forget_all(self):
        """保存した鍵をすべて消す(USB から: dev.py exec "import ...; ble.forget_all()")"""
        self.secrets = []
        self._secrets_dirty_ms = None
        for p in (self.bonds_path, self.tmp):
            try:
                self._remove(p)
            except OSError:
                pass

    # ---- 送信(remote.write として使う)
    def write(self, data):
        """1 行(\\n 付き)を通知で送る。MTU - 3 バイトずつに区切る。
        True: 受け付けた(送りきれなかった分は poll() で送る)/ False: 前の送り残しが詰まっている"""
        if not self.authenticated or self.conn is None:
            return True  # 接続していない: 捨てる(remote も送らないはず)
        self._flush_out()
        if self._out:
            return False
        n = self.mtu - ATT_OVERHEAD
        self._out = [data[i:i + n] for i in range(0, len(data), n)]
        self._flush_out()
        return True

    def _flush_out(self):
        while self._out and self.conn is not None:
            try:
                self._ble.gatts_notify(self.conn, self._h_tx, self._out[0])
            except OSError:  # BTstack のキューが満杯。次の poll で送り直す
                return
            self._out.pop(0)

    # ---- 局のループから
    def poll(self, now):
        if not self.running:
            return
        self._save_secrets(now=now)
        if not self.authenticated:
            return
        self._flush_out()
        ln = self.lines
        for _ in range(len(ln.lines)):
            self.remote.handle(ln.lines.pop(0))
        if ln.too_long:
            ln.too_long = 0
            self.remote.too_long()
        if ln.busy:
            ln.busy = 0
            self.remote.busy()
        self.remote.poll(now)
