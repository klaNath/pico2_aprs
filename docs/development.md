# 開発ガイド

pico2_aprs を開発する人向けの説明です。局の概要と動かし方は [README.md](../README.md) を見てください。

Claude Code で作業するときの規則は [CLAUDE.md](../CLAUDE.md) にあり、このガイドの内容を前提にしています。

---

## 1. 動作モード

`config.MODE` で、電源投入時に動作を選びます(実行中には切り替えません)。`src/main.py` が `station.main()` を呼び、そこで分かれます。

| 値 | 本体 | 状態 |
|----|------|------|
| `"tnc"`(既定) | `src/station.py` | 無線機の外付け TNC。受信は GP26 の音声(ADC)、送信は GP3 の音声(PWM)と PTT(GP2)。README に書いている動作はすべてこのモード |
| `"si5351"` | `src/transceiver.py` | Si5351B を使うフル機能のトランシーバー。**未完成** |

### Si5351 モード(未完成)

今は、LCD に「Si5351 MODE / NOT COMPLETE」と出して待つだけです。

- 受信・送信・GNSS・ビーコン・SD の記録は動かしません。BLE も起動しません(BLE は tnc モードだけ)
- PTT(GP2)は切ったままにし、Si5351 にも I2C にも触れません
- ウォッチドッグは(有効なら)フィードして、再起動を繰り返さないようにします

このモードのために用意してあるもの(どれも tnc モードでは使いません):

| 部分 | 内容 |
|------|------|
| `src/hw/si5351.py`、`config.SI5351_*` | Si5351B のドライバと設定。出力の番号は未確定(`None`) |
| `hw.txctl.si5351_gate` | Si5351 を周波数源にする送信ゲート。周波数が確定し、144〜146 MHz に入り、周波数源の準備ができていなければ送信を拒否する。I2C の失敗で出力を止められなければ、フィードせずに止まってウォッチドッグで再起動する |
| I2C0(GP4 SDA / GP5 SCL) | 無線機ボードへのコネクタ J5 の 4・6 ピン(tnc モードでは使わない) |
| `src/hw/fm_if_rx.py`、`PIN_RX_IF`(GP8、未接続) | 455 kHz FM IF(リミッタを通した 0〜3.3 V の方形波)を PIO でエッジの時刻から復調する。周波数の求め方は `RX_IF_METHOD`: `"pll"`(DPLL、乱れたエッジに強い。既定)/ `"count"`(周期の逆数計数)。どちらも、雑音向けとハム向けの 2 つの復調器とデフレーマで復号する |
| `src/fm_if_test.py`、`fm_if_afsk_test.py`、`fm_if_rt_test.py`、`fm_if_pll_test.py` | IF 復調の測定と比較(信号発生器が要るものもある) |
| `tests/test_si5351.py` | `SKIP = "..."` で、PC でも実機でも飛ばしている。実装を進めるときに `SKIP` を消す |

実装するときは、`src/transceiver.py` に Si5351 のドライバ、`si5351_gate`、IF の受信を組み込みます。

---

## 2. 開発環境

```bash
git clone --recurse-submodules <このリポジトリ>
cd pico2_aprs
python3 tools/bootstrap.py --board rpi_pico2_w   # Windows は py tools\bootstrap.py --board rpi_pico2_w
```

- `bootstrap.py` は `.venv` を作り、[requirements-dev.txt](../requirements-dev.txt)(`mpremote`、`mpy-cross` 1.29 系列、`ruff`、`pyright`、`bleak`)を入れ、`typings/` に MicroPython の型スタブを作ります。`--mp-version 1.28.0` のようにスタブのバージョンも指定できます
- `dev.py` はどの python で起動しても、`.venv` があればそちらで実行し直します
- 複数台つないでいるときは、環境変数 `PICO_PORT` でポートを指定します(例 `COM5`、`/dev/ttyACM0`)
- 開発用の設定は git 管理外の `src/config_local.py` に書きます([tools/config_local.example.py](../tools/config_local.example.py))
- **自局のコールサインをソース、テスト、サンプルに書かないでください。** テストと例示には架空の値(`Q1TEST` など)を使い、架空であることを明記します

### ファームウェア

