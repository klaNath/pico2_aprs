# BLE による設定変更と受信情報の読み取り

## 1. 方針

オーナーが決めたこと(2026-11-16)。

- スマートフォン(**Android のみ**)から、BLE で **Pico の設定変更**と**受信情報の読み取り**を行う。アプリは **Web Bluetooth を使う Web アプリ**で、**別のリポジトリ**で開発する
- **このリポジトリの範囲は、実機と PC の接続検証まで**。PC 側の検証ツールは `tools/ble_cli.py`(bleak)。Web アプリとの取り決め(インターフェース仕様)は [ble-protocol.md](ble-protocol.md)
- スマホから **TX_ENABLED を変えてよい**
- 送信の可否は今と変わらない。**物理スイッチ(GP21)がオン**で、かつ `core/txgate.py` のゲートが許可したときだけ送る。スマホから送信を指示する操作(今すぐビーコンを送る等)は第 1 段階では作らない。将来作る場合も、この条件を AND で足すだけにする
- ペアリングは **LCD に表示する 6 桁のパスキー**を使う(MITM 保護つきのボンディング)
- 受信履歴は**接続中に表示するだけ**。スマホ側では保存しない。本機は、接続したときに局の表(`core.stations`)の要約を返す
- 動作モードは `"tnc"` だけが対象。`"si5351"` は未完成なので BLE を起動しない

範囲外: スマホからの送信操作、地図、APRS メッセージ、KISS over BLE、BLE 経由のファームウェア更新とファイル転送。

## 2. 前提の確認(実測、2026-11-16)

- ファームウェア `RPI_PICO2_W_ROMFS2`(MicroPython 1.29)で `bluetooth.BLE()` が使える
- `BLE.active(True)` で MicroPython のヒープは約 100 バイトしか減らない(435,136 → 435,024 バイト)。BTstack はヒープの外に領域を取る
- 本機側の MTU 上限は 1017(`config('mtu')`)。Android の Chrome は通常 517 で交渉する
- CYW43 が使う GP23/24/25/29 は、今の配線(GP0–3, 6–12, 15–22, 26)と重ならない
- `Settings.set()` 1 回(フラッシュへの書き込み 1 回)は 8–58 ms(5 回の実測。長いのは消去が入ったとき)

未確認(§6 の検証項目): 2.4 GHz の電波による GP26 受信への回り込み、フラッシュ書き込み中に受信がどれだけ欠けるか(書き込み自体は上の時間)、Windows で passkey を入力する手順。

## 3. 構成

```
[Web アプリ(Chrome/Android)] ─ BLE (NUS) ─ hw/ble.py ─ core/remote.py ─┬─ core/settings.py ─ /settings.json
[tools/ble_cli.py(PC, bleak)]                                           └─ station.Station(状態、受信、局の表)
```

| モジュール | 役割 | テスト |
|---|---|---|
| `core/settings.py` | 設定項目の定義(型、範囲)、検証、`/settings.json` の読み書き、`config` への反映 | host |
| `core/remote.py` | 1 行の JSON コマンドを処理し、応答とイベントを作る。BLE には依存しない | host(偽の送受信) |
| `hw/ble.py` | 広告、NUS の GATT、行の分割と組み立て、ボンディング、パスキー、鍵の保存 | 実機(`test_hw_*`) |
| `station.py` | 受信と状態の変化を `remote` に渡し、`step()` で BLE のキューを処理する。LCD に BLE の状態とパスキーを出す | 既存の方法 |
| `tools/ble_cli.py` | PC から検索、接続、ペアリング、コマンドの送信、イベントの表示を行う | 手動、受け入れ試験 |

### 3.1 本機のループとの関係

- 局は同期的な `step()` で回り、ウォッチドッグは 3 秒。そのため `aioble`(asyncio が前提)は使わず、低レベルの `bluetooth.BLE.irq` を使う
- IRQ ハンドラではメモリを確保しない。受け取った書き込みは事前に確保した行バッファに足すだけにし、行がそろったら印を付ける。JSON の解析と応答は `step()` の中で行う
- Notify の送信は `step()` から行う。送れない(BTstack のキューが満杯)ときは次の `step()` で再び送る。イベントのキューは上限を決め、溢れたら古い `rx` から捨てて、捨てた数を次の `status` で知らせる
- 受信(Core1)には触らない。BLE の処理はすべて Core0 で行う

## 4. 設定の永続化

### 4.1 優先順位

`config.py`(既定値) < `config_local.py`(開発用、git 管理外) < `/settings.json`(スマホから変更)

- `/settings.json` はフラッシュのファイルシステムに置く。ROMFS は読み出し専用なので使えない。`src/` には置かないので、`deploy` / `romfs-deploy` でも消えない
- スマホから変えられるのは §4.2 の項目だけ。`settings.json` に未知のキーや範囲外の値があれば、その項目を無視して既定値を使い、起動ログに出す
- `settings.reset` で `/settings.json` を消し、`config.py` + `config_local.py` の値に戻す

### 4.2 スマホから変えられる項目(第 1 段階)

すべて、実行中に反映する(再起動は不要)。

