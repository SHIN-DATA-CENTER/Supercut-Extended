# Supercut Extended

Outplayed の Supercut を **GPU (NVENC)** で作り直す外部ツール。
イベント検出と録画は Outplayed のものをそのまま使い、**結合・エンコードの工程だけを置き換える**。

---

## 背景

Outplayed は録画も手動エクスポートも既に GPU を使っているのに、**Supercut だけが CPU の libx264**
で処理している。`ffprobe` の `TAG:encoder` で経路ごとに確認した結果:

| 経路 | エンコーダ | |
|---|---|---|
| ゲーム録画（OBS） | `obs_nvenc_h264_tex` | GPU |
| **Supercut / montage** | **`libx264`** | **CPU** ← 問題箇所 |
| EZ Edit 手動エクスポート | `h264_nvenc` | GPU |

さらに Supercut の出力は `High 4:4:4 Predictive` (yuv444p) になっている。
`-pix_fmt` を指定し忘れた事故と思われ、4:4:4 は 4:2:0 より遅く・大きく・互換性も低いので、
遅さを二重に悪化させている。

そして実際に破綻していた。`background.html.log` より:

```
11:00:43  createMontage  25 segments / 687.7s 要求
11:05:43  *** TIMEOUT ***          ← 5分で打ち切り
11:05:45  返却
```

出力の実尺は **317.95s** — 要求した 687.7s の **46%** しかなく、**完走していない**。

### 実測比較（同一の 25 セグメント / 687.7s、同じ PC）

| | 所要時間 | 結果 |
|---|---|---|
| Outplayed (libx264 / CPU) | 302s | **失敗** — 317.95s で打ち切り |
| 本ツール `GPU re-encode` | **160s** | 687.84s 完走 / 1498 MB |
| 本ツール `Stream copy` | **5.7s** | 702.16s 完走 / 894 MB / 無劣化 |

---

## 使い方

### exe 版（ビルド済み）

