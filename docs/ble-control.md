# BLE による設定変更と受信情報の読み取り

## 1. 方針

オーナーが決めたこと(2026-11-16)。

- スマートフォン(**Android のみ**)から、BLE で **Pico の設定変更**と**受信情報の読み取り**を行う。アプリは **Web Bluetooth を使う Web アプリ**で、**別のリポジトリ**で開発する
- **このリポジトリの範囲は、実機と PC の接続検証まで**。PC 側の検証ツールは `tools/ble_cli.py`(bleak)。Web アプリとの取り決め(インターフェース仕様)は [ble-protocol.md](ble-protocol.md)
- スマホから **TX_ENABLED を変えてよい**
- 送信の可否は今と変わらない。**物理スイッチ(GP21)がオン**で、かつ `core/txgate.py` のゲートが許可したときだけ送る
- 2026-11-19 追加: スマホから**今すぐビーコンを送る**操作(`beacon.send`)と、自動では送らない**手動のモード**(設定 `beacon_mode`)。送信の条件は上と同じで、スイッチの条件に AND で足しただけ(スイッチがオフなら断る)。測位と時刻が無ければ断る。前回の送信から `BEACON_MANUAL_MIN_S`(10 秒)は断る(連打の防止)。指示は自動のモードでも使え、送った時点から自動の次回を数え直す
  - `Station.send_now()` が `remote.handle` の中で(`step()` の中で)その場で送り、送り終えてから応答する(`settings.set` と同じ作り。応答の順序が保たれ、送り待ちの状態を持たない)。送信中の例外は `_fatal` に置き、`step()` が投げ直して局を再起動する(BLE の except で消さない)
- ペアリングは **LCD に表示する 6 桁のパスキー**を使う(MITM 保護つきのボンディング)
- 受信履歴は**接続中に表示するだけ**。スマホ側では保存しない。本機は、接続したときに局の表(`core.stations`)の要約を返す
- 動作モードは `"tnc"` だけが対象。`"si5351"` は未完成なので BLE を起動しない

範囲外: ビーコン以外の送信(任意のパケットやテキスト)、地図、APRS メッセージ、KISS over BLE、BLE 経由のファームウェア更新とファイル転送。

## 2. 前提の確認(実測、2026-11-16)

- ファームウェア `RPI_PICO2_W_ROMFS2`(MicroPython 1.29)で `bluetooth.BLE()` が使える
- `BLE.active(True)` で MicroPython のヒープは約 100 バイトしか減らない(435,136 → 435,024 バイト)。BTstack はヒープの外に領域を取る
- 本機側の MTU 上限は 1017(`config('mtu')`)。Android の Chrome は通常 517 で交渉する
- CYW43 が使う GP23/24/25/29 は、今の配線(GP0–3, 6–12, 15–22, 26)と重ならない
- `Settings.set()` 1 回(フラッシュへの書き込み 1 回)は 8–58 ms(5 回の実測。長いのは消去が入ったとき)

未確認(§6 の検証項目): 2.4 GHz の電波による GP26 受信への回り込み、フラッシュ書き込み中に受信がどれだけ欠けるか(書き込み自体は上の時間)。

実測(2026-11-17、パッチ済みファームウェア v3):

- 電源投入直後の最初の `active(True)` は 842 ms(CYW43 に BT のファームウェアを読み込む)。2 回目以降とソフトリセット後は 20 ms
- Windows のパスキー入力は `tools/ble_cli.py pair` でできる(WinRT のカスタムペアリング。pywinrt 3.x では `accept_with_pin(pin)`)。Windows は接続から約 1 秒で MTU 517 を交換する。Android の nRF Connect は要求しない限り 23 のまま

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
| `beacon_mode` | str | `"auto"`(規則で自動に送る)/ `"manual"`(`beacon.send` のときだけ)。schema では `choices` | `BEACON_MODE` |
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

- GATT は Nordic UART Service と同じ UUID。1 行 1 つの JSON + `\n`。行の組み立てと分割は `hw/ble.py`、
  中身の処理は `core/remote.py`
- 通知 1 つは MTU − 3 バイト、ただし最大 244 バイト(`hw/ble.py` の `NOTIFY_MAX`。データ長拡張のリンク層 1 パケット)
- 送っている途中(`write` の中)で切断の IRQ が来ると `Remote.detach()` が送り待ちを空にする。`Remote._flush()` は
  write のあと `attached` を確かめてから送り待ちを進める(2026-11-17 に Windows との試験で `IndexError` が出て BLE が止まった)
