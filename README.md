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

### インストーラー版（推奨）

**`SupercutExtended-v*-setup.exe`** を実行するだけ。
※前提としてOutPlayedを利用していること。

- スタートメニューに登録され、「アプリと機能」からアンインストールできる
- **管理者権限は不要**（UAC が出ない）
- インストール先は `%LOCALAPPDATA%\Programs\SupercutExtended`

Program Files ではなくユーザー領域に入れているのは意図的で、**Program Files だと
アプリ内の自動更新が動かなくなる**ため。更新はインストール先のファイルを置き換える
処理なので、書き込みに管理者権限が要る場所には置けない。

アンインストールしても**設定は残す**（ユーザーのデータなので消さない）。

### zip 版（展開して使う）

インストールせずに持ち運びたい場合はこちら。アプリ内の自動更新もこの zip を使う。

zip を展開すると `SupercutExtended` フォルダが出てくる。中身は:

| ファイル | 大きさ | 用途 |
|---|---|---|
| `SupercutExtended.exe` | 2.5 MB | GUI 本体 |
| `SupercutExtended-cli.exe` | 2.5 MB | コマンドライン版（`... list` など） |
| `_internal\` | 299 MB | Python・PySide6・**ffmpeg 本体**。2 つの exe が共有する |

**フォルダごと 1 セットで扱うこと。** インストールは不要で、フォルダごとどこに置いても動くが、
`_internal\` を置いていくと exe だけでは起動しない。移動やコピーはフォルダ単位で行う。

v1.3.2 までは exe 1 本ずつに全部入りだった（各 114 MB）。フォルダ形式にしたのは、
起動が **9.3 秒 → 2.0 秒**、配布 zip が **228 MB → 120 MB** になるため（下の比較表を参照）。

同梱している ffmpeg は gyan.dev の `ffmpeg-8.1.2-essentials_build`（**GPLv3**）。
libx264 / libx265 / NVENC / AMD AMF / Intel QSV の H.264・HEVC が全部有効な、
公式 Windows ビルドで唯一の組み合わせを選んでいる。再配布上の注意点は
[`vendor/ffmpeg/NOTICE.txt`](vendor/ffmpeg/NOTICE.txt) を参照。
同梱の ffmpeg が使えない場合は PATH、次に `SUPERCUT_FFMPEG` 環境変数にフォールバックする。

自分でビルドし直す場合:

```bash
pip install -r requirements.txt
python tools/fetch_ffmpeg.py             # vendor/ffmpeg/ に ffmpeg.exe 等を取得（初回のみ）
python tools/make_icon.py                # assets/app.ico を生成（初回のみ）
python -m PyInstaller supercut.spec --noconfirm --clean
python tools/build_installer.py          # インストーラーも作る場合（Inno Setup 6 が必要）
```

`build_installer.py` はバージョンを `supercut_extended/__init__.py` から読むので、
インストーラーとアプリの表示が食い違うことはない。出力は `dist/` に置かれる。

#### 単体 exe とフォルダ配布の切り替え

`supercut.spec` 冒頭の `ONEFILE` で出力の形を切り替えられる。同じソース・同じ PC での実測:

| | `ONEFILE = True` | `ONEFILE = False`（既定） |
|---|---|---|
| 出力 | exe 2 本（各 114.3 MB） | フォルダ 303.9 MB / 701 ファイル |
| exe 本体 | 114.3 MB × 2 | 2.5 MB × 2 ＋ 共有の `_internal\` |
| 同梱 ffmpeg | **2 部**（exe ごとに 1 部） | **1 部** |
| 配布 zip | 227.7 MB | **120.4 MB** |
| 起動・初回 | 9.3 秒 | 6.1 秒 |
| 起動・2 回目以降 | 9.3 秒 | **2.0 秒** |

単体 exe は起動のたびに中身を `%TEMP%` へ展開するので、同梱した ffmpeg（204 MB）が
そのまま毎回の起動時間に乗る。**何回起動しても速くならない**のはこのため。
フォルダ配布は展開が無いぶん、OS のファイルキャッシュが効いて 2 回目以降が一気に速い。

zip が半分近くまで小さくなるのは、単体 exe の中身が**既に圧縮済み**で zip がほとんど
効かないのに対し、フォルダ配布は生のファイルなので普通に圧縮できるから。
展開後のサイズは単体 exe 2 本（228.6 MB）よりフォルダ（303.9 MB）のほうが大きいが、
これは PySide6 等の共有分よりも「ffmpeg を 2 部持たない」効果が効く前の素の差。

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

#### 画面サイズ・黒帯（v1.3.0〜）

出力セクションの「画面サイズ・黒帯」で、**書き出すフレームそのもの**を決められる。
プレビューは常に**出力される範囲だけ**を映すので、書き出す前に最終的な絵が分かる。

- **出力解像度** — ソースのまま / FHD / QHD / 4K / HD / 正方形 / 縦、または任意サイズ
- **黒帯のカット** — 左右上下それぞれをソースのピクセル数で指定
- **`黒帯を自動検出`** — 録画の中盤を解析して黒帯の実寸を 4 つの欄に入れる
- **`画面いっぱいに引き延ばす`** — 4:3 のゲーム画面が 16:9 の録画に入っているときなど、
  黒帯をカットした映像を出力解像度いっぱいに広げる。**縦横比は意図的に崩れる**。
  オフなら縦横比を保って黒で埋める

出力解像度を指定すると全ソースが同じフレームに揃うため、**解像度の違う録画どうしも
1 本にまとめられる**ようになる。

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
確実に指定したいときは `events` で表示される**試合 ID をそのまま**渡す
（`build 6677164ad4174d0a8fc1af6a0a1e237e_5` のように）。

> 試合 ID は `<セッションのハッシュ>_<連番>` という形なので、**同じセッションの試合は
> 末尾以外が全部同じ**。先頭数文字だけ渡すと複数に当たるため、その場合は候補を並べて
> 中断する（黙って 1 件目を選ぶと違う試合を書き出してしまうので）。

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
| `--size` | 出力解像度（`1920x1080`）。未指定ならソースのまま |
| `--crop` | 黒帯のカット。`240`（四辺） / `240,240`（左右） / `240,240,0,0`（左右上下） |
| `--stretch` | `--size` いっぱいに引き延ばす（縦横比は崩れる） |

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

**添付する zip は原則 1 つにする。** 複数付ける場合、updater は名前が本体らしくないもの
（`cli` / `source` / `src` / `debug` などを含む）を後回しにして選ぶので、本体の zip は
`SupercutExtended-....zip` のように素直な名前にしておく。

過去のリリースにあとからノートだけ付ける場合は、**`latest` にしないこと**
（`gh release create ... --latest=false`）。updater は `releases/latest` しか見ないので、
zip の無いリリースが `latest` になると自動更新がリリースページを開くだけの動作に退化する。

zip が添付されていれば、アプリ内の「更新する」でダウンロード → 展開 → 再起動まで自動で行う。
Windows は実行中の exe を上書きできないため、**アプリ終了を待ってからファイルを差し替える
バッチを起動し、コピー後に再起動する**方式にしている。zip が無い場合やソースから
実行している場合は、ボタンがリリースページを開くだけの動作に変わる。

フォルダ配布（onedir）の zip では、`_internal\` だけ**同期**（古いファイルを削除）し、
アプリフォルダ直下は**上書きのみ**にしている。ユーザーが exe の隣に置いたファイルは
更新で消えない。

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
- **所要時間の見積もりは、単一クリップのベンチで測ってはいけない。** 本ツールは
  セグメントごとに ffmpeg を起動するため、短いクリップを多数つなぐ montage では
  起動コストが積み上がる。合成ベンチの 4.3 倍速に対し、実ジョブ（27 セグメント /
  55.5 秒）では 2.6 倍速だった。`encoder.BASE_RATES` は**実レンダリングで測った値**で、
  プリセット・画質の係数（`tools/bench_presets.py` で実測、既定値 = 1.00 に正規化）を
  それに掛ける
- **GPU では画質を変えても速度は変わらない。** NVENC は固定機能なので、CQ を変えても
  処理量がほとんど動かない（cq18〜cq30 で 0.98〜1.01 倍）。CPU の CRF はビット数が
  処理量に効くので libx264 で約 1.27 倍の差が出る。見積もりが画質で動くのは CPU の
  ときだけで、これが正しい挙動

---

## 検証用スクリプト

```bash
python tools/dump_indexeddb.py --limit 1   # IndexedDB の中身を見る
python tools/calibrate.py                  # eventTimeMs の意味を実測で確認
python tools/bench_encoder.py              # エンコーダ設定のベンチマーク
python tools/test_gui_render.py            # GUI のスレッド配線を検証
python tools/test_framing.py               # 解像度・黒帯カット・引き延ばしを実描画で検証
python tools/test_preview_framing.py       # プレビューが出力範囲だけを映すかをピクセルで検証
python tools/test_framing_ui.py            # フレーミング UI の配線と設定の保存/復元
```

## 必要環境

- Windows / Python 3.11+
- **ffmpeg**（exe 版は同梱済み。ソースから動かす場合は PATH 上に置くか
  `SUPERCUT_FFMPEG` / `SUPERCUT_FFPROBE` で上書き）
- NVIDIA / AMD / Intel いずれかの GPU。無ければ libx264/libx265 に自動フォールバック