<<<<<<< HEAD
`dist\` の中の **`SupercutExtended.exe`** を起動するだけ。
=======
**`SupercutExtended.exe`** を起動するだけ。
※前提としてOutPlayedを利用していること。
>>>>>>> 57aa2c36a71f8212440bb6a0404432b9981a5b44

| ファイル | 大きさ | 用途 |
|---|---|---|
| `SupercutExtended.exe` | 43 MB | GUI 本体 |
| `SupercutExtended-cli.exe` | 43 MB | コマンドライン版（`... list` など） |

<<<<<<< HEAD
**それぞれ 1 ファイルで完結している。** Python も PySide6 も exe の中に入っているので、
Python のインストールも、隣に置くフォルダも要らない。どこにコピーしても単体で動く。
2 つは独立しているので、GUI しか使わないなら `SupercutExtended.exe` だけ持っていけばよい。

**ただし ffmpeg だけは別**（サイズと GPL 再配布の都合で同梱していない）。PATH に ffmpeg が
あればそのまま動く。無い PC に持っていく場合は `ffmpeg.exe` と `ffprobe.exe` を **exe と同じ
フォルダ**に置けば認識する（`ffmpeg\` や `bin\` サブフォルダでも可）。見つからない場合は
起動時にその旨を表示する。

=======
>>>>>>> 57aa2c36a71f8212440bb6a0404432b9981a5b44
自分でビルドし直す場合:

```bash
pip install pyinstaller
python -m PyInstaller supercut.spec --noconfirm --clean
```

#### 単体 exe とフォルダ配布の切り替え

`supercut.spec` 冒頭の `ONEFILE` で出力の形を切り替えられる。

| 設定 | 出力 | 起動（GUI が出るまで） |
|---|---|---|
| `ONEFILE = True`（既定） | exe 2 つだけ | **約 9 秒** |
| `ONEFILE = False` | exe + `_internal\`（693 ファイル） | **約 2.5 秒** |

単体 exe は起動のたびに中身を `%TEMP%` に展開するため、この PC の実測で **6 秒ほど遅い**
（4 回平均: 8.95 秒 対 2.52 秒）。持ち運びやすさを取るか起動の速さを取るかで選ぶ。

exe に埋め込むアイコン `assets/` は**リポジトリに含めていない**（`cooliocns SVG/` と同じ扱い）。
`supercut.spec` が `assets/app.ico` を参照するので、**クローン直後は先にアイコンを生成する**:

```bash
python tools/make_icon.py     # assets/app.ico を生成（同梱のアイコンセットから描く）
```

これを飛ばすとビルドがアイコン不在で止まる。生成後はデザインを変えたときだけ再実行すればよい。
なお `.ico` が無くてもアプリ自体は動く（ウィンドウアイコンはグリフにフォールバックする）。

### ソースから動かす

```bash
pip install -r requirements.txt
python supercut.py            # GUI
```

### 画面の使い方

- 左のリストから試合を選ぶと、**動画がプレビュー表示**される
- 動画の下のタイムラインが**シークバー兼イベント表示**になっている
  - 青い帯 = 実際に書き出されるセグメント
  - 縦線 = 検出されたイベント（色はイベント種別）
  - クリック / ドラッグでシーク、マウスを乗せると何のイベントか出る
- `◀◀` `▶▶` で前後のイベントへジャンプ
- 「対象イベント」「クリップの長さ」を変えるとタイムラインが即座に更新されるので、
  **書き出す前にカット位置を確認できる**
- 表示メニューから日本語 / English を切り替え（次回起動時に反映）

#### 複数の試合をまとめて出力する（v1.0.1〜）

一覧の左端のチェック欄で複数の試合を選べる。**チェックが 1 つも無いときは、
プレビュー中の試合だけが対象**になるので、1 試合だけ作るときの操作は今までと変わらない。

- チェックは**検索で絞り込んでも保持される**（`全選択` / `解除` は表示中の行だけに効く）
- 2 件以上チェックすると「複数選択したとき」のラジオが有効になる
  - **1本にまとめる** — 選んだ試合を**時系列順**につないで 1 ファイルにする
  - **試合ごとに別ファイル** — 試合ごとに 1 ファイルずつ書き出す。保存先はフォルダ指定に変わる
- チェックした行を切り替えてもプレビューは別扱いなので、**選択を崩さずに各試合を見て回れる**

CLI も同じ core を使う:

```bash
python supercut.py list                          # 試合一覧
python supercut.py events 1                      # イベント詳細
python supercut.py build 1 --events kill --dry-run
python supercut.py build 1 --events kill,ace --pre 8 --post 1
python supercut.py build 1 --events kill --mode copy
python supercut.py build 3 5 8 --events kill     # 3 試合を 1 本にまとめる
python supercut.py build 3 5 8 --separate        # 試合ごとに 1 本ずつ
python supercut.py encoders                      # 使えるエンコーダを実測
```

`build` の番号は `list` の行番号だが、**録画が増えると行番号はずれる**。
確実に指定したいときは試合 ID の先頭数文字を渡す（`build 6677164a` など）。

### 主なオプション

| オプション | 説明 |
|---|---|
| `--events` | `kill,ace,knockdown,death,assist,victory,...` |
| `--pre` / `--post` | 前後の秒数。未指定なら **Outplayed のイベント別デフォルト**を使う |
| `--gap` | この秒数以内に近接したセグメントも結合する |
| `--mode` | `encode`（既定 / Outplayed 同等）または `copy`（無劣化・高速） |
| `--audio` | トラック番号 / `all` / `mix` / `none` |
| `--separate` | 複数指定したとき、1本にまとめず**試合ごとに**書き出す |
| `--quality` | CQ 値。小さいほど高画質（既定 23） |

---

## 2 つのモード

**`encode`（既定）** — Outplayed と同じ方式。セグメントをフレーム単位で正確に切り出し、
1 本に再エンコードする。違いはエンコーダが libx264 ではなく `h264_nvenc` であること。
拡大縮小や将来的なトランジションを入れるならこちら。

**`copy`** — 再エンコードを一切しない。ソースには**1 秒ごとにキーフレーム**があるため
（実測: 90 秒に 91 個、ちょうど整数秒）、キーフレーム境界で切って連結するだけで済む。
カット位置が最大 1 秒ほど手前にずれる代わりに、**画質劣化ゼロ・約 120 倍速**。

---

## 調査で判明した Outplayed の仕様

### 保存場所

| 内容 | パス |
|---|---|
| 拡張機能 ID | `cghphpbjeabdkomiphingnegihoigeggcfphdofo` |
| **イベント DB** | `%LOCALAPPDATA%\Overwolf\CefBrowserCache\Default\IndexedDB\overwolf-extension_<ID>_0.indexeddb.leveldb` |
| 動画本体 | `<メディアルート>\<ゲーム名>\<セッション>\*.mp4` |
| EZ Edit プロジェクト | `%APPDATA%\Overwolf\<ID>\Projects\project<N>_<epochMs>.dat`（素の UTF-8 JSON） |
| 試合の開始/終了 | `%LOCALAPPDATA%\Overwolf\Log\highlights.log` |
| イベント実ログ | `%LOCALAPPDATA%\Overwolf\Log\Apps\Outplayed\background.html*.log` |

**キル/ダウン等のログは動画の隣には保存されない。** サイドカーファイルは存在せず、
IndexedDB が唯一の永続保存先。本ツールは `MediaDatabase/matches` を読む。

### イベントの構造

`matches[].medias[].events[]`:

```json
{"type": "kill", "time": 137551.0, "timing": {"past": 15000, "future": 5000}, "data": "1"}
```

- `time` は **その動画ファイルの先頭からの ms**（epoch でも試合開始からでもない）
- `timing.past` / `future` は**ゲーム別・イベント別に異なる**
  （Apex の kill は 30s/10s、CS2 の ace は 20s/5s）。ハードコード禁止
- 確認できた種別: `kill` `death` `assist` `ace` `victory` `knockdown` `knocked_out`
  `respawned` `revived`

### セグメントの導出

```
セグメント = merge_overlapping( [ time - past , time + future ] for each event )
```

これが正しいことは実証済み。ある montage の 3 イベントから導出したセグメントは、
Outplayed が実際に `IOPlugin.createMontage` へ渡した `segmentsJson` と
**0.0000 ms の誤差で一致**した（`tools/calibrate.py`）。

### 注意点

> **ファイル名の時刻は動画の開始時刻ではない。**
> 例: `..._10-53-3-933.mp4` の実際の録画開始は 10:39:28。
> ファイル名からの逆算は禁止。`ffprobe` の実尺を使うこと。

Outplayed 本体の Supercut を GPU 化することはできない。
`plugins\outplayed-plugin-io.dll` が内蔵 ffmpeg を直接呼んでおり、差し替え口がないため。

---

## 構成

```
supercut_extended/
├─ library.py    # IndexedDB を読む（実行中でも安全にスナップショットして解析）
├─ model.py      # Match / Media / GameEvent / Segment
├─ segments.py   # イベント → 窓 → ソート → マージ
├─ probe.py      # ffprobe（実尺・解像度・音声トラック）
├─ encoder.py    # エンコーダの実機プローブ + 引数組み立て
├─ render.py     # 2 段レンダリング（NVENC 切り出し → 無劣化連結）
├─ updater.py    # GitHub Releases 経由の更新確認・自己更新
├─ winio.py      # ロック中ファイルの共有読み取り（Win32 CreateFileW）
├─ cli.py
└─ gui/
   ├─ main_window.py   # 試合一覧・設定・進捗
   ├─ player.py        # QMediaPlayer プレビュー
   ├─ timeline.py      # イベント表示兼シークバー
   ├─ icons.py         # coolicons SVG の読み込み・色置換・キャッシュ
   ├─ style.py         # パレットとスタイルシート
   ├─ i18n.py          # 日本語 / English
   └─ update_dialog.py # 更新ダイアログ
