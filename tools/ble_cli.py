"""BLE 接続検証ツール(PC 側、bleak 使用)。Web アプリを作る前に実機との通信を確かめる。

    python tools/ble_cli.py scan
    python tools/ble_cli.py pair                      # LCD のパスキーを Windows のダイアログに入力
    python tools/ble_cli.py hello
    python tools/ble_cli.py get | schema | stations
    python tools/ble_cli.py set comment=Hello beacon_still_s=900 tx_enabled=false
    python tools/ble_cli.py reset
    python tools/ble_cli.py listen -s 60              # status / rx / tx イベントを表示

接続先は --name(既定 PicoAPRS 始まり)か --address で選ぶ。仕様は docs/ble-protocol.md。
"""

import argparse
import asyncio
import json
import sys

from bleak import BleakClient, BleakScanner

NUS_SERVICE = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # PC -> デバイス(write)
NUS_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # デバイス -> PC(notify)


async def find(args):
    if args.address:
        return args.address
    dev = await BleakScanner.find_device_by_filter(
        lambda d, ad: NUS_SERVICE in [u.lower() for u in ad.service_uuids]
        and (not args.name or (d.name or ad.local_name or "").startswith(args.name)),
        timeout=args.scan_s,
    )
    if dev is None:
        sys.exit("device not found (is BLE enabled and not connected elsewhere?)")
    print("found %s (%s)" % (dev.name, dev.address))
    return dev


async def scan(args):
    seen = await BleakScanner.discover(timeout=args.scan_s, return_adv=True)
    for addr, (d, ad) in sorted(seen.items(), key=lambda kv: -kv[1][1].rssi):
        mark = "*" if NUS_SERVICE in [u.lower() for u in ad.service_uuids] else " "
        print("%s %s  %4d dBm  %s" % (mark, addr, ad.rssi, d.name or ad.local_name or ""))


PASSKEY_FILE = None
PASSKEY_WAIT_S = 25  # 本機(BTstack)は約 30 秒でペアリングを打ち切る


def ask_passkey():
    """標準入力から、または --passkey-file に書かれるのを待って、パスキーを得る。
    得られなければ None(ペアリングを断る)。"""
    if PASSKEY_FILE is None:
        return input("passkey shown on the LCD: ").strip() or None
    import os
    import time

    print("waiting for the passkey in", PASSKEY_FILE, flush=True)
    end = time.monotonic() + PASSKEY_WAIT_S
    while time.monotonic() < end:
        if os.path.exists(PASSKEY_FILE):
            v = open(PASSKEY_FILE).read().strip()
            if v:
                os.remove(PASSKEY_FILE)  # 次の回に古い値を使わない
                return v
        time.sleep(0.5)
    return None


async def pair_with_passkey(client, repair=False):
    """Windows でパスキー入力(LCD に出る 6 桁)を使ってペアリングする。

    bleak の pair() は Just Works しか扱えないので、WinRT を直接使う。
    本機は Just Works を断るので、パスキー以外の方式は受けない(断る)。
    """
    import os

    from bleak.backends.winrt.client import BleakClientWinRT
    from winrt.windows.devices.enumeration import (
        DeviceInformation,
        DevicePairingKinds,
        DevicePairingProtectionLevel,
    )

    assert isinstance(client._backend, BleakClientWinRT)
    info = await DeviceInformation.create_from_id_async(client._backend._requester.device_information.id)
    if info.pairing.is_paired:
        if not repair:
            print("already paired (if commands get no answer, the bond may be stale: use --repair)")
            return
        r = await info.pairing.unpair_async()
        print("unpaired:", r.status.name)
    if PASSKEY_FILE is not None and os.path.exists(PASSKEY_FILE):
        os.remove(PASSKEY_FILE)  # 前の回の値
    custom = info.pairing.custom
    errors = []

    def handler(_sender, a):
        # WinRT のスレッドで呼ばれる。ここで sys.exit しない(例外は後で報告する)
        try:
            if a.pairing_kind & DevicePairingKinds.PROVIDE_PIN:
                pin = ask_passkey()
                if pin is None:
                    errors.append("no passkey")
                else:
                    a.accept(pin)
            else:
                errors.append("unexpected pairing kind %r (device should require a passkey)" % a.pairing_kind)
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))

    tok = custom.add_pairing_requested(handler)
    try:
        r = await custom.pair_with_protection_level_async(
            DevicePairingKinds.PROVIDE_PIN | DevicePairingKinds.CONFIRM_ONLY,
            DevicePairingProtectionLevel.ENCRYPTION_AND_AUTHENTICATION,
        )
    finally:
        custom.remove_pairing_requested(tok)
    print("pair result:", r.status.name)
    for e in errors:
        print("  ", e)
    if r.status.name not in ("PAIRED", "ALREADY_PAIRED"):
        sys.exit("pairing failed")