カスタムビルド `RPI_PICO2_W_ROMFS2`(MicroPython v1.29.0)を使います。ボード定義は [hw/RPI_PICO2_W_ROMFS2/](../hw/RPI_PICO2_W_ROMFS2/)、BTstack のパッチとビルドの手順は [hw/micropython/README.md](../hw/micropython/README.md) にあります。ビルドは WSL のツリーで行い、BOOTSEL で書き込みます。正確なバージョンは `python tools/dev.py info` で確かめます。

---

## 3. ディレクトリ構成

```
.
├── src/                    デバイスに載るコード
│   ├── boot.py             起動直後に PTT を切り、ウォッチドッグのフィードを引き継ぐ(wdt_bridge)
│   ├── wdt_bridge.py       ソフトリセットのあと、タイマーでウォッチドッグをフィードする
│   ├── main.py             起動時のエントリポイント(station.main())
│   ├── station.py          局(tnc モード)の本体。受信・ビーコン・GNSS・画面・SD・BLE を 1 つのループで回す
│   ├── transceiver.py      si5351 モード(未完成。§1)
│   ├── config.py           ピン割り当てと動作設定
│   ├── config_local.py     開発用の設定(git 管理外)
│   ├── core/               ハードに依存しない処理(送信ゲート txgate、ビーコン、局の表、メッセージ、
│   │                       設定 settings、BLE のコマンド処理 remote、受信音量とチャネル level、ログ、SD のファイル)
│   ├── hw/                 ハードのドライバ(AFSK 送受信、IF 復調、GNSS、LCD、microSD、BLE、
│   │                       ウォッチドッグ、再起動、送信ゲートと PTT のつなぎ txctl、Si5351)
│   ├── modem/              AFSK 復調器(demod)と FX.25
│   ├── ui/                 画面の描画、ボタン、通知、BLE のアイコン(生成物)
│   ├── aprs/               APRS の解析、メッセージ、Mic-E のエンコード
│   ├── tnc/                [生成物] micro-aprs 由来の AX.25 / HDLC / AFSK 変調
│   ├── maxm10/             [生成物] MAX-M10 のドライバ
│   ├── micropyGPS.py       [生成物] NMEA パーサ
│   └── *_demo.py など      部品ごとの確認用スクリプト(§11)
├── tests/                  テスト(_runner.py、_fake_hw.py、test_*.py)。実機でも PC でも動く
├── tools/
│   ├── dev.py              ビルド・書き込み・実行・デバッグ用の CLI(mpremote のラッパー)
│   ├── host_test.py        test --host の土台(MicroPython の代わりの関数)
│   ├── vendor.py           third_party/ から src/ の生成物を作る
│   ├── bootstrap.py        開発環境の初回セットアップ
│   ├── ble_cli.py          PC から BLE でつなぐ検証ツール(bleak)
│   ├── afsk_bench.py       復調器の劣化条件ベンチ
│   ├── icons/              BLE のアイコンの生成(make_icons.py、h2py.py → src/ui/ble_icons.py)
│   └── config_local.example.py  開発用の設定の例
├── docs/                   設計メモ(§12)と、無線機インターフェースのシミュレーション(spice/)
├── hw/                     基板のネットリスト、無線機インターフェースの回路(ft818_if/。gen_sch.py で生成)、
│                           ファームウェアのボード定義(RPI_PICO2_W_ROMFS2/)とパッチ(micropython/)
├── third_party/            サブモジュール(§7)
├── typings/                MicroPython の型スタブ(bootstrap.py が生成。編集しない)
└── LICENSE                 GPL-3.0
```

---

## 4. 開発の手順(`tools/dev.py`)

デバイスの操作はすべて `tools/dev.py` で行います。`mpremote` を直接、特に引数なしや `repl` で起動すると、対話モードに入って固まることがあります。実行には必ずタイムアウト(`-t 秒`)を付けます。

```bash
python tools/dev.py lint              # 1. vendor --check + ruff + pyright(PC だけ、デバイス不要)
python tools/dev.py test --host       # 2a. PC(CPython)上で tests/ を実行(速い。test_hw_* はスキップ)
python tools/dev.py test              # 2b. デバイス上で tests/ を実行 → 最後に RESULT: OK
python tools/dev.py run -t 15         # 3. src/main.py を実行して出力を見る
python tools/dev.py run src/xxx.py -t 10   # 個別のスクリプトの実行
python tools/dev.py exec "import config; print(config.MODE)" -t 10   # 対話の代わりのコード片の実行
python tools/dev.py check             # lint + test --host + test
python tools/dev.py build             # check のあと、本体だけ(デモを除く)の ROMFS イメージを作る(書き込みなし)
python tools/dev.py release           # build + ROMFS への書き込み + 起動の確認
```

