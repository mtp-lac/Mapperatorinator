"""Native widgets for the Mapperatorinator generation workflow."""

import json
import os
from pathlib import Path

from PyQt6.QtCore import Qt, QSettings, QTimer, QUrl
from PyQt6.QtGui import QAction, QDesktopServices, QFont, QIcon, QKeySequence
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox,
    QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QScrollArea, QSplitter, QStackedWidget,
    QVBoxLayout, QWidget,
)

from desktop.core import DEFAULTS, MODELS, ROOT, export_settings, load_settings, read_reference, validate_settings
from desktop.credits import CreditsDialog
from desktop.descriptors import DescriptorPanel
from desktop.jobs import InferenceJob


STYLE = """
QMainWindow, QDialog { background: #15171d; color: #e7e9ef; }
QWidget { color: #e7e9ef; font-size: 13px; }
QLabel { background: transparent; }
QLabel#title { font-size: 23px; font-weight: 700; }
QLabel#subtitle, QLabel#hint { color: #9ba3b6; }
QLabel#sectionTitle { font-size: 16px; font-weight: 600; padding-top: 12px; }
QLabel#error { color: #ff9aab; padding: 10px; background: #38202a; border-radius: 6px; }
QFrame#sidebar { background: #1b1e26; border-right: 1px solid #303540; }
QLabel#brand { font-size: 16px; font-weight: 700; color: #f3a6c8; }
QListWidget#navigation { background: transparent; border: none; outline: none; }
QListWidget#navigation::item { padding: 13px 10px; margin: 3px 0; border-radius: 6px; }
QListWidget#navigation::item:selected { background: #353043; color: #ffc1df; }
QScrollArea, QStackedWidget { background: #15171d; border: none; }
QScrollArea > QWidget > QWidget { background: #15171d; }
QLineEdit, QComboBox, QPlainTextEdit {
    background: #20242d; border: 1px solid #3b4251; border-radius: 5px;
    padding: 7px; selection-background-color: #7c4b70;
}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus { border: 1px solid #e68eb8; }
QLineEdit:disabled, QComboBox:disabled { color: #737b8b; background: #1b1e25; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView { background: #252a35; color: #e7e9ef; selection-background-color: #59415b; }
QPushButton { background: #2b303c; border: 1px solid #434b5d; padding: 8px 13px; border-radius: 5px; }
QPushButton:hover { background: #3c4251; border-color: #7b708e; }
QPushButton:pressed { background: #484050; }
QPushButton:disabled { color: #71798a; background: #242831; border-color: #303540; }
QPushButton#primary { background: #d68bb3; border-color: #d68bb3; color: #211722; font-weight: 700; padding: 11px 18px; }
QPushButton#primary:hover { background: #efadd1; }
QPushButton#primary:disabled { background: #53414f; color: #94818e; border-color: #53414f; }
QPushButton#cancel { color: #ffb6bf; }
QCheckBox { spacing: 9px; padding: 5px 0; }
QCheckBox::indicator { width: 16px; height: 16px; }
QFrame#descriptorCard { background: #202124; border: 1px solid #34373e; border-radius: 4px; }
QLabel#descriptorHeading { font-size: 16px; font-weight: 700; padding-bottom: 6px; }
QCheckBox#descriptor { spacing: 9px; padding: 4px 0; font-weight: 600; }
QFrame#creditHeader { background: #1b1e26; border-bottom: 1px solid #303540; }
QFrame#creditFooter { background: #1b1e26; border-top: 1px solid #303540; }
QFrame#creditCard { background: #202124; border: 1px solid #34373e; border-radius: 6px; }
QLabel#creditHeading { font-size: 15px; font-weight: 700; color: #f3a6c8; padding-bottom: 2px; }
QLabel#creditEntry { color: #e7e9ef; line-height: 1.4; }
QProgressBar { border: 1px solid #3b4251; background: #242831; border-radius: 4px; min-height: 12px; text-align: center; }
QProgressBar::chunk { background: #cc87b1; border-radius: 3px; }
QSplitter::handle { background: #303540; width: 1px; }
QMenuBar, QMenu { background: #1b1e26; color: #e7e9ef; }
QMenu::item:selected { background: #59415b; }
QToolTip { background: #303540; color: #ffffff; border: 1px solid #70798c; padding: 5px; }
QStatusBar { color: #9ba3b6; }
"""


