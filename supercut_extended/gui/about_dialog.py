"""The About box.

More than a version string, because this build redistributes ffmpeg: the GPLv3
notice has to be reachable from inside the app, not only from a text file next to
the exe that nobody opens.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from .. import __version__
from ..updater import repo
from . import icons
from .i18n import tr
from .style import ACCENT_HI, BORDER_SOFT, TEXT_DIM, build_style


def _rule() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setFixedHeight(1)
    line.setStyleSheet(f"background: {BORDER_SOFT}; border: none;")
    return line


class AboutDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("menu.about"))
        self.setStyleSheet(build_style())
        self.setWindowIcon(icons.app_icon())
        self.setMinimumWidth(460)

        url = f"https://github.com/{repo()}"

        glyph = QLabel()
        glyph.setPixmap(icons.app_icon().pixmap(QSize(56, 56)))
        glyph.setFixedSize(56, 56)

        title = QLabel("Supercut Extended")
        title.setStyleSheet("font-size: 17px; font-weight: 600;")
        version = QLabel(tr("about.version", version=__version__))
        version.setStyleSheet(f"color: {TEXT_DIM};")

        heading = QVBoxLayout()
        heading.setSpacing(2)
        heading.addWidget(title)
        heading.addWidget(version)
        heading.addStretch(1)

        top = QHBoxLayout()
        top.setSpacing(14)
        top.addWidget(glyph)
        top.addLayout(heading, 1)

        body = QLabel(tr("about.description"))
        body.setWordWrap(True)
        body.setTextFormat(Qt.RichText)

        links = QLabel(
            f'<a style="color:{ACCENT_HI}; text-decoration:none;" href="{url}">'
            f'{tr("about.repo")}</a>'
            f'&nbsp;&nbsp;·&nbsp;&nbsp;'
            f'<a style="color:{ACCENT_HI}; text-decoration:none;" '
            f'href="{url}/releases">{tr("about.releases")}</a>')
        links.setTextFormat(Qt.RichText)
        links.setOpenExternalLinks(True)
        links.setTextInteractionFlags(Qt.TextBrowserInteraction)

        notice = QLabel(tr("about.ffmpeg"))
        notice.setWordWrap(True)
        notice.setObjectName("captionLabel")
        notice.setTextFormat(Qt.RichText)
        notice.setOpenExternalLinks(True)

        icons_notice = QLabel(tr("about.icons"))
        icons_notice.setWordWrap(True)
        icons_notice.setObjectName("captionLabel")
        icons_notice.setTextFormat(Qt.RichText)
        icons_notice.setOpenExternalLinks(True)

        copyright_ = QLabel(tr("about.copyright"))
        copyright_.setObjectName("captionLabel")

        close = QPushButton(tr("editor.close"))
        close.setDefault(True)
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(12)
        lay.addLayout(top)
        lay.addWidget(_rule())
        lay.addWidget(body)
        lay.addWidget(links)
        lay.addWidget(_rule())
        lay.addWidget(notice)
        lay.addWidget(icons_notice)
        lay.addWidget(copyright_)
        lay.addLayout(row)
