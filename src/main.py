"""デバイス起動時に自動実行されるエントリポイント(deploy 後)。

開発中は `python tools/dev.py run -t 10` でフラッシュに書き込まずに実行できる。
"""

import app

app.run()
