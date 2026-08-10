"""Update check plumbing for the GUI: background check, prompt, download, apply."""

from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton,
    QTextBrowser, QVBoxLayout,
)

from .. import __version__, updater
from .i18n import tr


class UpdateCheck(QThread):
    """Looks for a newer release. Emits nothing at all when there isn't one."""

    found = Signal(object)
    none = Signal()

    def run(self) -> None:
        try:
            release = updater.check()
        except Exception:
            release = None
        if release is not None:
            self.found.emit(release)
        else:
            self.none.emit()


class DownloadWorker(QObject):
    progressed = Signal(int, int)
    finished = Signal(object)     # staged payload Path
    failed = Signal(str)

    def __init__(self, release) -> None:
        super().__init__()
        self._release = release

    def run(self) -> None:
        try:
            tmp = Path(tempfile.gettempdir()) / (
                self._release.asset_name or "supercut_update.zip")
            updater.download(self._release.asset_url, tmp,
                             lambda d, t: self.progressed.emit(d, t))
            payload = updater.stage(tmp)
            self.finished.emit(payload)
        except Exception as exc:
            self.failed.emit(str(exc))


class UpdateDialog(QDialog):
    """Shows what changed and offers to install it (or just open the page)."""

    def __init__(self, release, parent=None) -> None:
        super().__init__(parent)
        self._release = release
        self._thread: QThread | None = None
        self._worker: DownloadWorker | None = None
        self._skip = False

        self.setWindowTitle(tr("update.title"))
        self.setMinimumWidth(520)

        head = QLabel(tr("update.available", current=__version__,
                         latest=release.tag.lstrip("vV")))
        head.setObjectName("h1")
        head.setWordWrap(True)

        notes = QTextBrowser()
        notes.setMarkdown(release.notes or tr("update.nonotes"))
        notes.setOpenExternalLinks(True)
        notes.setMinimumHeight(180)

        self.progress = QProgressBar()
        self.progress.setVisible(False)

        self.install_btn = QPushButton(tr("update.install"))
        self.install_btn.setObjectName("primary")
        self.install_btn.clicked.connect(self._install)
        self.page_btn = QPushButton(tr("update.open_page"))
        self.page_btn.clicked.connect(self._open_page)
        self.later_btn = QPushButton(tr("update.later"))
        self.later_btn.clicked.connect(self.reject)
        self.skip_btn = QPushButton(tr("update.skip"))
        self.skip_btn.clicked.connect(self._skip_version)

        if not updater.can_self_update(release):
            # Running from source, or the release has no zip asset attached.
            self.install_btn.setVisible(False)
            self.page_btn.setObjectName("primary")

        row = QHBoxLayout()
        row.addWidget(self.skip_btn)
        row.addStretch(1)
        row.addWidget(self.later_btn)
        row.addWidget(self.page_btn)
        row.addWidget(self.install_btn)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(12)
        lay.addWidget(head)
        lay.addWidget(notes, 1)
        lay.addWidget(self.progress)
        lay.addLayout(row)

    def skipped_version(self) -> str | None:
        return self._release.tag if self._skip else None

    def _open_page(self) -> None:
        QDesktopServices.openUrl(QUrl(self._release.page_url))
        self.reject()

    def _skip_version(self) -> None:
        self._skip = True
        self.reject()

    def _install(self) -> None:
        for b in (self.install_btn, self.page_btn, self.later_btn, self.skip_btn):
            b.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 100)
        self.progress.setFormat(tr("update.downloading") + "  %p%")

        self._thread = QThread(self)
        self._worker = DownloadWorker(self._release)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progressed.connect(self._on_progress, Qt.QueuedConnection)
        self._worker.finished.connect(self._on_ready, Qt.QueuedConnection)
        self._worker.failed.connect(self._on_failed, Qt.QueuedConnection)
        self._thread.start()

    def _on_progress(self, done: int, total: int) -> None:
        if total > 0:
            self.progress.setValue(int(done / total * 100))
        else:
            self.progress.setRange(0, 0)

    def _stop_thread(self) -> None:
        if self._thread:
            self._thread.quit()
            self._thread.wait(4000)
        self._thread = None
        self._worker = None

    def _on_ready(self, payload: Path) -> None:
        self._stop_thread()
        self.progress.setFormat(tr("update.restarting"))
        try:
            updater.apply_and_restart(payload)
        except Exception as exc:
            QMessageBox.critical(self, tr("update.title"), str(exc))
            self.reject()
            return
        # The batch script waits for this process to disappear before copying.
        self.accept()
        from PySide6.QtWidgets import QApplication
        QApplication.instance().quit()

    def _on_failed(self, message: str) -> None:
        self._stop_thread()
        self.progress.setVisible(False)
        for b in (self.install_btn, self.page_btn, self.later_btn, self.skip_btn):
            b.setEnabled(True)
        QMessageBox.critical(self, tr("update.title"),
                             tr("update.failed", err=message))
