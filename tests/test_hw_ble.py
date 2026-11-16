"""hw.ble を実機の BLE(CYW43 + BTstack)で動かす。実機専用。中央装置(スマホ、PC)は要らない。
確かめること: 起動と広告、サービスの登録、ペアリングの設定が通ること、停止、ヒープの増減。
本物のペアリングと通信は tools/ble_cli.py(PC)で確かめる。ファイルは偽物(フラッシュに書かない)。"""

import gc

from test_ble import make  # noqa: F401  (FakeFs など同じ部品を使う)
from test_remote import Src
from test_settings import Cfg, FakeFs

from core.remote import Remote
from core.settings import Settings


def _real_ble(fs):
    from hw.ble import Ble

    cfg = Cfg()
    st = Settings(cfg, "/settings.json", opener=fs.open, remove=fs.remove, rename=fs.rename)
    src = Src()
    r = Remote(st, None, src.status_fn, src.stations_fn)
    shown = []
    return Ble(r, on_passkey=shown.append, bonds_path="/ble_bonds.json", opener=fs.open,
               remove=fs.remove, rename=fs.rename), r, shown


def test_hw_ble_start_stop_and_heap():
    fs = FakeFs()
    gc.collect()
    free0 = gc.mem_free()
    b, r, _shown = _real_ble(fs)
    b.start()
    try:
        name = b.name or ""
        assert b.running and name.startswith("PicoAPRS-") and len(name) == 13
        assert r.write == b.write
        gc.collect()
        used = free0 - gc.mem_free()
        print("ble started: heap used %d bytes, name %s" % (used, b.name))
        assert used < 24 * 1024
        b.poll(0)  # 接続がなくても落ちない
        assert not b.authenticated and not r.attached
    finally:
        b.stop()
    assert not b.running
    # もう一度起動できる(停止後の再起動)
    b2, _r2, _s2 = _real_ble(fs)
    b2.start()
    b2.stop()


def test_hw_ble_bonds_survive_restart():
    fs = FakeFs()
    b, _r, _shown = _real_ble(fs)
    b.start()
    try:
        b._ble.irq(b._irq)  # 本物の BLE でも自分の IRQ ハンドラが結ばれている
        b._set_secret(2, b"key", b"value")
        b._save_secrets(force=True)
    finally:
        b.stop()
    b2, _r2, _s2 = _real_ble(fs)
    b2.start()
    try:
        assert b2._get_secret(2, 0, b"key") == b"value"
    finally:
        b2.stop()
