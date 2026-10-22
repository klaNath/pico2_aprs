# Pico 2 × MicroPython × Claude Code 開発環境

VSCode 上の Claude Code が、コードを書き、Pico 2 実機で lint・テスト・実行・デバッグまで回せる構成です。

```
VSCode
 ├─ Claude Code 拡張 ── ターミナルで tools/dev.py を実行
 ├─ Pylance + MicroPython スタブ(typings/)── 補完・型チェック
 └─ Ruff ── lint / format
          │
          ▼  tools/dev.py(タイムアウト付き・非対話の mpremote ラッパー)
          ▼  USB シリアル
     Raspberry Pi Pico 2(MicroPython)
```

ポイントは **Claude Code を Pico をつないだ PC 上で動かす** ことです。そうすれば Claude が自分で mpremote を叩いて実機の出力を読めます。

---

## 1. 必要なもの

- Raspberry Pi Pico 2(または Pico 2 W)と **データ通信対応の** USB ケーブル
- VSCode
- Python 3.9 以上(Windows は python.org 版か `winget install Python.Python.3.12`)
- Claude のアカウント(Claude Code が使えるプラン)

## 2. MicroPython を Pico 2 に書き込む(初回のみ)

1. https://micropython.org/download/RPI_PICO2/ から最新の **.uf2** をダウンロード
   (Pico 2 W なら RPI_PICO2_W。「RISC-V CPU mode」版ではなく通常版を選ぶ)
2. Pico の **BOOTSEL ボタンを押したまま** USB を挿す → `RP2350` というドライブが現れる
3. .uf2 をそのドライブにコピー → 自動で再起動して MicroPython が起動

## 3. プロジェクトのセットアップ

このフォルダを好きな場所に置いて VSCode で開き、ターミナルで:

```bash
# macOS / Linux
python3 tools/bootstrap.py

# Windows
py tools\bootstrap.py
```

Pico 2 W の場合は `--board rpi_pico2_w`、ファームウェアが最新でない場合は `--mp-version 1.28.0` のように合わせます。

完了したら VSCode で `Ctrl+Shift+P` →「Python: Select Interpreter」→ `.venv` を選択。
推奨拡張機能のインストールを促されたら全部入れてください(Claude Code, Python, Pylance, Ruff)。

### OS 別の注意

- **Linux**: シリアルポートの権限が必要です。`sudo usermod -aG dialout $USER` のあと再ログイン
- **macOS**: ポートは `/dev/cu.usbmodem…`。追加作業は不要
- **Windows**: ドライバ不要。ポートは `COM3` など

## 4. 動作確認

```bash
python tools/dev.py ports    # Pico が見えるか
python tools/dev.py info     # MicroPython のバージョン・空きメモリ
python tools/dev.py test     # 実機でテスト → 最後に RESULT: OK
python tools/dev.py run -t 5 # LED が 5 秒点滅して、タイムアウトで終了
python tools/dev.py lint     # ruff + pyright(初回は pyright が Node を自動取得するので少し待つ)
```

ここまで通れば、Claude が使う道具は全部そろっています。

## 5. Claude Code で開発を始める

VSCode のサイドバー(またはエディタ右上)の Claude アイコンからパネルを開き、サインインします。
Claude はプロジェクト直下の `CLAUDE.md` を自動で読むので、ルール(タイムアウト必須、deploy は確認制、など)を毎回説明する必要はありません。

最初の依頼の例:

> `dev.py info` で環境を確認してから、GP15 に繋いだタクトスイッチを押すと LED が点灯/消灯するようにして。テストも書いて実機で通るまで直して。

`.claude/settings.json` で権限を設定済みです:

| 操作 | 扱い |
|------|------|
| `dev.py info / run / exec / test / lint / ls / cat / monitor` | 確認なしで実行 |
| `dev.py deploy / rm-main / mip`(フラッシュを書き換える) | 毎回確認 |
| `dev.py repl`(対話モードで固まる) | 禁止 |

**配線は `CLAUDE.md` 末尾の表に書いておく** と、Claude がピン番号を正しく使えます。

## 6. 仕組みとコマンド一覧

`run` / `exec` / `test` は `mpremote mount` で PC の `src/` をデバイスに見せて実行するので、**フラッシュに書かずに** 何度でも試せます。完成したら `deploy` で書き込み、電源投入時に `main.py` が自動で動くようになります。

```
python tools/dev.py --help
```

