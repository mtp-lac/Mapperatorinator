"""Grouped, three-state descriptor controls for the native Style page."""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QStyle, QStyleOptionButton, QVBoxLayout, QWidget,
)

from desktop.core import descriptor_catalog


class DescriptorCheckBox(QCheckBox):
    def __init__(self, descriptor, parent=None):
        super().__init__(descriptor["label"], parent)
        self.descriptor = descriptor
        self.setObjectName("descriptor")
        self.setTristate(True)
        self.checkStateChanged.connect(self._describe_state)
        self._describe_state()

    def hitButton(self, position):
        return self.rect().contains(position)

    def nextCheckState(self):
        states = (Qt.CheckState.Unchecked, Qt.CheckState.Checked, Qt.CheckState.PartiallyChecked)
        self.setCheckState(states[(states.index(self.checkState()) + 1) % len(states)])

    def _describe_state(self, *_):
        state = {Qt.CheckState.Unchecked: "Disabled", Qt.CheckState.Checked: "Encourage", Qt.CheckState.PartiallyChecked: "Discourage"}[self.checkState()]
        self.setAccessibleName(f"{self.descriptor['group_title']}: {self.text()}")
        self.setAccessibleDescription(f"{state}. Click or press Space to cycle Disabled, Encourage, Discourage.")
        description = self.descriptor.get("description", "")
        self.setToolTip(f"{state}\n{description}\nClick to cycle: Disabled → Encourage → Discourage.")
        self.update()

    def paintEvent(self, event):
        option = QStyleOptionButton()
        self.initStyleOption(option)
        rect = self.style().subElementRect(QStyle.SubElement.SE_CheckBoxIndicator, option, self)
        label_option = QStyleOptionButton(option)
        if self.layoutDirection() == Qt.LayoutDirection.RightToLeft:
            label_option.rect.setRight(rect.left() - 10)
        else:
            label_option.rect.setLeft(rect.right() + 10)
        painter = QPainter(self)
        self.style().drawControl(QStyle.ControlElement.CE_CheckBoxLabel, label_option, painter, self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        state = self.checkState()
        color = QColor("#83d6ae" if state == Qt.CheckState.Checked else "#f1a1b0" if state == Qt.CheckState.PartiallyChecked else "#444955")
        painter.setPen(QPen(color, 1.5))
        painter.setBrush(QColor("#243b32" if state == Qt.CheckState.Checked else "#412b33" if state == Qt.CheckState.PartiallyChecked else "#292b30"))
        painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 2, 2)
        if state != Qt.CheckState.Unchecked:
            painter.setPen(QPen(color, 2))
            center = rect.center()
            painter.drawLine(center.x() - 4, center.y(), center.x() + 4, center.y())
            if state == Qt.CheckState.Checked:
                painter.drawLine(center.x(), center.y() - 4, center.x(), center.y() + 4)


