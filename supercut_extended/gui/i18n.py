"""Minimal string table. Japanese is the default; English is kept as a fallback.

Deliberately not Qt Linguist: there are a few dozen strings and no translators to
hand off to, so a dict avoids a .ts/.qm build step in the PyInstaller pipeline.
"""

from __future__ import annotations

_LANG = "ja"

STRINGS: dict[str, dict[str, str]] = {
    "ja": {
        "app.title": "Supercut Extended",
        "search.placeholder": "ゲーム名・マップで絞り込み...",

        "col.pick": "",
        "col.when": "日時",
        "col.game": "ゲーム",
        "col.length": "長さ",
        "col.highlights": "ハイライト",

        "sel.all": "全選択",
        "sel.clear": "解除",
        "sel.count": "{n} 件を選択中",
        "sel.none": "チェックした試合をまとめて出力します",
        "sel.tip": "チェックした試合がまとめて出力されます。\n"
                   "チェックが無いときは、表示中の試合だけを出力します。",

        "select.match": "試合を選択してください",
        "loading": "Outplayed のライブラリを読み込み中...",
        "loaded": "{n} 件の試合にハイライトがあります",

        "player.play": "再生",
        "player.pause": "一時停止",
        "player.prev": "前のイベント",
        "player.next": "次のイベント",
        "player.volume": "音量",
        "player.novideo": "動画を読み込めませんでした",
        "player.jump_seg": "セグメントのみ再生",

        "group.events": "対象イベント",
        "group.timing": "クリップの長さ",
        "group.output": "出力",
        "group.preview": "プレビュー",

        "timing.defaults": "Outplayed のイベント別デフォルトを使う",
        "timing.before": "前",
        "timing.after": "後",
        "timing.gap": "結合間隔",

        "out.method": "方式",
        "out.mode.encode": "再エンコード",
        "out.mode.encode.desc": "Outplayed と同じ方式。フレーム単位で正確（下でエンコード方式を選択）",
        "out.mode.copy": "無劣化コピー",
        "out.mode.copy.desc": "最速・画質劣化なし。カットは約1秒単位",

        "out.engine": "エンコード方式",
        "out.engine.gpu": "ハードウェアエンコード（GPU）",
        "out.engine.cpu": "ソフトウェアエンコード（CPU）",
        "out.engine.detected.gpu": "このPCでは {vendor} GPU のハードウェアエンコードが使えます（推奨・高速）。",
        "out.engine.detected.cpu_only": "このPCで使えるハードウェアエンコーダが見つかりませんでした。ソフトウェアエンコード（CPU）を使用します（低速）。",
        "out.engine.gpu_unavailable": "このPCで使えるハードウェアエンコーダが見つかりません",
        "out.codec": "コーデック",
        "out.codec.h264": "H.264",
        "out.codec.h264.desc": "再生互換性が最も高い、標準的な選択肢",
        "out.codec.hevc": "HEVC（H.265）",
        "out.codec.hevc.desc": "同じ画質でファイルサイズを削減できるが、再生互換性はH.264より劣る",
        "out.codec.unavailable": "このPCのこのエンコード方式では使えません",
        "out.encoder": "エンコーダ",
        "out.preset": "プリセット",
        "out.preset.recommended": "{name}・おすすめ",
        "preset.p1": "最速",
        "preset.p2": "かなり速い",
        "preset.p3": "速め",
        "preset.p4": "標準",
        "preset.p5": "高画質より",
        "preset.p6": "高画質",
        "preset.p7": "最高画質",
        "preset.ultrafast": "最速",
        "preset.veryfast": "かなり速い",
        "preset.faster": "速め",
        "preset.fast": "標準",
        "preset.medium": "高画質より",
        "preset.slow": "高画質",
        "out.quality": "画質（CQ/CRF）",
        "quality.18": "高画質",
        "quality.21": "画質重視",
        "quality.23": "標準",
        "quality.26": "サイズ重視",
        "quality.30": "小さいファイル",
        "out.audio": "音声",
        "out.file": "保存先",
        "out.dir": "保存先フォルダ",
        "out.browse": "参照...",

        "out.shape": "複数選択したとき",
        "out.shape.combine": "1本にまとめる",
        "out.shape.combine.desc": "選んだ試合を時系列につないで 1 ファイルにする",
        "out.shape.separate": "試合ごとに別ファイル",
        "out.shape.separate.desc": "試合ごとに 1 ファイルずつ書き出す",

        "audio.all": "全トラックを残す",
        "audio.mix": "全トラックをミックス",
        "audio.none": "音声なし",

        "btn.build": "Supercut を作成",
        "btn.cancel": "キャンセル",
        "btn.reveal": "フォルダを開く",

        "summary.none": "イベントが選択されていません",
        "summary": "{events} 件のイベント → {segments} セグメント / {length}   ·   予想 {eta:.0f} 秒",
        "summary.multi": "{matches} 試合 · {events} 件のイベント → {segments} セグメント / {length}   ·   予想 {eta:.0f} 秒",

        "status.building": "作成中...",
        "status.cancelling": "キャンセルしています...",
        "status.cancelled": "キャンセルしました",
        "status.done": "{name} を {secs:.1f} 秒で作成しました（{speed:.1f}倍速 / {mb:.0f} MB）",
        "status.done.multi": "{n} 本を {secs:.1f} 秒で作成しました（合計 {mb:.0f} MB）",
        "status.probe_fail": "動画を読み込めません: {err}",
        "status.load_fail": "試合の読み込みに失敗しました: {err}",

        "err.ffmpeg.title": "ffmpeg が必要です",
        "err.lib.title": "Outplayed のデータを読めません",
        "err.render.title": "作成に失敗しました",
        "err.encoder.title": "エンコーダ",
        "err.encoder.body": "使用可能なエンコーダがありません。",
        "err.output.title": "保存先",
        "err.output.body": "先に保存先を指定してください。",
        "err.output.dir": "先に保存先フォルダを指定してください。",
        "err.noevents.title": "対象がありません",
        "err.noevents.body": "選択した試合には、指定した種類のイベントがありません。",
        "encoder.missing": "ffmpeg が見つかりません",

        "editor.title": "クリップ編集",
        "editor.open": "編集画面を開く",
        "editor.hint": "クリップをドラッグして並べ替え、端をドラッグして長さを調整します。Ctrl+ホイールで拡大縮小。",
        "editor.noselection": "クリップを選択してください",
        "editor.enabled": "使用する",
        "editor.fade_in": "フェードイン",
        "editor.fade_out": "フェードアウト",
        "editor.length": "長さ",
        "editor.remove": "削除",
        "editor.bgm": "BGM",
        "editor.bgm_none": "（なし）",
        "editor.bgm_pick": "音声ファイルを選ぶ...",
        "editor.bgm_clear": "外す",
        "editor.volume": "音量",
        "editor.loop": "ループ",
        "editor.render": "この内容で書き出す",
        "editor.close": "閉じる",
        "editor.preview_note": "プレビューは並び順と長さの確認用です。フェードと BGM は書き出し時に適用されます。",
        "editor.summary": "{clips} クリップ / 合計 {length:.1f} 秒",
        "editor.will_encode": "　·　フェード/BGM のため再エンコードします",
        "editor.done": "{name} を書き出しました",

        "menu.view": "表示",
        "menu.language": "言語 / Language",
        "dlg.restart": "言語を切り替えました。次回起動時から反映されます。",

        "menu.help": "ヘルプ",
        "menu.check_update": "アップデートを確認",
        "menu.about": "バージョン情報",
        "about.body": "Supercut Extended {version}\n\nOutplayed の Supercut を GPU (NVENC) で作り直すツール",
        "update.title": "アップデート",
        "update.available": "新しいバージョンがあります\n\n現在: {current}  →  最新: {latest}",
        "update.nonotes": "（リリースノートはありません）",
        "update.install": "更新する",
        "update.open_page": "リリースページ",
        "update.later": "後で",
        "update.skip": "このバージョンをスキップ",
        "update.downloading": "ダウンロード中",
        "update.restarting": "再起動して適用します...",
        "update.failed": "更新に失敗しました: {err}",
        "update.uptodate": "最新バージョンです（{version}）",
        "update.disabled": "アップデート確認は未設定です（updater.py の GITHUB_REPO）",
        "update.checking": "アップデートを確認しています...",
    },
    "en": {
        "app.title": "Supercut Extended",
        "search.placeholder": "Filter by game or map...",

        "col.pick": "",
        "col.when": "When",
        "col.game": "Game",
        "col.length": "Length",
        "col.highlights": "Highlights",

        "sel.all": "Select all",
        "sel.clear": "Clear",
        "sel.count": "{n} selected",
        "sel.none": "Tick matches to build them together",
        "sel.tip": "Ticked matches are built together.\n"
                   "With nothing ticked, only the match being previewed is built.",

        "select.match": "Select a match",
        "loading": "Loading Outplayed library...",
        "loaded": "{n} matches with highlights",

        "player.play": "Play",
        "player.pause": "Pause",
        "player.prev": "Previous event",
        "player.next": "Next event",
        "player.volume": "Volume",
        "player.novideo": "Could not load video",
        "player.jump_seg": "Play segments only",

        "group.events": "Include events",
        "group.timing": "Clip timing",
        "group.output": "Output",
        "group.preview": "Preview",

        "timing.defaults": "Use Outplayed's per-event defaults",
        "timing.before": "before",
        "timing.after": "after",
        "timing.gap": "merge gap",

        "out.method": "Method",
        "out.mode.encode": "Re-encode",
        "out.mode.encode.desc": "Same method as Outplayed, frame accurate (pick the encoder type below)",
        "out.mode.copy": "Stream copy",
        "out.mode.copy.desc": "Fastest, no quality loss, cuts snap to ~1s",

        "out.engine": "Encoder type",
        "out.engine.gpu": "Hardware encode (GPU)",
        "out.engine.cpu": "Software encode (CPU)",
        "out.engine.detected.gpu": "This PC has a hardware encoder available on the {vendor} GPU (recommended, faster).",
        "out.engine.detected.cpu_only": "No usable hardware encoder was found on this PC. Using software encode (CPU) instead (slower).",
        "out.engine.gpu_unavailable": "No usable hardware encoder was found on this PC",
        "out.codec": "Codec",
        "out.codec.h264": "H.264",
        "out.codec.h264.desc": "The most broadly compatible, standard choice",
        "out.codec.hevc": "HEVC (H.265)",
        "out.codec.hevc.desc": "Smaller file at the same quality, but less compatible than H.264",
        "out.codec.unavailable": "Not available with this encoder type on this PC",
        "out.encoder": "Encoder",
        "out.preset": "Preset",
        "out.preset.recommended": "{name} - recommended",
        "preset.p1": "Fastest",
        "preset.p2": "Very fast",
        "preset.p3": "Fast",
        "preset.p4": "Balanced",
        "preset.p5": "Leaning quality",
        "preset.p6": "High quality",
        "preset.p7": "Best quality",
        "preset.ultrafast": "Fastest",
        "preset.veryfast": "Very fast",
        "preset.faster": "Fast",
        "preset.fast": "Balanced",
        "preset.medium": "Leaning quality",
        "preset.slow": "High quality",
        "out.quality": "Quality (CQ/CRF)",
        "quality.18": "High quality",
        "quality.21": "Leans quality",
        "quality.23": "Balanced",
        "quality.26": "Leans smaller",
        "quality.30": "Small file",
        "out.audio": "Audio",
        "out.file": "File",
        "out.dir": "Folder",
        "out.browse": "Browse...",

        "out.shape": "When several are selected",
        "out.shape.combine": "Combine into one file",
        "out.shape.combine.desc": "Join the selected matches in chronological order",
        "out.shape.separate": "One file per match",
        "out.shape.separate.desc": "Write a separate supercut for each match",

        "audio.all": "All tracks (keep separate)",
        "audio.mix": "Mix all tracks",
        "audio.none": "No audio",

        "btn.build": "Build Supercut",
        "btn.cancel": "Cancel",
        "btn.reveal": "Open folder",

        "summary.none": "No events selected",
        "summary": "{events} events → {segments} segments / {length}   ·   about {eta:.0f}s",
        "summary.multi": "{matches} matches · {events} events → {segments} segments / {length}   ·   about {eta:.0f}s",

        "status.building": "Building...",
        "status.cancelling": "Cancelling...",
        "status.cancelled": "Render cancelled",
        "status.done": "Built {name} in {secs:.1f}s ({speed:.1f}x realtime, {mb:.0f} MB)",
        "status.done.multi": "Built {n} files in {secs:.1f}s ({mb:.0f} MB total)",
        "status.probe_fail": "cannot probe media: {err}",
        "status.load_fail": "failed to load match: {err}",

        "err.ffmpeg.title": "ffmpeg is required",
        "err.lib.title": "Cannot read Outplayed library",
        "err.render.title": "Render failed",
        "err.encoder.title": "Encoder",
        "err.encoder.body": "No usable encoder available.",
        "err.output.title": "Output",
        "err.output.body": "Choose an output file first.",
        "err.output.dir": "Choose an output folder first.",
        "err.noevents.title": "Nothing to build",
        "err.noevents.body": "The selected matches have none of the chosen event kinds.",
        "encoder.missing": "ffmpeg not found",

        "editor.title": "Clip editor",
        "editor.open": "Open editor",
        "editor.hint": "Drag a clip to reorder it, drag its edge to trim. Ctrl+wheel to zoom.",
        "editor.noselection": "Select a clip",
        "editor.enabled": "Include",
        "editor.fade_in": "Fade in",
        "editor.fade_out": "Fade out",
        "editor.length": "Length",
        "editor.remove": "Remove",
        "editor.bgm": "Music",
        "editor.bgm_none": "(none)",
        "editor.bgm_pick": "Choose audio...",
        "editor.bgm_clear": "Clear",
        "editor.volume": "Volume",
        "editor.loop": "Loop",
        "editor.render": "Render this arrangement",
        "editor.close": "Close",
        "editor.preview_note": "Preview shows order and length. Fades and music are applied when rendering.",
        "editor.summary": "{clips} clips / {length:.1f}s total",
        "editor.will_encode": "  -  fades/music force a re-encode",
        "editor.done": "Wrote {name}",

        "menu.view": "View",
        "menu.language": "言語 / Language",
        "dlg.restart": "Language changed. It will apply the next time you start the app.",

        "menu.help": "Help",
        "menu.check_update": "Check for updates",
        "menu.about": "About",
        "about.body": "Supercut Extended {version}\n\nGPU (NVENC) replacement for Outplayed's Supercut",
        "update.title": "Update",
        "update.available": "A new version is available\n\nCurrent: {current}  →  Latest: {latest}",
        "update.nonotes": "(no release notes)",
        "update.install": "Update now",
        "update.open_page": "Release page",
        "update.later": "Later",
        "update.skip": "Skip this version",
        "update.downloading": "Downloading",
        "update.restarting": "Restarting to apply...",
        "update.failed": "Update failed: {err}",
        "update.uptodate": "You are up to date ({version})",
        "update.disabled": "Update checking is not configured (GITHUB_REPO in updater.py)",
        "update.checking": "Checking for updates...",
    },
}