| コマンド | 内容 |
|----------|------|
| `ports` | 接続中の Pico を列挙 |
| `info` | ファームウェア / メモリ / ストレージ |
| `run [FILE] -t N` | `src/` をマウントして実行(既定 `src/main.py`) |
| `exec "CODE" -t N` | コード片を実行(REPL の代わり) |
| `test [PATTERN]` | `tests/test_*.py` を実機で実行、結果で終了コード |
| `lint` / `check` | ruff + pyright / それ + test |
| `deploy [--reset]` | `src/` の中身をフラッシュに書き込み |
| `romfs-query` | ROMFS 区画の有無・サイズを表示 |
| `romfs-build [-o FILE]` | `src/` から ROMFS イメージを作るだけ(既定 `build/app.romfs`) |
| `romfs-deploy [--reset]` | `src/` を .mpy 化して ROMFS に書き込み、フラッシュにランチャー `main.py` を配置 |
| `monitor -s N [--reset]` | シリアル出力を N 秒読む(起動時の挙動確認) |
| `ls` / `cat` / `rm-main` / `mip` / `reset` | デバイスのファイル操作など |
| `repl` | 人間用の対話 REPL |

複数台つないでいる場合は環境変数 `PICO_PORT` でポートを指定します。

### ROMFS への書き込み

ROMFS はフラッシュ上の読み出し専用イメージで、`/rom` にマウントされます。.mpy にコンパイル済みのコードをフラッシュから直接実行するので、通常の `deploy` より import が速く、RAM の消費も抑えられます。

```bash
python tools/dev.py romfs-query     # まず区画があるか確認
python tools/dev.py romfs-deploy    # ROMFS へ書き込み → ソフトリセット → /rom の中身を表示
python tools/dev.py monitor --reset -s 15   # 起動時の動作確認
```

- ファームウェアに ROMFS 区画が無い場合は使えません(`romfs-query` で分かります)
- `mpy-cross` は `requirements-dev.txt` に含まれています。ファームウェアと同じバージョン系列にしておくと安全です(例: `pip install "mpy-cross==1.28.*"`)
- 元に戻すには通常の `deploy` を実行します(フラッシュの `main.py` が上書きされます)

## MicroPico と併用する場合

MicroPico(VSCode 拡張)はシリアルポートを常時掴むため、Claude の `dev.py` と同時には使えません。
併用するなら、Claude に作業させる前に MicroPico のステータスバーから **Disconnect** してください。
本構成は MicroPico なしで完結するので、迷ったら入れなくて構いません。

## ファイル構成

```
.
├── CLAUDE.md               Claude への作業ルール(ターゲット情報・禁止事項・デバッグ手順)
├── .claude/settings.json   Claude Code の権限設定
├── .vscode/                推奨拡張・設定・タスク(Tasks: Run Task から人間も使える)
├── pyproject.toml          pyright / ruff 設定
├── requirements-dev.txt    mpremote, mpy-cross, ruff, pyright
├── src/                    デバイスに載るコード(main.py, app.py)
├── tests/                  実機テスト(_runner.py, test_app.py)
├── tools/                  bootstrap.py(初期設定), dev.py(開発 CLI)
├── third_party/micro-aprs/ サブモジュール(klaNath/micro-aprs の pico2-subset ブランチ)
└── LICENSE                 GPL-3.0(micro-aprs から継承)
```

## micro-aprs サブモジュールとライセンス

モデム/AX.25 部分は [stephanelsmith/micro-aprs](https://github.com/stephanelsmith/micro-aprs) をフォークした
[klaNath/micro-aprs](https://github.com/klaNath/micro-aprs) の `pico2-subset` ブランチに由来します。
改変はすべてフォーク側にコミットとして積み、本家との差分を追跡できるようにしています。
そのためこのプロジェクト全体のライセンスは **GPL-3.0** です。

本家には Windows のパス長制限を超えるファイル名があるため、サブモジュールは `src/` `upy/` だけを
展開する sparse-checkout で使います。新しく clone するときは:

```bash
git clone <このリポジトリ> && cd <ディレクトリ>
git config core.longpaths true
git submodule init
git clone --no-checkout https://github.com/klaNath/micro-aprs third_party/micro-aprs   # 既存 repo として取り込む
git -C third_party/micro-aprs config core.longpaths true
git -C third_party/micro-aprs sparse-checkout set src upy
git -C third_party/micro-aprs checkout pico2-subset
git submodule absorbgitdirs                    # .git を親の .git/modules へ移す
```
