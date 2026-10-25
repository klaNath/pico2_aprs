"""異常時の再起動の待ち時間と、続けて失敗した回数の記録(station.main が使う)。

失敗の回数はウォッチドッグのスクラッチレジスタに置く。リセットでは消えず、電源を切ると消える
(フラッシュに書かない)。station を import すると大きなバッファを確保するので、テストはこちらを使う。
"""

import machine

RESTART_S = 10
RESTART_MAX_S = 300
_SCRATCH = 0x400D8000 + 0x0C  # RP2350 WATCHDOG SCRATCH0(SCRATCH4-7 はブート ROM が使う)
_MAGIC = 0x5A000000


def wait_s(fails, base_s=RESTART_S):
    """続けて fails 回失敗したときの再起動までの秒(10, 20, 40, ... 最大 300)"""
    return min(base_s << min(max(fails - 1, 0), 8), RESTART_MAX_S)


def fails():
    v = machine.mem32[_SCRATCH]
    return v & 0xFFFF if v & 0xFFFF0000 == _MAGIC else 0


def set_fails(n):
    machine.mem32[_SCRATCH] = _MAGIC | n if n else 0