# Event kinds as Outplayed names them, translated for display.
EVENT_LABELS: dict[str, dict[str, str]] = {
    "ja": {
        "kill": "キル", "death": "デス", "assist": "アシスト", "ace": "エース",
        "victory": "勝利", "knockdown": "ダウン奪取", "knocked_out": "ダウン",
        "respawned": "リスポーン", "revived": "蘇生",
    },
    "en": {},
}


def set_language(lang: str) -> None:
    global _LANG
    if lang in STRINGS:
        _LANG = lang


def language() -> str:
    return _LANG


def tr(key: str, **kwargs) -> str:
    """Look the key up, falling back to English and finally to the key itself.

    Membership rather than truthiness: a deliberately empty string (the tick column's
    header, say) is a real translation, and `table.get(key) or ...` would fall through
    it and display the raw key.
    """
    table = STRINGS.get(_LANG) or STRINGS["en"]
    if key in table:
        text = table[key]
    elif key in STRINGS["en"]:
        text = STRINGS["en"][key]
    else:
        text = key
    return text.format(**kwargs) if kwargs else text


def event_label(kind: str) -> str:
    """Localised event name, falling back to Outplayed's own wording."""
    return EVENT_LABELS.get(_LANG, {}).get(kind, kind)


def fmt_duration(seconds: float) -> str:
    """A length of footage: "15分04秒" in Japanese, "15m04s" in English.

    Distinct from the playback timecode in player.fmt_time, which stays as h:mm:ss
    because that is what people expect next to a transport control.
    """
    total = max(0, int(round(seconds)))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if _LANG == "ja":
        if h:
            return f"{h}時間{m:02d}分{s:02d}秒"
        if m:
            return f"{m}分{s:02d}秒"
        return f"{s}秒"
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"
