"""Credits and acknowledgements dialog for Mapperatorinator Desktop."""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from desktop.core import ROOT


PROJECT_LINKS = (
    ("GitHub Repository", "https://github.com/OliBomby/Mapperatorinator"),
    ("Community Discord", "https://discord.gg/5uTwbXuACK"),
    ("Video Showcase", "https://youtu.be/FEr7t1L2EoA"),
)

SPECIAL_THANKS = (
    (
        "Authors of osuT5",
        "https://github.com/gyataro/osuT5",
        "Training code and foundation",
    ),
    (
        "Hugging Face Team",
        "https://huggingface.co/docs/transformers/index",
        "Transformers library and Whisper architecture tooling",
    ),
    (
        "Jason Won & Richard Nagyfi",
        "https://github.com/jaswon",
        "Ideas and technical discussions (jaswon, sedthh)",
    ),
    (
        "Marvin (minetoblend)",
        "https://github.com/minetoblend",
        "Donating training compute credits",
    ),
    (
        "The osu! Community",
        "https://osu.ppy.sh",
        "For creating the beatmaps that make this project possible",
    ),
)

RELATED_WORKS = (
    ("osu! Beatmap Generator", "https://github.com/Syps/osu_beatmap_generator", "Syps (Nick Sypteras)"),
    ("osumapper", "https://github.com/kotritrona/osumapper", "kotritrona, jyvden, Yoyolick (Ryan Zmuda)"),
    ("osu-diffusion", "https://github.com/OliBomby/osu-diffusion", "OliBomby (Olivier Schipper), NiceAesth (Andrei Baciu)"),
    ("osuT5", "https://github.com/gyataro/osuT5", "gyataro (Xiwen Teoh)"),
    ("Beat Learning", "https://github.com/sedthh/BeatLearning", "sedthh (Richard Nagyfi)"),
    ("osu!dreamer", "https://github.com/jaswon/osu-dreamer", "jaswon (Jason Won)"),
)


def _link(text, url):
    return f'<a href="{url}" style="color: #f3a6c8; text-decoration: none;">{text}</a>'


class CreditsDialog(QDialog):
    """Dedicated credit page opened from the Help menu."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Credits — Mapperatorinator")
        self.setMinimumSize(520, 460)
        self.resize(620, 600)
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QFrame()
        header.setObjectName("creditHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(24, 20, 24, 18)
        header_layout.setSpacing(16)

        icon_path = ROOT / "static" / "icon.png"
        if icon_path.is_file():
            icon_label = QLabel()
            pixmap = QPixmap(str(icon_path))
            if not pixmap.isNull():
                icon_label.setPixmap(
                    pixmap.scaled(52, 52, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                )
                header_layout.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignTop)

        title_col = QVBoxLayout()
        title_col.setSpacing(4)
        title = QLabel("Mapperatorinator")
        title.setObjectName("title")
        subtitle = QLabel("Credits & Acknowledgements  ·  Desktop App")
        subtitle.setObjectName("subtitle")
        links = QLabel("  ·  ".join(_link(label, url) for label, url in PROJECT_LINKS))
        links.setOpenExternalLinks(True)
        links.setTextFormat(Qt.TextFormat.RichText)
        title_col.addWidget(title)
        title_col.addWidget(subtitle)
        title_col.addWidget(links)
        header_layout.addLayout(title_col, 1)
        outer.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(24, 18, 24, 20)
        body.setSpacing(14)

        body.addWidget(self._card(
            "Created By",
            [
                (
                    f"<b>{_link('OliBomby (Olivier Schipper)', 'https://github.com/OliBomby')}</b><br>"
                    "Creator and lead developer of Mapperatorinator.<br>"
                    f"Built upon {_link('osuT5', 'https://github.com/gyataro/osuT5')} and "
                    f"{_link('osu-diffusion', 'https://github.com/OliBomby/osu-diffusion')}."
                ),
            ],
        ))

        thanks_items = [
            f"<b>{_link(name, url)}</b> — {role}"
            for name, url, role in SPECIAL_THANKS
        ]
        body.addWidget(self._card("Special Thanks", thanks_items))

        related_items = [
            f"<b>{_link(name, url)}</b> — by {authors}"
            for name, url, authors in RELATED_WORKS
        ]
        body.addWidget(self._card("Related Works", related_items))

        body.addWidget(self._card(
            "License & Responsible Use",
            [
                "Released under the <b>MIT License</b> · Copyright © 2024 OliBomby.",
                "Always disclose the use of AI in your beatmaps.",
            ],
        ))

        body.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        footer = QFrame()
        footer.setObjectName("creditFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(24, 12, 24, 14)
        footer_note = QLabel("Thank you for using Mapperatorinator.")
        footer_note.setObjectName("hint")
        footer_layout.addWidget(footer_note, 1)
        self.close_button = QPushButton("Close")
        self.close_button.setObjectName("primary")
        self.close_button.setDefault(True)
        self.close_button.clicked.connect(self.accept)
        footer_layout.addWidget(self.close_button)
        outer.addWidget(footer)

    def _card(self, heading, entries):
        card = QFrame()
        card.setObjectName("creditCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        title = QLabel(heading)
        title.setObjectName("creditHeading")
        layout.addWidget(title)
        for html in entries:
            label = QLabel(html)
            label.setObjectName("creditEntry")
            label.setWordWrap(True)
            label.setTextFormat(Qt.TextFormat.RichText)
            label.setOpenExternalLinks(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
            layout.addWidget(label)
        return card