class PathEdit(QWidget):
    def __init__(self, caption, file_filter="All files (*)", folder=False, parent=None):
        super().__init__(parent)
        self.caption = caption
        self.file_filter = file_filter
        self.folder = folder
        self.edit = QLineEdit()
        self.edit.setClearButtonEnabled(True)
        self.edit.setPlaceholderText("Choose a folder…" if folder else "Choose a file or paste a path…")
        self.edit.setAccessibleName(caption)
        self.button = QPushButton("Browse…")
        self.button.clicked.connect(self.browse)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.edit, 1)
        layout.addWidget(self.button)
        self.setAcceptDrops(True)

    def browse(self):
        current = self.edit.text().strip()
        initial = str(Path(current).expanduser()) if current else str(ROOT)
        if self.folder:
            path = QFileDialog.getExistingDirectory(self, self.caption, initial)
        else:
            path, _ = QFileDialog.getOpenFileName(self, self.caption, initial, self.file_filter)
        if path:
            self.edit.setText(path)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and event.mimeData().urls()[0].isLocalFile():
            event.acceptProposedAction()

    def dropEvent(self, event):
        path = event.mimeData().urls()[0].toLocalFile()
        if self.folder and not Path(path).is_dir():
            path = str(Path(path).parent)
        self.edit.setText(path)
        event.acceptProposedAction()


