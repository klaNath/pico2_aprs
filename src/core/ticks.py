"""time.ticks_ms() の値を長い時間持つときの補助。

ticks_ms は約 12.4 日(2^30 ms)で一周し、ticks_diff が正しいのは差が ±2^29 ms(約 6.2 日)まで。
「最後に送った時刻」のような値を何日も持っていると、差が負になって「ついさっき」に見える
(送信の間隔の判定などが逆になる)。局のループ(station.Station._sweep)が定期的に cap() を通して、
古い値を AGE_MAX_MS 前に寄せておく。寄せたあとも「AGE_MAX_MS 以上たった」ことは保たれるので、
AGE_MAX_MS より短い間隔との比較はそのまま正しい。
"""

import time

from micropython import const

AGE_MAX_MS = const(432_000_000)  # 5 日。2^29 ms(約 6.2 日)より短く、どの間隔の判定よりも長い


def cap(now, t):
    """過去の時刻 t(ticks_ms / None)が AGE_MAX_MS より古ければ、ちょうど AGE_MAX_MS 前を返す"""
    if t is not None and time.ticks_diff(now, t) > AGE_MAX_MS:
        return time.ticks_add(now, -AGE_MAX_MS)
    return t


def passed(now, deadline):
    """期限 deadline(ticks_ms / None)を過ぎていれば None、まだなら deadline を返す
    (過ぎた期限を持ち続けると、一周したあとに遠い未来に見える)"""
    if deadline is not None and time.ticks_diff(now, deadline) >= 0:
        return None
    return deadline