| キー | 型 | 範囲 / 書式 | 元の config |
|---|---|---|---|
| `callsign` | str または null | 英大文字と数字 3–6 文字。null は未設定(送信しない) | `CALLSIGN` |
| `ssid` | int | 0–15 | `SSID` |
| `symbol` | str | 2 文字(シンボル表 + シンボル) | `SYMBOL` |
| `path` | list[str] | 0–2 個。`WIDE1-1` の形 | `TX_PATH` |
| `comment` | str | 印字可能な ASCII、最大 40 文字 | `BEACON_COMMENT` |
| `mice_message` | int | 0–6(`aprs.mice.MESSAGES`)。7(Emergency)はスマホからは選べない(誤操作で緊急を送らないため) | `BEACON_MESSAGE` |
| `beacon_moving_s` | int | 30–3600。**移動しているとき**のビーコン間隔(秒) | `BEACON_MOVING_S` |
| `beacon_still_s` | int | 60–7200。**移動していないとき**のビーコン間隔(秒) | `BEACON_STILL_S` |
| `beacon_move_m` | float | 1–1000。移動しているとみなす距離(m) | `BEACON_MOVE_M` |
| `tx_enabled` | bool | | `TX_ENABLED` |
| `fx25_tx` | bool | | `FX25_TX` |

ピン、MODE、LCD、無線機の立ち上がり時間などは出さない(間違えると復旧に USB が要るため)。

### 4.3 実行中の反映に必要な変更

今は、次の値をオブジェクトを作るときに一度だけ読んでいる。値を変えるための口を足す。

- `TxGate` の `callsign` と `allowed`(`hw/txctl.py` が `config.CALLSIGN` と `config.TX_ENABLED` を渡している)
- `Beacon` の間隔と移動距離(`station.py:121`)
- `Station.call`(`display_call` の結果、`station.py:97`)と FX.25 の有効/無効(`station.py:94`)
- `_beacon()` が毎回読む `TX_PATH`、`SYMBOL`、`BEACON_COMMENT`、`BEACON_MESSAGE` は、`config` の属性を書き換えれば反映される

送信中に設定は変えない。`set` は `step()` の中で処理するので、送信の途中には割り込まない。

### 4.4 書き込み

- 一時ファイル(`/settings.json.tmp`)に書いてから名前を変える。途中で電源が落ちても、古いか新しいかのどちらかが残る
- フラッシュに書いている間は XIP が止まり、Core1 の受信も止まる見込み。そのため書き込みは `settings.set` 1 回につき 1 回だけにする(複数の項目をまとめて送る)
- 書き込みの前にウォッチドッグをフィードする

## 5. BLE プロトコル(Web アプリとの取り決め)

**正は [ble-protocol.md](ble-protocol.md)**(Web アプリ向けのインターフェース仕様。確定・暫定・未確定の区別つき)。
ここには本機の作りに関わる要点だけを残す。

- GATT は Nordic UART Service と同じ UUID。1 行 1 つの JSON + `
`。行の組み立てと分割は `hw/ble.py`、
  中身の処理は `core/remote.py`
- ペアリングは DisplayOnly、`bond=True, mitm=True, le_secure=True`。鍵は `/ble_bonds.json`
  (`_IRQ_GET_SECRET` / `_IRQ_SET_SECRET`)、4 台まで。鍵の消去は USB から
  (`dev.py exec "import hw.ble; hw.ble.forget_all()"`)
- 認証された接続になったら `Remote.attach()`、切れたら `detach()`。受ける 1 行は `remote.LINE_MAX`(512)まで
- 局のループの `step()` で `Remote.poll()` を呼ぶ(送り待ちを Notify に流し、status を送る)

## 6. このリポジトリでの作業と受け入れ条件

| # | 作業 | 完了の条件 |
|---|---|---|
| 1 ✅ | `core/settings.py` と §4.3 の変更 | host テストで検証、保存、読み込み、優先順位、全部か無しかを確かめる。実機の `run` で `/settings.json` から起動する |
| 2 ✅ | `core/remote.py` | ble-protocol.md §7〜§8 のすべての `op` と `err` を、偽の送受信で host テストする |
| 3 | `hw/ble.py` | 広告、NUS、行の分割と組み立て、パスキー、鍵の保存。実機の `test_hw_ble_*` |
| 4 | `station.py` へのつなぎ込み、LCD 表示 | `status`、`rx`、`tx` のイベントが出る。BLE があってもウォッチドッグ(3 秒)に余裕がある |
| 5 | `tools/ble_cli.py` | PC からペアリングし、`hello`、`status`、`settings.get/set`、`stations.list` が使え、`rx` が流れる |
| 6 | 受信性能の確認 | 同じ信号源で、BLE を止めた場合、広告中、接続中に通知が流れている場合の受信数を比べ、劣化がないか、許容できる程度であること |
| 7 | 書き込み中の受信の欠け | `settings.set` の書き込み時間と、Core1 が止まる時間を測る |

受け入れ試験(5 が完了の条件): PC から接続し、パスキーでペアリングする → `settings.set` でコメントを変える → 次のビーコン(dry run で可)に反映される → 電源を入れ直しても残っている → `rx` が受信のたびに届く。

実機の試験は偽の PTT ピンで行い、本物の PTT は入れない(CLAUDE.md の「PTT の扱い」)。