class MainWindow(QMainWindow):
    def __init__(self, restore=True):
        super().__init__()
        self.setWindowTitle("Mapperatorinator — Desktop")
        self.setMinimumSize(960, 680)
        self.resize(1320, 900)
        icon_path = ROOT / "static" / "icon.png"
        if icon_path.is_file():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.preferences = QSettings()
        self.fields = {}
        self.rows = {}
        self.contexts = {}
        self.forms = []
        self._closing = False
        self._elapsed_seconds = 0
        self._last_output_dir = None
        self.job = InferenceJob(self)
        self.job.output.connect(self._append_log)
        self.job.stage.connect(self._set_stage)
        self.job.progress.connect(self._set_progress)
        self.job.finished.connect(self._job_finished)
        self.clock = QTimer(self)
        self.clock.setInterval(1000)
        self.clock.timeout.connect(self._tick)
        self._build_menu()
        self._build_ui()
        initial = dict(DEFAULTS)
        if restore:
            try:
                stored = self.preferences.value("generationSettings", "")
                if stored:
                    initial = load_settings(json.loads(stored))
            except (ValueError, TypeError, KeyError):
                self.statusBar().showMessage("Saved settings could not be loaded. Using defaults.", 8000)
            geometry = self.preferences.value("geometry")
            if geometry:
                self.restoreGeometry(geometry)
        self.apply_settings(initial)
        self.fields["model"].currentIndexChanged.connect(self._update_capabilities)
        self.fields["gamemode"].currentIndexChanged.connect(self._update_capabilities)
        self.fields["beatmap_path"].edit.textChanged.connect(self._update_capabilities)
        self.fields["add_to_beatmap"].toggled.connect(self._update_capabilities)
        self._update_capabilities()
        self.setAcceptDrops(True)

    def _build_menu(self):
        menu = self.menuBar().addMenu("&File")
        for title, shortcut, callback in (
            ("Choose &audio…", "Ctrl+O", self._choose_audio),
            ("&Import settings…", "Ctrl+Shift+O", self.import_settings),
            ("&Export settings…", "Ctrl+S", self.export_settings),
            ("&Reset settings", "", self.reset_settings),
            ("E&xit", "Ctrl+Q", self.close),
        ):
            action = QAction(title, self)
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(callback)
            menu.addAction(action)
        run = QAction("Generate", self)
        run.setShortcut(QKeySequence("Ctrl+Return"))
        run.triggered.connect(self.start_generation)
        self.addAction(run)
        help_menu = self.menuBar().addMenu("&Help")
        self.about_action = help_menu.addAction("About / Credits")
        self.about_action.triggered.connect(self.show_credits)

    def show_credits(self):
        dialog = CreditsDialog(self)
        return dialog.exec()

    def _build_ui(self):
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        top = QWidget()
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(24, 18, 24, 18)
        titles = QVBoxLayout()
        title = QLabel("Create your next beatmap")
        title.setObjectName("title")
        subtitle = QLabel("Audio to osu!  ·  Mapperatorinator Desktop")
        subtitle.setObjectName("subtitle")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        top_layout.addLayout(titles, 1)
        self.model_badge = QLabel("V32 / Standard")
        self.model_badge.setObjectName("subtitle")
        top_layout.addWidget(self.model_badge)
        outer.addWidget(top)
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(182)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(16, 22, 16, 18)
        brand = QLabel("MAPPERATORINATOR")
        brand.setObjectName("brand")
        brand.setStyleSheet("font-size: 11px; letter-spacing: 1px;")
        side_layout.addWidget(brand)
        self.navigation = QListWidget()
        self.navigation.setObjectName("navigation")
        self.navigation.setAccessibleName("Settings pages")
        self.navigation.addItems(["Generation", "Metadata", "Style", "Advanced"])
        side_layout.addWidget(self.navigation, 1)
        for label, callback in (("Import settings…", self.import_settings), ("Export settings…", self.export_settings), ("Reset to defaults", self.reset_settings)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            side_layout.addWidget(button)
        note = QLabel("Always disclose AI use\nin your beatmaps.")
        note.setObjectName("hint")
        note.setWordWrap(True)
        side_layout.addWidget(note)
        body.addWidget(sidebar)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.pages = QStackedWidget()
        self._generation_page()
        self._metadata_page()
        self._style_page()
        self._advanced_page()
        self.splitter.addWidget(self.pages)
        self.splitter.addWidget(self._console())
        self.splitter.setSizes([690, 390])
        self.splitter.setChildrenCollapsible(False)
        body.addWidget(self.splitter, 1)
        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.navigation.setCurrentRow(0)
        outer.addLayout(body, 1)
        footer = QWidget()
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(24, 12, 24, 12)
        self.error_label = QLabel()
        self.error_label.setObjectName("error")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        footer_layout.addWidget(self.error_label)
        actions = QHBoxLayout()
        self.summary_label = QLabel("Ready to generate")
        self.summary_label.setObjectName("hint")
        self.summary_label.setWordWrap(True)
        actions.addWidget(self.summary_label, 1)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_generation)
        actions.addWidget(self.cancel_button)
        self.generate_button = QPushButton("Generate beatmap")
        self.generate_button.setObjectName("primary")
        self.generate_button.setToolTip("Start generation (Ctrl+Enter)")
        self.generate_button.clicked.connect(self.start_generation)
        actions.addWidget(self.generate_button)
        footer_layout.addLayout(actions)
        outer.addWidget(footer)
        self.setCentralWidget(central)
        self.statusBar().showMessage("Ready. Drop an audio file or a reference .osu file into the window.")

    def _page(self, title, description):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(24, 12, 24, 24)
        layout.setSpacing(12)
        label = QLabel(title)
        label.setObjectName("sectionTitle")
        layout.addWidget(label)
        hint = QLabel(description)
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        scroll.setWidget(container)
        self.pages.addWidget(scroll)
        return layout

    def _form(self, layout, title=None):
        if title:
            heading = QLabel(title)
            heading.setObjectName("sectionTitle")
            layout.addWidget(heading)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(12)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        layout.addLayout(form)
        self.forms.append(form)
        return form

    def _field(self, form, key, label, placeholder="", tooltip="", choices=None, check=False, path=None):
        if path:
            widget = PathEdit(label, **path)
        elif choices:
            widget = QComboBox()
            for text, value in choices:
                widget.addItem(text, value)
        elif check:
            widget = QCheckBox(label)
        else:
            widget = QLineEdit()
            widget.setClearButtonEnabled(True)
            widget.setPlaceholderText(placeholder or "Automatic / inherit")
        widget.setObjectName(key)
        widget.setAccessibleName(label)
        if tooltip:
            widget.setToolTip(tooltip)
        self.fields[key] = widget
        if check:
            form.addRow("", widget)
        else:
            form.addRow(label, widget)
        self.rows[key] = form
        return widget

    def _generation_page(self):
        layout = self._page("Generation", "Choose your source and the beatmap you want to create. Blank optional fields use reference values or model defaults.")
        form = self._form(layout, "Files")
        self._field(form, "audio_path", "Audio file", path={"file_filter": "Audio (*.mp3 *.wav *.ogg *.m4a *.flac);;All files (*)"})
        self._field(form, "output_path", "Output folder", path={"folder": True}, tooltip="Leave blank to save beside the reference beatmap, or beside the audio file.")
        self.fields["output_path"].edit.setPlaceholderText("Automatic: reference or audio folder")
        self._field(form, "beatmap_path", "Reference beatmap", path={"file_filter": "osu! beatmap (*.osu)"}, tooltip="Optional .osu file for metadata, timing, or remapping.")
        reference_actions = QHBoxLayout()
        self.reference_button = QPushButton("Fill fields from reference")
        self.reference_button.clicked.connect(self.fill_reference)
        reference_actions.addWidget(self.reference_button)
        reference_actions.addStretch()
        layout.addLayout(reference_actions)
        self.reference_hint = QLabel("Select a reference to reuse its timing or fill metadata.")
        self.reference_hint.setObjectName("hint")
        self.reference_hint.setWordWrap(True)
        layout.addWidget(self.reference_hint)
        self.context_box = QWidget()
        context_layout = QVBoxLayout(self.context_box)
        context_layout.setContentsMargins(0, 0, 0, 0)
        context_layout.addWidget(QLabel("Use from reference"))
        for key, label in (("TIMING", "Timing"), ("KIAI", "Kiai sections"), ("GD", "Guest difficulty context"), ("NO_HS", "Beatmap without hitsounds"), ("MAP", "Full beatmap context")):
            checkbox = QCheckBox(label)
            self.contexts[key] = checkbox
            context_layout.addWidget(checkbox)
        layout.addWidget(self.context_box)
        form = self._form(layout, "Beatmap")
        self._field(form, "model", "Model", choices=[(value, key) for key, value in MODELS.items()])
        self._field(form, "gamemode", "Game mode", choices=[("osu! / Standard", 0), ("osu!taiko", 1), ("osu!catch", 2), ("osu!mania", 3)])
        self._field(form, "difficulty", "Star rating", "5.0", "Target star rating. Leave blank to inherit or let the model choose.")
        self._field(form, "year", "Style year", "2007–2024", "Year of mapping style to simulate. V32 supports 2007–2024.")
        self._field(form, "export_osz", "Export .osz archive", check=True, tooltip="Package the generated beatmap and audio for importing into osu!.")
        self._field(form, "hitsounded", "Generate hitsounds", check=True)
        self._field(form, "super_timing", "Use super timing", check=True, tooltip="Slower, more accurate variable BPM timing. Reference timing takes precedence.")
        self._field(form, "add_to_beatmap", "Merge generated content into reference", check=True)
        self._field(form, "overwrite_reference_beatmap", "Overwrite reference file", check=True, tooltip="Requires merge mode. You will be asked to confirm before generation.")
        layout.addStretch()

    def _metadata_page(self):
        layout = self._page("Metadata", "Set the song information and difficulty settings written to the generated beatmap. Leave fields blank to inherit them.")
        form = self._form(layout, "Song information")
        for key, label in (("title", "Title"), ("title_unicode", "Title (Unicode)"), ("artist", "Artist"), ("artist_unicode", "Artist (Unicode)"), ("creator", "Beatmap creator"), ("version", "Difficulty name"), ("source", "Source"), ("tags", "Tags")):
            self._field(form, key, label)
        self._field(form, "preview_time", "Preview time (ms)", "Automatic (−1 for no preview)")
        self._field(form, "background_image", "Background image", path={"file_filter": "Images (*.jpg *.jpeg *.png *.bmp);;All files (*)"})
        form = self._form(layout, "Difficulty settings")
        for key, label, placeholder in (
            ("hp_drain_rate", "HP drain rate", "0–10"),
            ("circle_size", "Circle size", "0–10"),
            ("keycount", "Mania key count", "1–18"),
            ("overall_difficulty", "Overall difficulty", "0–10"),
            ("approach_rate", "Approach rate", "0–10"),
            ("slider_multiplier", "Slider velocity", "e.g. 1.4"),
            ("slider_tick_rate", "Slider tick rate", "e.g. 1"),
            ("hold_note_ratio", "Hold note ratio", "0–1"),
            ("scroll_speed_ratio", "Scroll speed ratio", "0–1"),
        ):
            self._field(form, key, label, placeholder)
        layout.addStretch()

    def _style_page(self):
        layout = self._page("Style", "Guide the model toward a mapper or a set of descriptors. Negative descriptors take effect when guidance scale is greater than 1.")
        form = self._form(layout)
        self._field(form, "mapper_id", "Mapper user ID", "Optional osu! user ID")
        self._field(form, "cfg_scale", "Guidance scale", "1.0", "Higher values strengthen mapper and descriptor guidance.")
        self._field(form, "lora_path", "LoRA adapter folder", path={"folder": True}, tooltip="Optional local adapter folder. A weights file path may also be pasted here.")
        self.descriptor_panel = DescriptorPanel()
        layout.addWidget(self.descriptor_panel)
        self.style_hint = QLabel()
        self.style_hint.setObjectName("hint")
        self.style_hint.setWordWrap(True)
        layout.addWidget(self.style_hint)
        layout.addStretch()

    def _advanced_page(self):
        layout = self._page("Advanced", "Control sampling, reproducibility, timing, and the inference device. The backend resolves automatic device selection when generation starts.")
        form = self._form(layout, "Sampling")
        self._field(form, "seed", "Random seed", "Random each run")
        self._field(form, "temperature", "Temperature", "1.0", "Sampling temperature from 0 to 1. Zero selects the most likely tokens.")
        self._field(form, "top_p", "Top-p", "0.9", "Nucleus sampling threshold from 0 to 1.")
        form = self._form(layout, "Generation range")
        self._field(form, "output_type", "Generate", choices=[("Model default (full beatmap)", None), ("Timing only", ["TIMING"])])
        self._field(form, "start_time", "Start time (ms)", "Beginning of song")
        self._field(form, "end_time", "End time (ms)", "End of song")
        form = self._form(layout, "Compute")
        self._field(form, "device", "Device", choices=[("Automatic", "auto"), ("NVIDIA / AMD GPU (CUDA)", "cuda"), ("CPU", "cpu"), ("Apple GPU (MPS)", "mps")])
        self._field(form, "precision", "Precision", choices=[("FP32 — full precision", "fp32"), ("BF16 — supported GPUs", "bf16"), ("FP16 — half precision", "fp16"), ("AMP — mixed precision", "amp")])
        self._field(form, "fast_decoder_loop", "Use fast decoder loop", check=True, tooltip="CUDA optimization. The backend falls back where unsupported.")
        self._field(form, "super_timing_fast_loop", "Use fast decoder for super timing", check=True)
        note = QLabel("Generation runs one job at a time. The model loads for each run and GPU memory is released when the worker exits. The first run may download model weights.")
        note.setObjectName("hint")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch()

    def _console(self):
        panel = QWidget()
        panel.setMinimumWidth(270)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(18, 12, 18, 18)
        title = QLabel("Generation activity")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        self.stage_label = QLabel("Ready")
        self.stage_label.setWordWrap(True)
        layout.addWidget(self.stage_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        layout.addWidget(self.progress_bar)
        self.elapsed_label = QLabel("No generation running")
        self.elapsed_label.setObjectName("hint")
        layout.addWidget(self.elapsed_label)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setAccessibleName("Generation log")
        self.log_view.setPlaceholderText("Model loading, generation progress, and diagnostics appear here.")
        self.log_view.setMaximumBlockCount(2500)
        font = QFont("Consolas" if os.name == "nt" else "Monospace", 10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.log_view.setFont(font)
        layout.addWidget(self.log_view, 1)
        self.result_label = QLabel()
        self.result_label.setObjectName("hint")
        self.result_label.setWordWrap(True)
        self.result_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.result_label.hide()
        layout.addWidget(self.result_label)
        controls = QHBoxLayout()
        self.output_button = QPushButton("Open output")
        self.output_button.setEnabled(False)
        self.output_button.clicked.connect(self.open_output)
        controls.addWidget(self.output_button)
        self.log_button = QPushButton("Open log")
        self.log_button.setEnabled(False)
        self.log_button.clicked.connect(self.open_log)
        controls.addWidget(self.log_button)
        layout.addLayout(controls)
        return panel

    def settings(self):
        values = {}
        for key, widget in self.fields.items():
            if isinstance(widget, PathEdit):
                values[key] = widget.edit.text().strip()
            elif isinstance(widget, QCheckBox):
                values[key] = widget.isChecked()
            elif isinstance(widget, QComboBox):
                values[key] = widget.currentData()
            elif isinstance(widget, QPlainTextEdit):
                values[key] = [part.strip() for part in widget.toPlainText().replace(",", "\n").splitlines() if part.strip()]
            else:
                values[key] = widget.text().strip()
        values["descriptors"], values["negative_descriptors"] = self.descriptor_panel.selections()
        values["in_context"] = [key for key, widget in self.contexts.items() if widget.isChecked() and widget.isEnabled()]
        if not values["beatmap_path"]:
            values["add_to_beatmap"] = False
        if not values["beatmap_path"] or not values["add_to_beatmap"]:
            values["overwrite_reference_beatmap"] = False
        return values

    def apply_settings(self, settings):
        values = load_settings(settings)
        for key, widget in self.fields.items():
            value = values.get(key, "")
            widget.blockSignals(True)
            if isinstance(widget, PathEdit):
                widget.edit.setText(str(value) if value is not None else "")
            elif isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            elif isinstance(widget, QComboBox):
                index = widget.findData(value)
                if index < 0:
                    index = widget.findData(str(value))
                if index < 0 and key == "gamemode":
                    try:
                        index = widget.findData(int(value))
                    except (ValueError, TypeError):
                        pass
                if index >= 0:
                    widget.setCurrentIndex(index)
            elif isinstance(widget, QPlainTextEdit):
                widget.setPlainText("\n".join(value) if isinstance(value, list) else str(value or ""))
            else:
                widget.setText(str(value) if value is not None else "")
            widget.blockSignals(False)
        for key, widget in self.contexts.items():
            widget.setChecked(key in values.get("in_context", []))
        self._update_capabilities()
        self.descriptor_panel.set_selections(values.get("descriptors"), values.get("negative_descriptors"))

    def _row_visible(self, key, visible):
        if key in self.rows:
            self.rows[key].setRowVisible(self.fields[key], visible)

    def _update_capabilities(self, *_):
        model = self.fields["model"].currentData()
        mode_widget = self.fields["gamemode"]
        mode = mode_widget.currentData()
        for index in range(mode_widget.count()):
            mode_widget.model().item(index).setEnabled(model != "v30" or index == 0)
        if model == "v30" and mode != 0:
            mode_widget.setCurrentIndex(0)
            mode = 0
        reference = bool(self.fields["beatmap_path"].edit.text().strip())
        self.reference_button.setEnabled(reference)
        self.context_box.setVisible(reference)
        supported = {"TIMING"} if model in ("v30", "v32", "v32-mini") else set(self.contexts)
        for key, checkbox in self.contexts.items():
            checkbox.setEnabled(reference and key in supported)
            checkbox.setVisible(key in supported)
        self._row_visible("year", model != "v30")
        self._row_visible("hitsounded", model != "v30")
        self._row_visible("keycount", mode == 3)
        self._row_visible("circle_size", mode == 0)
        self._row_visible("hold_note_ratio", mode == 3)
        self._row_visible("scroll_speed_ratio", mode in (1, 3))
        self._row_visible("approach_rate", mode in (0, 2))
        self._row_visible("add_to_beatmap", reference)
        self._row_visible("overwrite_reference_beatmap", reference and self.fields["add_to_beatmap"].isChecked())
        self.descriptor_panel.set_catalog(model, mode)
        self.style_hint.setText("V30 uses mapper guidance and supports Standard mode only; it does not use year or descriptors." if model == "v30" else "Descriptors match the Web UI for this model and game mode. Negative guidance requires a guidance scale above 1 and equal Encourage and Discourage counts.")
        self.reference_hint.setText("Reference values fill blank fields. Use “Fill fields from reference” to replace the visible metadata and difficulty settings." if reference else "An audio file alone is enough. Output defaults to the audio folder.")
        self.model_badge.setText(f"{model.upper()} / {mode_widget.currentText()}")

    def fill_reference(self):
        try:
            values = read_reference(self.fields["beatmap_path"].edit.text())
            settings = self.settings()
            settings.update({key: value for key, value in values.items() if key in self.fields and value is not None})
            self.apply_settings(settings)
            self.error_label.hide()
            self.statusBar().showMessage("Filled available fields from the reference beatmap.", 6000)
        except (ValueError, OSError) as exc:
            self._show_error(str(exc))

    def _choose_audio(self):
        self.fields["audio_path"].browse()

    def import_settings(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import generation settings", str(ROOT), "JSON settings (*.json)")
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict):
                raise ValueError("Settings must be a JSON object.")
            self.apply_settings(data)
            self.error_label.hide()
            self.statusBar().showMessage(f"Imported settings from {path}", 7000)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self._show_error(f"Could not import settings: {exc}")

    def export_settings(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export generation settings", str(ROOT / "mapperatorinator-settings.json"), "JSON settings (*.json)")
        if not path:
            return
        try:
            Path(path).write_text(json.dumps(export_settings(self.settings()), ensure_ascii=False, indent=2), encoding="utf-8")
            self.statusBar().showMessage(f"Exported settings to {path}", 7000)
        except (OSError, ValueError) as exc:
            self._show_error(f"Could not export settings: {exc}")

    def reset_settings(self):
        self.apply_settings(dict(DEFAULTS))
        self.error_label.hide()
        self.statusBar().showMessage("Generation settings reset to defaults.", 6000)

    def _show_error(self, message):
        self.error_label.setText(message)
        self.error_label.show()
        self.statusBar().showMessage(message, 10000)

    def start_generation(self):
        if self.job.running:
            return
        values = self.settings()
        try:
            overrides = validate_settings(values)
        except (ValueError, OSError, TypeError) as exc:
            self._show_error(str(exc))
            return
        if overrides.get("overwrite_reference_beatmap"):
            answer = QMessageBox.warning(
                self, "Overwrite reference beatmap?",
                f"Generation will replace this reference file:\n\n{overrides['beatmap_path']}\n\nKeep a backup if you want to preserve the original.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.error_label.hide()
        self.log_view.clear()
        self.result_label.hide()
        self.output_button.setEnabled(False)
        self._last_output_dir = overrides["output_path"]
        self.progress_bar.setRange(0, 0)
        self._elapsed_seconds = 0
        self._set_busy(True)
        self.summary_label.setText(f"Generating with {values['model'].upper()} · settings captured for this run")
        try:
            self.job.start(values["model"], overrides)
        except (OSError, RuntimeError, ValueError) as exc:
            self._set_busy(False)
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(0)
            self._set_stage("Could not start generation")
            self._show_error(str(exc))
            return
        self.log_button.setEnabled(True)
        self.elapsed_label.setText("Elapsed 0:00")
        self.clock.start()
        self._save_preferences()

    def _set_busy(self, busy):
        self.generate_button.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)

    def cancel_generation(self):
        self.cancel_button.setEnabled(False)
        self.job.cancel()

    def _set_stage(self, text):
        self.stage_label.setText(text)

    def _set_progress(self, value):
        if self.job.running:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(value)

    def _append_log(self, line):
        bar = self.log_view.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 4
        self.log_view.appendPlainText(line)
        if at_end:
            bar.setValue(bar.maximum())

    def _tick(self):
        self._elapsed_seconds += 1
        minutes, seconds = divmod(self._elapsed_seconds, 60)
        self.elapsed_label.setText(f"Elapsed {minutes}:{seconds:02d}")

    def _job_finished(self, status, message):
        self.clock.stop()
        self._set_busy(False)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100 if status == "completed" else 0)
        self._set_stage({"completed": "Complete", "cancelled": "Cancelled", "failed": "Failed"}[status])
        self.summary_label.setText(message)
        self._append_log(message)
        self.log_button.setEnabled(bool(self.job.log_path and self.job.log_path.is_file()))
        self.output_button.setEnabled(bool(self._last_output_dir and Path(self._last_output_dir).is_dir()))
        if status == "completed" and self.job.result_path:
            self.result_label.setText(f"Saved to\n{self.job.result_path}")
            self.result_label.show()
            self._last_output_dir = str(Path(self.job.result_path).parent)
            self.output_button.setEnabled(Path(self._last_output_dir).is_dir())
        elif status == "failed":
            self._show_error(message)
        self.statusBar().showMessage(message, 10000)
        if self._closing:
            QTimer.singleShot(0, self.close)

    def open_output(self):
        if self._last_output_dir:
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self._last_output_dir).resolve()))):
                self._show_error("Could not open the output folder.")

    def open_log(self):
        if self.job.log_path:
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.job.log_path))):
                self._show_error(f"Could not open the log: {self.job.log_path}")

    def _save_preferences(self):
        self.preferences.setValue("generationSettings", json.dumps(export_settings(self.settings()), ensure_ascii=False))
        self.preferences.setValue("geometry", self.saveGeometry())
        self.preferences.sync()

    def closeEvent(self, event):
        if self.job.running:
            if not self._closing:
                answer = QMessageBox.question(
                    self, "Stop generation and exit?", "A generation is running. Stop it and close the desktop app?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    event.ignore()
                    return
                self._closing = True
                self.cancel_generation()
                self.setEnabled(False)
            event.ignore()
            return
        self._save_preferences()
        event.accept()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = Path(url.toLocalFile())
            if path.suffix.lower() == ".osu":
                self.fields["beatmap_path"].edit.setText(str(path))
            elif path.suffix.lower() in {".mp3", ".wav", ".ogg", ".m4a", ".flac"}:
                self.fields["audio_path"].edit.setText(str(path))
        self.navigation.setCurrentRow(0)
        event.acceptProposedAction()


def run():
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Mapperatorinator Desktop")
    app.setOrganizationName("Mapperatorinator")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    window = MainWindow()
    app.aboutToQuit.connect(window.job.shutdown)
    window.show()
    return app.exec()