変更したら、少なくとも `lint` と `test` を通します。`lint` の ruff のエラーは出力の先頭に出るので、最後の数行だけで判断しないでください。

| コマンド | 内容 |
|----------|------|
| `ports` / `info` | 接続中の Pico の列挙 / ファームウェア・メモリ・ストレージ |
| `run [FILE] -t N` | `src/` をマウントして実行(既定 `src/main.py`) |
| `exec "CODE" -t N` | コード片の実行(REPL の代わり)。書き込んだ本体を使うときは `--no-mount` |
| `test [PATTERN] [--host]` | `tests/test_*.py` の実行。`--host` は PC 上、省略すると実機 |
| `lint` | 生成物の確認(`vendor.py --check`)+ ruff + pyright |
| `check` | `lint` + `test --host` + `test` |
| `build [--skip-check]` | `check` のあと、デモを除いた ROMFS イメージを作る |
| `release [--skip-check]` | `build` + ROMFS への書き込み + 起動の確認 |
| `deploy [--reset]` | `src/` を `.py` のままフラッシュに書く |
| `romfs-query` / `romfs-build` / `romfs-deploy` | ROMFS 区画の確認 / イメージの作成 / 書き込み |
| `monitor -s N [--reset]` | シリアル出力を N 秒読む(起動時の挙動の確認) |
| `ls` / `cat` | デバイス上のファイルの一覧と表示 |
| `rm-main` | デバイス上の `main.py` を消す(自動起動を止める) |
| `mip PKG` | micropython-lib のパッケージを入れる |
| `reset` / `repl` | ハードリセット / 人間用の対話 REPL |

- `run` / `exec` / `test` は PC の `src/` を Pico にマウントして動くので、フラッシュを書き換えません。開発中の動作確認はこれで十分です
- 無限ループのプログラムがタイムアウトで終わるのは正常です(終了コード 124)。時間切れになると、Ctrl-C を送ってデバイス上のプログラムを止めてから終わります(止められなければ終了コード 125)
- フラッシュに書く `deploy` / `romfs-deploy` / `release`、`rm-main`、`mip` は、実行前に内容を確かめてください

### 書き込み方式

| 方式 | 内容 |
|------|------|
| `release`(普段はこれ) | `check` → デモを除いた ROMFS イメージ → `romfs-deploy` → ソフトリセットして `[station] ... started` を待つ。除外するファイルは `dev.py` の `RELEASE_EXCLUDE` |
| `romfs-deploy` | `src/` を `.mpy` にコンパイルした読み出し専用イメージを ROMFS 区画に書き、`/rom` から実行する。import が速く、RAM の消費が少ない。フラッシュには `/rom` の `_romfs_main` を呼ぶランチャー `main.py` と `boot.py` だけが残る |
| `deploy` | `src/` を `.py` のままフラッシュのファイルシステムに書く。デバイス上で直接編集できる。フラッシュの `main.py` が上書きされ、ROMFS 版から戻る |

- ROMFS を使う前に `romfs-query` で区画があるか確かめます(区画は 1 MB)
- `.mpy` 化は `dev.py` が `mpy-cross -march=armv7emsp` で行います(viper / native を含むため。mpremote 任せだと失敗します)
- ROMFS のイメージには `src/` の中身がそのまま入ります(`__pycache__` と `boot.py`、`RELEASE_EXCLUDE` を除く)。ドキュメントなど、デバイスに要らないファイルを `src/` に置かないでください
- 開発中の `run` / `test` はマウントが優先されるので、ROMFS の中身に影響されません
- 書き込んだ本体を動かすときは `dev.py exec --no-mount "..."` を使います

---

## 5. テスト