- ペアリングは DisplayOnly、`bond=True, mitm=True, le_secure=True`。鍵は `/ble_bonds.json`
  (`_IRQ_GET_SECRET` / `_IRQ_SET_SECRET`)、4 台まで。鍵の消去は USB から、BLE を止めた状態で
  `Ble.forget_all()`(本機の ER/IR は残す。BLE が動いている間は False を返して何もしない。
  BTstack が起動時に読んだ表とずれるため)
- MITM と LE Secure の両方を要求するので、パッチ済みファームウェアは LE Secure Connections の
  パスキー/数値比較だけを受ける(旧方式と Just Works は断る。hw/micropython/README.md)
- 認証された接続になったら `Remote.attach()`、切れたら `detach()`。受ける 1 行は `remote.LINE_MAX`(512)まで
- つながってから 60 秒(`hw/ble.py` の `AUTH_TIMEOUT_MS`)たっても認証されなければ、本機から `gap_disconnect` する。
  接続中は広告を止めるので、放っておかれた接続(ペアリングを始めない、パスキーを打たない)があると
  ほかの端末から見つからなくなるため(2026-11-18、issue #5)。切断のイベントで広告を再開する
- 行の組み立てのバッファは、接続、切断、認証のたびに消す。時間では捨てない。空行は無視する
  (アプリは書き込みに失敗したら `\n` を 1 つ送って立て直す。仕様書 §5.3、issue #1)
- 局の表(`core.stations`)は、局とオブジェクト/アイテムを別の件にする(キーは局が `"Q1AAA-9"`、
  オブジェクトが `("obj", 名前)`。架空の例)。局と同じ名前のオブジェクトを同じ件にしていた(issue #6)。
  `rx` には `obj`(名前)、`station` には `obj: true` を付ける
- 局のループの `step()` で `Remote.poll()` を呼ぶ(送り待ちを Notify に流し、status を送る)

## 6. このリポジトリでの作業と受け入れ条件

| # | 作業 | 完了の条件 |
|---|---|---|
| 1 ✅ | `core/settings.py` と §4.3 の変更 | host テストで検証、保存、読み込み、優先順位、全部か無しかを確かめる。実機の `run` で `/settings.json` から起動する |
| 2 ✅ | `core/remote.py` | ble-protocol.md §7〜§8 のすべての `op` と `err` を、偽の送受信で host テストする |
| 3 ✅ | `hw/ble.py` | 広告、NUS、行の分割と組み立て、パスキー、鍵の保存。実機の `test_hw_ble_*` |
| 4 ✅ | `station.py` へのつなぎ込み、LCD 表示 | `status`、`rx`、`tx` のイベントが出る。BLE があってもウォッチドッグ(3 秒)に余裕がある |
| 5 (一部) | `tools/ble_cli.py` | PC からペアリングし、`hello`、`status`、`settings.get/set`、`stations.list` が使え、`rx` が流れる。ペアリングと各コマンドは確かめた。この PC では §7 の問題で通しの受け入れ試験ができていない |
| 6 | 受信性能の確認 | 同じ信号源で、BLE を止めた場合、広告中、接続中に通知が流れている場合の受信数を比べ、劣化がないか、許容できる程度であること |
| 7 | 書き込み中の受信の欠け | `settings.set` の書き込み時間と、Core1 が止まる時間を測る |

受け入れ試験(5 が完了の条件): PC から接続し、パスキーでペアリングする → `settings.set` でコメントを変える → 次のビーコン(dry run で可)に反映される → 電源を入れ直しても残っている → `rx` が受信のたびに届く。

実機の試験は偽の PTT ピンで行い、本物の PTT は入れない(CLAUDE.md の「PTT の扱い」)。

## 7. Windows で通知が止まる問題(2026-11-17)

開発用の PC(Windows 11、**Bluetooth 4.0** のアダプター)と bleak で起きた。Web アプリ向けには ble-protocol.md §10.1(U8)。

- 症状: 1 回の要求に通知 1 つの応答なら何度でも通る。通知を続けて送る応答(`settings.schema` など)は、1 回目は全部届くが、2 回目の途中から届かなくなる。その後、Windows は本機からの ATT を処理しない(本機は MTU の交換に応答しているのに、Windows の MTU は 23 のまま。30 秒後に Windows が切断する)。本機の再起動や USB の抜き差しでは直らず、**Windows の Bluetooth を入れ直すと直る**
- 実験(一時的な応答役を本機で動かし、大きさと数を変えた): 244 バイト x 5、200 バイト x 10、20 バイト x 56 の通知、通知の間に 50 ms、購読から 10 秒待ってから、本機の MTU を 23 に抑える。どれも Windows では止まった(MTU 23 では 1 回目から、102 通知のうち 70 しか届かず、行の途中が欠けた)
- **Android(nRF Connect)では MTU 23 でも 517 でも、2 回目以降も全部届いた**。本機の `gatts_notify` は失敗を返していない。本機は正しく送っていて、PC のアダプターかドライバーが取りこぼしていると判断した
- 次の確認: BT 5.x の USB ドングルで同じ試験をする。それまで Web アプリの試験は Android で行う
- 試験のときの Bluetooth の入れ直しは、WinRT の `Windows.Devices.Radios`(`Radio.set_state_async`)でスクリプトからできる。PC の Bluetooth 機器も一度切れる
- 調べる途中で見つけた、ファームウェアの気になる点(今回の原因ではない): pico-sdk 2.3.0 の `cyw43_btbus_write()` は `cybt_hci_write_buf()` の `CYBT_ERR_QUEUE_FULL` を無視して 0 を返す。CYW43 への共有バスのリングバッファ(4 KB)が満杯だと、パケットを黙って捨てる

## 8. APRS メッセージとチャネルの判定(2026-11-20)

仕様(アプリとの取り決め)は ble-protocol.md §7.6〜§7.9、§8.8。本機の作り:

| モジュール | 役割 | テスト |
|---|---|---|
| `aprs/message.py` | 情報フィールド(`:ADDRESSEE:text{NO`、`ack`、`rej`)の組み立てと値の検査(`MessageError`) | host |
| `aprs/packet.py` | 受信の解析。APRS 1.1 の reply-ack(`{MM}AA`、`ackMM}AA`)の `}` の前を番号に、後ろを `replyack` に | host |
| `core/messages.py` | 送信待ちの列(最大 4 件)、再送(30/60/120/240/240 秒、5 回で `failed`)、ack/rej の照合、自局宛への ack の列(送れなければ 60 秒で捨てる)、重複の判定(最近 32 件)、一覧(16 件、pending は消さない)、未読の数 | host |
| `core/msgfile.py` | `/sd/messages.txt` に一覧を書き直す(変わってから 5 秒、終了時)。最初のマウントで読み戻し、pending は `stopped` | host(偽のファイル) |
| `core/level.py` の `Channel` | 受信音量の実効値がしきい値 `config.CH_BUSY_DB` 以上なら使用中。下回ってから 0.5 秒で空き、さらに 0〜1 秒の乱数。使用中が 30 秒続いたら開きっぱなしとみなす。復号したパケットも使用中の印にする | host |
| `station.py` | `_messages()`: スイッチ・ゲート・チャネル・前回の送信から 3 秒(`MSG_GAP_MS`)がそろえば、ack を先に 1 ステップ 1 フレーム送る。自動のビーコンも同じチャネルの判定で待つ(dry run と `beacon.send` は待たない)。送信は `_transmit()` に共通化 | host(偽のハードウェア、`test_tx_safety`) |
| `ui/` | 自局宛を受けたら `MSG_SHOW_S`(30 秒)全画面で出す(パスキーが優先)。状態の欄に `MSG n`(未読)/ `MSG>`(送信中) | host、実機(`test_hw_ui_*`) |

- メッセージは dry run にしない。ゲートが拒否している間は送らずに待つ(ack の来ない dry run を作っても意味がない)
- 送れない間(スイッチがオフ、ゲートが拒否、チャネルが使用中)は、期限の来た送信は回数を数えずに待つ
- 番号は SD のファイルの `next` から続ける。SD を読む前に送ったときと SD が無いときは、起動時の乱数(100〜999)から
- いちばん長いメッセージ(宛先 9 文字、本文 67 文字、経路 2 つ、FX.25)は 1.34 秒で、送信波形のバッファ(1.5 秒)に収まる(`test_longest_message_fits_tx_buffer_with_fx25`)
- **未確定**: `CH_BUSY_DB`(仮に -30 dBFS)。スケルチを閉じた/開けた/信号を受けたときの `rx level rms` を測って決める(ble-protocol.md U9)