class DescriptorPanel(QWidget):
    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items = {}
        self.groups = []
        self._catalog_key = None
        self._states = {}
        self._columns = 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 0)
        legend = QLabel("Click a checkbox to cycle:  □ Disabled   →   + Encourage   →   − Discourage")
        legend.setObjectName("hint")
        legend.setWordWrap(True)
        layout.addWidget(legend)
        controls = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search descriptors…")
        self.search.setAccessibleName("Search descriptors")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        controls.addWidget(self.search, 1)
        clear = QPushButton("Clear selections")
        clear.clicked.connect(lambda: self.set_selections([], []))
        controls.addWidget(clear)
        layout.addLayout(controls)
        self.summary = QLabel()
        self.summary.setObjectName("hint")
        layout.addWidget(self.summary)
        self.notice = QLabel()
        self.notice.setObjectName("hint")
        self.notice.setWordWrap(True)
        self.notice.hide()
        layout.addWidget(self.notice)
        self.cards = QWidget()
        self.grid = QGridLayout(self.cards)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(14)
        layout.addWidget(self.cards)
        self.empty = QLabel()
        self.empty.setObjectName("hint")
        self.empty.setWordWrap(True)
        layout.addWidget(self.empty)

    def set_catalog(self, model, gamemode):
        if self._catalog_key == (model, gamemode):
            return
        positive = [value for value, state in self._states.items() if state == Qt.CheckState.Checked]
        negative = [value for value, state in self._states.items() if state == Qt.CheckState.PartiallyChecked]
        self._catalog_key = (model, gamemode)
        while self.grid.count():
            self.grid.takeAt(0)
        for _, card, _ in self.groups:
            card.hide()
            card.deleteLater()
        self.items = {}
        self.groups = []
        grouped = {}
        for descriptor in descriptor_catalog(model, gamemode):
            grouped.setdefault(descriptor["group"], []).append(descriptor)
        for key, descriptors in grouped.items():
            card = QFrame(self.cards)
            card.setObjectName("descriptorCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(10, 10, 10, 12)
            card_layout.setSpacing(4)
            heading = QLabel(descriptors[0]["group_title"])
            heading.setObjectName("descriptorHeading")
            card_layout.addWidget(heading)
            checkboxes = []
            for descriptor in descriptors:
                checkbox = DescriptorCheckBox(descriptor)
                checkbox.checkStateChanged.connect(self._selection_changed)
                card_layout.addWidget(checkbox)
                self.items[descriptor["value"]] = checkbox
                checkboxes.append(checkbox)
            card_layout.addStretch()
            self.groups.append((key, card, checkboxes))
        self.setEnabled(model != "v30")
        self.empty.setText("V30 does not support descriptors." if model == "v30" else "No descriptors are available for this model and game mode. Check that the descriptor dataset is installed.")
        self.set_selections(positive, negative)
        self._filter()

    def set_selections(self, positive, negative):
        positive = set(positive or [])
        negative = set(negative or [])
        self._states = {value: Qt.CheckState.Checked for value in positive}
        self._states.update({value: Qt.CheckState.PartiallyChecked for value in negative})
        unavailable = (positive | negative) - self.items.keys()
        for value, checkbox in self.items.items():
            checkbox.blockSignals(True)
            checkbox.setCheckState(self._states.get(value, Qt.CheckState.Unchecked))
            checkbox.blockSignals(False)
            checkbox._describe_state()
        self.notice.setText("Unavailable selections are inactive for this model and game mode: " + ", ".join(sorted(unavailable)))
        self.notice.setVisible(bool(unavailable))
        self._selection_changed()

    def selections(self):
        return (
            [value for value, checkbox in self.items.items() if checkbox.checkState() == Qt.CheckState.Checked],
            [value for value, checkbox in self.items.items() if checkbox.checkState() == Qt.CheckState.PartiallyChecked],
        )

    def _selection_changed(self, *_):
        for value, checkbox in self.items.items():
            if checkbox.checkState() == Qt.CheckState.Unchecked:
                self._states.pop(value, None)
            else:
                self._states[value] = checkbox.checkState()
        positive, negative = self.selections()
        self.summary.setText(f"{len(positive)} Encourage  ·  {len(negative)} Discourage  ·  {len(self.items)} available")
        self.changed.emit()

    def _filter(self, *_):
        text = self.search.text().strip().casefold()
        for _, card, checkboxes in self.groups:
            for checkbox in checkboxes:
                descriptor = checkbox.descriptor
                matches = text in " ".join(str(descriptor.get(key, "")) for key in ("value", "label", "description", "group_title")).casefold()
                checkbox.setVisible(matches)
            card.setVisible(any(not checkbox.isHidden() for checkbox in checkboxes))
        self.empty.setVisible(not any(not card.isHidden() for _, card, _ in self.groups))
        if self.items:
            self.empty.setText("No descriptors match your search.")
        self._arrange()

    def _arrange(self):
        columns = max(1, min(4, (self.width() + 14) // 215))
        while self.grid.count():
            self.grid.takeAt(0)
        for column in range(self._columns):
            self.grid.setColumnStretch(column, 0)
        visible = [card for _, card, _ in self.groups if not card.isHidden()]
        for index, card in enumerate(visible):
            self.grid.addWidget(card, index // columns, index % columns)
        for column in range(columns):
            self.grid.setColumnStretch(column, 1)
        self._columns = columns

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._arrange()
