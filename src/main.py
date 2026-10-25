"""デバイス起動時に自動実行されるエントリポイント(deploy 後)。APRS 局の本体を動かす。

開発中は `python tools/dev.py run -t 60` でフラッシュに書き込まずに実行できる。
例外で止まったら station.main() が内容を表示して再起動する(USB から Ctrl-C で止めたときは除く)。
"""

import station

station.main()
