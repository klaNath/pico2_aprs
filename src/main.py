"""デバイス起動時に自動実行されるエントリポイント(deploy 後)。APRS 局の本体を動かす。

開発中は `python tools/dev.py run -t 60` でフラッシュに書き込まずに実行できる。
"""

import station

station.run()