cooliocns SVG/   # アイコンセット（exe にも同梱される）
assets/app.ico   # exe・ウィンドウ・タスクバー用アイコン（tools/make_icon.py が生成）
tools/           # 調査・検証・ベンチマーク用スクリプト
```

### 自動アップデート（GitHub Releases）

起動時に GitHub の最新リリースを確認し、新しければダイアログを出す。
`ヘルプ → アップデートを確認` で手動確認も可能。

確認先のリポジトリは `supercut_extended/updater.py` の `GITHUB_REPO` で設定している。

```python
# supercut_extended/updater.py
GITHUB_REPO = "SHIN-DATA-CENTER/Supercut-Extended"
```

環境変数 `SUPERCUT_GITHUB_REPO` でも上書きできる（フォークでの検証用）。
空文字にするとアップデート確認そのものを無効化する（404 を叩き続けないため）。
リリースがまだ 1 つも無い間は API が 404 を返すが、その場合も静かに無視して起動する。

**リリースの作り方**

1. タグを `v1.0.1` のように付ける（先頭の `v` は有無どちらでも可）
2. `dist\` の中の exe を zip にして**リリースの添付ファイル**に加える
   （zip の直下でも 1 階層下でもよい。`SupercutExtended.exe` を探して見つける）
3. リリースノートはそのままアプリ内にマークダウンで表示される。
   [CHANGELOG.md](CHANGELOG.md) の該当バージョンの節をそのまま貼れる形にしてある

zip が添付されていれば、アプリ内の「更新する」でダウンロード → 展開 → 再起動まで自動で行う。
Windows は実行中の exe を上書きできないため、**アプリ終了を待ってからファイルを差し替える
バッチを起動し、コピー後に再起動する**方式にしている。zip が無い場合やソースから
実行している場合は、ボタンがリリースページを開くだけの動作に変わる。

動作の細かい点:

- 自動確認は **1 日 1 回**まで（GitHub の未認証 API は 60 回/時のため）
- ネットワークエラー・タイムアウトは**完全に無視**する（オフラインでダイアログは出さない）
- 「このバージョンをスキップ」を選んだタグは以後通知しない

### アイコンについて

UI のアイコンは同梱の **coolicons** SVG セットを使用している。

このセットは `stroke="currentColor"` で描かれているが、**`currentColor` は CSS/HTML の
仕組みで QSvgRenderer は解釈しない**ため、そのまま描画すると全部黒くなって暗い背景で見えない。
そこで `icons.py` が読み込み時に SVG のテキストを実際の色に置換してから描画している。
これにより 1 つのアイコンを用途別に色違いで使い回せる（通常はグレー、イベント別チェックボックスは
そのイベントの色、など）。

またチェックボックス・ラジオ・コンボの▼・スピンの▲▼は Qt スタイルシート経由で描かれるが、
**スタイルシートは QIcon を受け取れずファイルパスしか取らない**ため、`css_icon()` が色置換済みの
PNG を一時フォルダに書き出してそのパスを返している。Qt のスタイルシートはバックスラッシュを
エスケープ文字として扱うので、パスは必ずスラッシュ区切りで渡している。

**アプリ自体のアイコン**（exe・ウィンドウ・タスクバー）は `assets/app.ico`。
`tools/make_icon.py` が上と同じ「Edit/Layers」グリフを 16〜256px の 9 サイズで描き、
1 つの .ico にまとめている（PyInstaller は SVG を受け取れないため）。

- グリフの背後に**角丸のプレートを敷いている**。アプリ内では暗いクロームの上に線だけで
  置いても見えるが、Explorer の白背景に 16px で置くと細い青線はほぼ消えるため
- Windows は Explorer では **exe に埋め込まれた** アイコンを、タスクバーでは
  **ウィンドウ**のアイコンを見る。別々にすると起動した瞬間にアイコンが変わるので、
  `icons.app_icon()` が同じ .ico を読んで `setWindowIcon()` に渡している
- ソースから動かすと Windows は python.exe としてグループ化してしまうので、
  起動時に `SetCurrentProcessExplicitAppUserModelID` で独自の AppUserModelID を宣言している

### 設計上の要点

- **エンコーダは一覧ではなく実測で判定する。** RTX 3070 は `ffmpeg -encoders` に
  `av1_nvenc` を出すが AV1 エンコードはできない（Ampere は非対応。OBS ログも
  `AV1 supported: false`）。実際に数フレーム encode して可否を決めている
- **GPU パスでは `-pix_fmt` を付けない。** `-hwaccel_output_format cuda` と併用すると
  ソフトウェア scale フィルタが挿入されて `Impossible to convert between the formats`
  で落ちる。CUDA フレームは既に NV12 なので出力は yuv420p になる
- **ffmpeg の stderr は必ず別スレッドで吸う。** フィルタグラフのエラーは数十 KB 出るため、
  64KB のパイプが詰まって ffmpeg が永久にブロックする
- **プリセットが速度を支配する。** 実測 p1=8.2x / p4=4.5x / p7=2.1x（1080p60）。
  一方 B フレーム・空間 AQ はほぼ無料（p4 で 4.5x → 4.6x）なので有効にしてある。既定は **p4**
- **並列ワーカーはほぼ効かない。** 3070 の NVENC チップは 1 基のため
  1→2→3 ワーカーで 166.8s → 160.0s → 158.5s（約 5%）
- **GUI のシグナルは `Qt.QueuedConnection` で繋ぐ。** `render()` は
  `ThreadPoolExecutor` から進捗を emit するので、明示的にキューしないと
  GUI スレッド外でウィジェットを触ることになる
- **1本にまとめられるのは、ストリームの形が揃っている録画だけ。** 2段目の連結は
  concat デマクサなので、パーツのストリームが違うと繋げない。`encode` はコーデックと
  （単一トラック選択時の）音声は揃えるが、**解像度と fps はソースのまま**で、`copy` は
  何も揃えない。そこで `_stream_shape()` が**エンコード前に**照合し、食い違う場合は
  どのファイルがどう違うかを挙げて中断する（長時間エンコードした後に concat で
  失敗するのを避けるため）
- **チェック状態は match_id で持つ。** 行番号で持つと検索で絞り込んだ瞬間にずれる。
  また `_refresh_match_table` は `itemChanged` を大量に出すので、`_populating`
  フラグで自分の再描画とユーザー操作を区別している
- **ラジオは両方の `toggled` に繋いで、ON 側だけで処理する。** Qt は先に旧ラジオを
  OFF にしてから新ラジオを ON にするため、片方だけに繋ぐと「どちらも OFF」の瞬間に
  走って古い状態を読んでしまう
- **テーブルのチェック印は `QTableWidget::indicator` で指定する。** アイテムのチェックは
  QCheckBox ではなくビューが描くので、`QCheckBox::indicator` の指定は届かず、
  Windows 標準の見た目のまま浮いてしまう

---

## 検証用スクリプト

```bash
python tools/dump_indexeddb.py --limit 1   # IndexedDB の中身を見る
python tools/calibrate.py                  # eventTimeMs の意味を実測で確認
python tools/bench_encoder.py              # エンコーダ設定のベンチマーク
python tools/test_gui_render.py            # GUI のスレッド配線を検証
```

## 必要環境

- Windows / Python 3.11+
- **ffmpeg**（PATH 上。`SUPERCUT_FFMPEG` で上書き可）
- NVIDIA / AMD / Intel いずれかの GPU。無ければ libx264 に自動フォールバック