- `tests/` の `test_*.py` にある `test_*` 関数がテストです。ランナーは `tests/_runner.py`。同じテストが実機でも PC でも動きます
- 実機専用のテストは `test_hw_*` と名付けます(PC ではスキップ)
- モジュールに `SKIP = "理由"` を書くと、そのモジュールは PC でも実機でも飛ばします(今は未完成の si5351 モードの `test_si5351` だけ)
- ハードウェアに依存する部分(Pin、I2C、SPI など)はクラスの引数として渡し、テストでは偽物に差し替えます(`tests/_fake_hw.py`、`tests/test_tx_safety.py` が例)
- PC で実行するときの MicroPython の代わりの関数は `tools/host_test.py` にあります。PC と実機では、浮動小数点の精度(実機は単精度)やソートの安定性が違うことがあるので、実機のテストも流してください
- 復調器を変えたら `python tools/afsk_bench.py -n 200` を回します(劣化条件: 雑音、ハム、周波数ずれなど。PC だけで数分かかる)

### PTT の扱い

- PTT(GP2)は `core/txgate.py` のゲートだけが操作します。テストとデモは既定で PTT を入れません
- 実機のテスト(`dev.py test`、`check`、`build`、`release`)は偽のピンを使い、本物の PTT を駆動しません
- 本物の PTT を入れるデモは `src/tx_demo.py` だけで、`config.BENCH_TX_HZ` に周波数を明示したときだけです。電波が出るので、ダミーロードをつないで行います
- 送信時間の上限、PTT の後始末、ウォッチドッグの設計は [tx-time-limit.md](tx-time-limit.md)

---

## 6. 設定の仕組み

設定は `src/config.py`(既定値)< `src/config_local.py` < デバイスの `/settings.json` の順で上書きされます。

