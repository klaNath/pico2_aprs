# プロジェクト: Raspberry Pi Pico 2 上の MicroPython アプリ

このファイルは Claude Code が毎回読む作業ルールです。人間が読んでも分かるように書いています。

## ターゲット

- ボード: Raspberry Pi Pico 2(RP2350, Arm Cortex-M33 デュアルコア, SRAM 520KB, フラッシュ 4MB)
- ファームウェア: MicroPython(正確なバージョンは `python tools/dev.py info` で確認する)
    - カスタムされたファームウェアを使用。ROMFSを1MB設定。標準のライブラリに加えてmicropython-libから `sdcard` と `datetime` を読み込みビルドしている
    - ROMFS が有効であるため、必要ならばライブラリなどをROMFSへDeployし高速化を図ってください
- オンボード LED は `Pin("LED", Pin.OUT)`
- PC とは USB シリアルで接続。操作は全て `tools/dev.py`(mpremote のラッパー)経由
- 自局のコールサインを記入する欄には、デフォルトで `JK1BBJ` と入力すること

## ディレクトリ構成

- `src/` … デバイスに載るコード。`main.py` は起動時に自動実行されるエントリポイント
    - `src/aprs/` … APRS の解析と Mic-E エンコード(自作)
    - `src/tnc/` … micro-aprs 由来のコード(**生成物。手で編集しない**、下記「micro-aprs 由来のコード」)
- `tests/` … テスト(`test_*.py` の `test_*` 関数)。`_runner.py` はランナー。実機でも PC でも動く。実機専用のテストは `test_hw_*` と名付ける(PC ではスキップ)
- `tools/dev.py` … ビルド・実行・デバッグ用 CLI(PC 側で動く通常の Python)
- `tools/vendor.py` … `third_party/micro-aprs/src/pico2/` から `src/tnc/` を生成する
- `third_party/micro-aprs/` … サブモジュール(フォーク klaNath/micro-aprs、ブランチ `pico2-subset`、GPL-3.0)
- `typings/` … MicroPython の型スタブ(自動生成、編集しない)

## micro-aprs 由来のコード(出典の追跡)

- 派生コードの変更は必ず `third_party/micro-aprs/src/pico2/` で行い、サブモジュール側でコミットしてから `python tools/vendor.py` で `src/tnc/` を再生成する。試行錯誤中は `--dirty` で未コミットのまま生成できる(ヘッダに `+dirty` と出る)。
- 派生ファイルの先頭には出典(上流のファイルとコミット)と変更点を書く。
- `lint` は `vendor.py --check` で `src/tnc/` が古くないかも確認する。
- フォークへの push はユーザーが行う。本体より先にフォークを push しないと、サブモジュールの参照先が存在しない状態になる。

## 絶対に守ること

1. **デバイス操作は必ず `python tools/dev.py <cmd>` を使う。** `mpremote` を直接、特に引数なしや `repl` で起動しない(対話モードに入って固まる)。`dev.py repl` も使わない。
2. **実行には必ずタイムアウトを付ける**(`-t 秒`)。無限ループのプログラムがタイムアウトで終わるのは正常(終了コード 124)。
3. **`deploy` / `romfs-deploy`(フラッシュへの書き込み)、`rm-main`、`mip` はユーザーに確認してから。** 開発中の動作確認は `run` / `test` で十分(フラッシュに書かずに PC 上の `src/` をマウントして実行する)。
4. **物理的な結果は見えない。** LED・モーター・センサー値の実際の振る舞いは、print 出力で確かめるか、ユーザーに目視確認を頼む。推測で「動いた」と報告しない。
5. 配線を変えるような指示を出すときは、ピン番号と接続先を明記し、電源(3.3V 系、GPIO は 5V 非耐性)に注意を促す。

## 開発ループ

```
python tools/dev.py lint              # 1. vendor --check + ruff + pyright(ホスト側、デバイス不要)
python tools/dev.py test --host       # 2a. PC(CPython)上で tests/ を実行(速い。test_hw_* はスキップ)
python tools/dev.py test              # 2b. デバイス上で tests/ を実行
python tools/dev.py run -t 15         # 3. src/main.py を実行して出力を見る
python tools/dev.py run src/xxx.py -t 10   # 個別スクリプトの実行
python tools/dev.py exec "import app; print(app.Blinker)" -t 10   # 対話の代わりのコード片実行
python tools/dev.py check             # lint + test --host + test をまとめて
python tools/afsk_bench.py -n 200     # 復調器の劣化条件ベンチ(PC のみ、数分かかる。モデムを変えたら回す)
```