class Session:
    def __init__(self, client):
        self.client = client
        self.buf = b""
        self.queue = asyncio.Queue()
        self.next_id = 1

    def on_notify(self, _char, data):
        self.buf += bytes(data)
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            line = line.strip()
            if line:
                try:
                    self.queue.put_nowait(json.loads(line))
                except ValueError:
                    print("bad line:", line)

    async def request(self, op, **kw):
        """要求を送り、同じ id の応答を最後まで集めて返す(station は stations.end まで)。"""
        rid = self.next_id
        self.next_id += 1
        msg = dict(kw, op=op, id=rid)
        await self.client.write_gatt_char(NUS_RX, (json.dumps(msg) + "\n").encode(), response=True)
        out = []
        while True:
            m = await asyncio.wait_for(self.queue.get(), 10)
            if m.get("id") != rid:
                show(m)  # 途中で来た別のイベント
                continue
            out.append(m)
            if m.get("t") == "err" or op != "stations.list" or m.get("t") == "stations.end":
                return out


def show(m):
    print(json.dumps(m, ensure_ascii=False))


def parse_value(s):
    try:
        return json.loads(s)
    except ValueError:
        return s


async def main_async(args):
    if args.cmd == "scan":
        return await scan(args)
    target = await find(args)
    async with BleakClient(target, timeout=15) as client:
        print("connected, mtu", client.mtu_size)
        if args.cmd == "pair" or args.pair:
            print("pairing...")
            if sys.platform == "win32" and not args.justworks:
                await pair_with_passkey(client, repair=args.repair)
            else:
                await client.pair()
        s = Session(client)
        await client.start_notify(NUS_TX, s.on_notify)
        if args.cmd == "pair":
            for m in await s.request("hello"):
                show(m)
        elif args.cmd == "listen":
            end = asyncio.get_running_loop().time() + args.s
            while (left := end - asyncio.get_running_loop().time()) > 0:
                try:
                    show(await asyncio.wait_for(s.queue.get(), left))
                except asyncio.TimeoutError:
                    break
        else:
            kw = {}
            op = {"hello": "hello", "get": "settings.get", "schema": "settings.schema",
                  "stations": "stations.list", "reset": "settings.reset", "set": "settings.set"}[args.cmd]
            if args.cmd == "set":
                kw["values"] = {k: parse_value(v) for k, v in (kv.split("=", 1) for kv in args.kv)}
            for m in await s.request(op, **kw):
                show(m)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", default="PicoAPRS", help="広告名の先頭(既定 PicoAPRS)")
    ap.add_argument("--address", help="アドレスを直接指定")
    ap.add_argument("--scan-s", type=float, default=8.0)
    ap.add_argument("--passkey-file", help="パスキーを標準入力の代わりにこのファイルから読む(自動化用)")
    ap.add_argument("--justworks", action="store_true", help="bleak 標準の pair()(Just Works)を使う。切り分け用")
    ap.add_argument("--pair", action="store_true", help="接続後にペアリングしてから実行")
    ap.add_argument("--repair", action="store_true", help="Windows 側のボンドを消してからペアリングし直す")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for c in ("scan", "pair", "hello", "get", "schema", "stations", "reset"):
        sub.add_parser(c)
    sp = sub.add_parser("set")
    sp.add_argument("kv", nargs="+", metavar="key=value")
    sp = sub.add_parser("listen")
    sp.add_argument("-s", type=float, default=30.0)
    args = ap.parse_args()
    global PASSKEY_FILE
    PASSKEY_FILE = args.passkey_file
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