- `/settings.json` は BLE の `settings.set` で保存します(`core/settings.py`)。スマートフォンから変えられる項目は [ble-protocol.md §9](ble-protocol.md#9-settings-items)、設計は [ble-control.md §4](ble-control.md#4-設定の永続化)
- `src/config_local.py` は `release` のイメージにも入ります
- ピンや配線を変えたら `src/config.py` の先頭の説明と `PIN_*` を直し、[CLAUDE.md](../CLAUDE.md) の配線メモも合わせます

---

## 7. サブモジュール由来のコード

`src/` の次のファイルは、`third_party/` のサブモジュールから `tools/vendor.py` が生成したものです(対応表は `vendor.py` の `SOURCES`)。**手で編集しないでください。**

| 生成先 | サブモジュール | ブランチ | ライセンス |
|--------|----------------|----------|------------|
| `src/tnc/` | `third_party/micro-aprs/`(klaNath/micro-aprs、stephanelsmith/micro-aprs のフォーク) | `pico2-subset` | GPL-3.0 |
| `src/maxm10/` | `third_party/micropython-maxm10/`(klaNath/micropython-maxm10) | `pico2-aprs` | MIT |
| `src/micropyGPS.py` | `third_party/micropyGPS/`(klaNath/micropyGPS。互換性を保って改善する) | `pico2-aprs` | MIT |

- 変更はサブモジュール側で行い、そこでコミットしてから `python tools/vendor.py` で生成し直します。試行錯誤中は `--dirty` で未コミットのまま生成できます(ヘッダに `+dirty` と出ます)
- 生成したファイルの先頭には、出典(上流のファイルとコミット)と変更点が入ります
- `lint` は `vendor.py --check` で生成物が古くないかも確かめます。ruff / pyright は `src/maxm10`、`src/micropyGPS.py` を検査しません(各リポジトリの書式のまま)
- フォークへの push は、本体より先に行います。逆にすると、サブモジュールの参照先が存在しない状態になります

---

## 8. デバッグ

- **トレースバック**: `File "/remote/station.py", line N` の `/remote/` は、マウントした `src/` のことです。ROMFS から動かしているときは元のパス(`src/xxx.py`、`_romfs_main` は `src/main.py`)が出ます。ソースは表示されないので、手元の該当行を見ます
- **状態を覗く**: `dev.py exec "<コード>"` でモジュールを import して、関数を呼んだり変数を print したりします
- **メモリ**: `import gc; gc.collect(); print(gc.mem_free())` を怪しい箇所の前後で出します。`MemoryError` が出たら、大きなリスト、文字列の連結、ループの中での確保を疑います。送信波形のバッファなど大きな領域は、`station.py` の読み込みの最初に確保しています。ほかのモジュールを先に読み込むと、ヒープが細切れになって確保に失敗することがあります
- **起動時の挙動(書き込んだあと)**: `dev.py monitor --reset -s 15` でソフトリセットし、`main.py` の出力を 15 秒読みます
- **デバイス上のファイル**: `dev.py ls`、`dev.py cat boot.py`
- **接続できない**: ほかのツール(MicroPico、Thonny、シリアルモニタ)がポートを掴んでいないか確かめます。`dev.py ports` で検出の状況を見ます

### マウント中に PC 側を切らない

マウント中、デバイスは PC へファイルを要求している間、Ctrl-C を無効にして応答を待ちます。その途中で mpremote が終わる(強制終了、ケーブルが抜ける)と、デバイスは固まり、次の接続の Ctrl-C も読み捨てられます(「could not enter raw repl」)。局の起動中は PC の `src/` から import を続けます(マウントでは `import station` に約 23 秒)。`dev.py` の `run` / `exec` / `test` は時間切れでも切らずに止めるので、mpremote を直接起動しないでください。

### 固まったとき・ウォッチドッグ

- 局(`station.main`)は、マウント中の `run` / `exec` 以外ではウォッチドッグを有効にします(起動中 16 秒、ループ 3 秒。設計は [tx-time-limit.md §4](tx-time-limit.md#4-ファームウェアが止まったとき))。有効にしたウォッチドッグは止められません
- USB から Ctrl-C で局を止めると、タイマーがフィードを続けるので、REPL で作業できます。ソフトリセットのあとは `boot.py` の `wdt_bridge` が引き継ぎます(プログラムが固まってもフィードを続けます)
- 固まったときは、`wdt_bridge` がフィードし続けるので、自然には戻りません。USB を抜き差しします。ブリッジが動いていなければ、ウォッチドッグのリセットで約 15 秒後に自然に戻ります
- ソフトリセットすると約 16 秒後にリセットされることがあるので、書き込み済みの局を触るときは、基板が再起動してもよい状態にしておきます
- **リセットを伴う試験**(`station.main()` の異常時の再起動など)は、書き込んだあとに `dev.py exec --no-mount` で行います。リセットするとマウントが切れ、`dev.py` は出力を追えません
- **局を止めたあと**: `dev.py` で接続すると、動いていた局は Ctrl-C で止まり、ソフトリセットでは `main.py` が走らないので止まったままです(LCD も消えます)。作業を終えるときは `dev.py monitor --reset -s 20` で起動し直します
- **応答しない / 起動ループ**: まず `dev.py rm-main`。それでも駄目なら USB を抜き差しします。最後の手段は BOOTSEL でファームウェアを書き直すことです

### 異常時の再起動

局が例外で止まると、内容を液晶・USB・SD に出し、待ってからハードリセットします。起動から 60 秒以内の失敗が続くと、待ち時間を 10、20、40 … 秒と延ばします(最大 300 秒、`hw/restart.py`)。失敗の回数はウォッチドッグのスクラッチレジスタに置くので、電源を切ると消えます。異常で再起動した直後は、最初のビーコンを `BEACON_RESTART_HOLD_S`(90 秒)待ちます。USB から Ctrl-C で止めたときは再起動しません。

---

## 9. BLE の開発

- 仕様は [ble-protocol.md](ble-protocol.md)(Web アプリとの取り決め、英語)。変えるときは、まずここを直します。本機の作りは [ble-control.md](ble-control.md)
- ペアリングは LCD に出る 6 桁のパスキーです。ボンドの鍵はデバイスの `/ble_bonds.json` にあります(LTK を含むので、中身をそのまま表示したり外へ出したりしないでください)。消すのは USB から、BLE を止めた状態で `Ble.forget_all()` です(本機の ER/IR は残る。[ble-control.md §5](ble-control.md#5-ble-プロトコルweb-アプリとの取り決め))
- PC からの確認: `python tools/ble_cli.py pair`(初回、パスキーは標準入力)、`hello` / `get` / `schema` / `set key=value` / `stations` / `listen -s 30` / `send`(今すぐビーコン。スイッチがオンのときだけ送る。電波が出るので `tx_enabled` を確かめてから)/ `msg send|list|cancel`
- 本機は同時に 1 台しかつながず、つながっている間は広告を止めます(スマートフォンがつながったままだと PC から見つかりません)
- **Windows の既知の問題**: 開発用の PC(Bluetooth 4.0)では、通知が続く応答の 2 回目の途中から受信が止まり、Windows の Bluetooth を入れ直すまで戻りません。本機は正常です(Android では起きない)。BLE の動作の確認は Android(nRF Connect)で行います([ble-control.md §7](ble-control.md#7-windows-で通知が止まる問題2026-11-17))
- nRF Connect(Android)から書くときは TEXT を使います(Android 版は TEXT でしか Write できません)。改行を付けられないので、行の終わりが要る確認は `ble_cli.py` か、改行で終わる書き込みのできるツールで行います
- `bluetooth.irq` のハンドラの中の `print` は失われることがあります。IRQ の中では記録だけして、ループで表示します

---

## 10. MicroPython で書くときの注意(CPython との違い)

- 時間は `time.ticks_ms()` / `time.ticks_diff(a, b)` / `time.ticks_add()` を使います(ラップアラウンドするので引き算しない)。待ちは `time.sleep_ms()`
- 定数は `from micropython import const` の `const()`
- 使えない・限られるもの: `dataclasses`、`typing`(実行時。型ヒントの構文自体は使える)、`pathlib`、`threading`(代わりに `_thread` か `asyncio`)、`f"{x=}"` などの新しい書式
- 型ヒント用の import は `try: from typing import ... except ImportError: pass` で包みます
- 例外の表示は `sys.print_exception(e)`
- 割り込みハンドラ(`Pin.irq` など)の中ではメモリを確保しません。重い処理は `micropython.schedule` に逃がします
- `@micropython.viper` のポインタ(`ptr8/16/32`)は**境界を検査しません**。書き込み先の大きさを必ず自分で守ってください(溢れるとメモリが壊れます)。PC では同じコードが添字のエラーや `array('i')` の OverflowError になるので、`test --host` で見つけられます
- viper の `ptr16` / `ptr32` の読み出しは符号なしです。符号付きの値は `array('i')` に入れて `int(p[i])` で読みます
- 標準以外のライブラリは、micropython-lib にあれば `dev.py mip <名前>` で入れます。CPython の pip パッケージは基本的に動きません

---

## 11. 確認用スクリプト

`src/` には、局の本体とは別に、部品ごとの確認用スクリプトがあります。`python tools/dev.py run src/<名前>.py -t <秒>` で実行します。`release` のイメージには入りません(`RELEASE_EXCLUDE`)。

| スクリプト | 内容 |
|------------|------|
| `tx_demo.py` | 試験ビーコンを GP3 から 5 秒おきに 12 回出す。**既定では PTT を入れず、音声だけ**。PC のライン入力につないで Dire Wolf などで復号を確かめる。`config.BENCH_TX_HZ` に周波数を明示したときだけ PTT を入れる(電波が出る。コールサインと `TX_ENABLED` も要る) |
| `rx_loopback.py` | 送信音声を受信側に戻して、送ったフレームが復号できるかを数える |
| `ui_demo.py` | 試験用の局データで画面を表示する(無線機・GNSS 不要。局はすべて架空) |
| `gnss_demo.py`、`m10_check.py` | MAX-M10M の通信と測位の確認 |
| `sdlog_demo.py` | microSD への記録の確認 |
| `lcd_test.py` | 液晶の向き・色・オフセットの確認 |
| `fm_if_*_test.py` | 455 kHz IF の FM 復調(si5351 モード用。§1) |
| `gt505_demo.py` | 旧 GNSS モジュール(GT-505)の確認 |

---

## 12. 設計メモ

| 文書 | 内容 |
|------|------|
| [ble-protocol.md](ble-protocol.md) | BLE のインターフェース仕様(Web アプリの開発者向け、英語) |
| [ble-control.md](ble-control.md) | BLE による設定変更と受信情報の読み取り(本機側の設計、APRS メッセージとチャネルの判定) |
| [tx-time-limit.md](tx-time-limit.md) | 送信時間の制限、PTT の後始末、ウォッチドッグ |
| [fx25-tx.md](fx25-tx.md) | FX.25 の送信 |
| [hw/micropython/README.md](../hw/micropython/README.md) | ファームウェアへのパッチ(BTstack のペアリング)とビルド |
| `docs/spice/` | 無線機インターフェース(`hw/ft818_if`)の送信・受信のアナログ部のシミュレーション(SPICE) |