変更後は少なくとも `lint` と `test` を通してから完了を報告する。

## 書き込み方式: deploy と romfs-deploy

- `deploy`: `src/` を .py のままフラッシュのファイルシステムに書く。シンプルで、デバイス上で直接編集もできる。
- `romfs-deploy`: `src/` を .mpy にコンパイルした読み出し専用イメージを ROMFS 区画に書き、`/rom` から実行する。import が速く RAM 消費が少ない。フラッシュには `/rom` の `_romfs_main` を呼ぶランチャー `main.py`(と `boot.py`)だけが残る。
  - 使う前に `romfs-query` で区画があるか確認する。区画が無いファームウェアでは使えない。
  - .mpy 化は dev.py が `mpy-cross -march=armv7emsp` で行う(viper/native を含むため。mpremote 任せだと失敗する)。元のパスを埋め込むので、トレースバックには `src/xxx.py`(`_romfs_main` は `src/main.py`)と出る。ソースは表示されないので、ローカルの該当行を見る。
  - 開発中の `run` / `test` は従来どおり PC の `src/` をマウントして動く(マウントが優先される)ので、ROMFS の中身に影響されない。
  - `deploy` を実行するとフラッシュの `main.py` が上書きされ、フラッシュ版に戻る。

## デバッグの手順

- **トレースバック**: `File "/remote/app.py", line N` の `/remote/` はマウントされた `src/` のこと。そのまま `src/app.py` の N 行目を見る。
- **状態を覗く**: `dev.py exec "<コード>"` でモジュールを import して関数を呼ぶ・変数を print する。REPL の代わりにこれを使う。
- **メモリ**: `import gc; gc.collect(); print(gc.mem_free())` を怪しい箇所の前後で出す。`MemoryError` が出たら大きなリスト/文字列連結/ループ内での確保を疑う。
- **起動時の挙動(deploy 後)**: `dev.py monitor --reset -s 15` でソフトリセットして main.py の出力を 15 秒読む。
- **デバイス上のファイル**: `dev.py ls`, `dev.py cat boot.py`。
- **接続できない**: 他ツール(MicroPico、Thonny、シリアルモニタ)がポートを掴んでいないかユーザーに確認。`dev.py ports` で検出状況を見る。
- **応答しない / 起動ループ**: まず `dev.py rm-main`(要確認)。それでも駄目ならユーザーに USB の抜き差しを頼む。最終手段は BOOTSEL でファームウェア再書き込み(ユーザー作業)。

## MicroPython で書くときの注意(CPython との違い)

- 時間は `time.ticks_ms()` / `time.ticks_diff(a, b)` / `time.ticks_add()` を使う(ラップアラウンドするので引き算しない)。待ちは `time.sleep_ms()`。
- 定数は `from micropython import const` の `const()`。
- 使えない/限定的: `dataclasses`, `typing`(実行時。型ヒントの構文自体は OK)、`pathlib`, `threading`(代わりに `_thread`、または `asyncio`)、`f"{x=}"` などの新しい書式。
- 型ヒント用の import が必要なら `try: from typing import ... except ImportError: pass` で包む。
- 例外の表示は `sys.print_exception(e)`。
- 割り込みハンドラ(`Pin.irq` など)の中ではメモリ確保をしない。重い処理は `micropython.schedule` に逃がす。
- `@micropython.viper` のポインタ(`ptr8/16/32`)は**境界検査をしない**。書き込み先の大きさを必ず自前で守る(溢れるとメモリ破壊)。PC では同じコードが配列の添字エラー/`array('i')` の OverflowError になるので、`test --host` で検出できる。
- viper の `ptr16`/`ptr32` の読み出しは符号なし扱い。符号付きの値は `array('i')` に入れて `int(p[i])` で読む。
- 標準以外のライブラリは micropython-lib にあれば `dev.py mip <名前>`(要確認)。CPython の pip パッケージは基本的に動かない。
- ハードウェア依存部分(Pin, I2C, SPI など)はクラスの引数として渡し、テストでは偽オブジェクトに差し替えられるようにする(`src/app.py` と `tests/test_app.py` が例)。

## 配線メモ(ユーザーが追記する)

| 用途 | GPIO | 接続先 | 備考 |
|------|------|--------|------|
| オンボード LED | LED (GP25) | — | |
| オーディオ出力 | GP03 | 出力アナログフロントエンド | |
| PTTスイッチ | GP02 | — | |
| オーディオ入力 | GP26 | 入力アナログフロントエンド | |
