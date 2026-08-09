"""Preferences window (issue #5): edits and persists the JSON config.

Covers default format, save directory + filename template, capture hotkeys,
gain-map quality/downscale, clipboard behaviour, notifications and run-at-login.
Validates the filename template and hotkeys before saving.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import hotkeys, startup
from ..config import CANONICAL_FORMATS, InvalidFormatError, validate_format
from ..core import pipeline

FORMATS = list(CANONICAL_FORMATS)

FORMAT_LABELS = {
    "auto": "Auto",
    "uhdr-jpeg": "UltraHDR JPEG",
    "uhdr-avif": "UltraHDR AVIF",
    "uhdr-heic": "UltraHDR HEIC",
    "pq-avif": "AVIF (PQ)",
    "pq-heic": "HEIC (PQ)",
    "exr": "OpenEXR",
    "png": "PNG",
    "jpeg": "JPEG",
    "avif-sdr": "AVIF (SDR)",
}


class PreferencesDialog(QDialog):
    def __init__(self, config, on_apply=None, parent=None):
        super().__init__(parent)
        self.config = config
        self.on_apply = on_apply
        self.setWindowTitle("HDR Shot — Preferences")
        self.setObjectName("preview")
        self.setMinimumWidth(480)
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(10)

        self.fmt = QComboBox()
        for fmt in FORMATS:
            self.fmt.addItem(FORMAT_LABELS.get(fmt, fmt), fmt)
            checked = validate_format(fmt, allow_legacy=False)
            if not checked.available:
                idx = self.fmt.count() - 1
                self.fmt.model().item(idx).setEnabled(False)
                self.fmt.setItemData(idx, checked.message(), Qt.ToolTipRole)
        self._select_saved_format()
        self.fmt.currentIndexChanged.connect(self._update_format_state)
        form.addRow("Default format", self.fmt)
        self.format_hint = QLabel("")
        self.format_hint.setObjectName("subtle")
        form.addRow("", self.format_hint)

        self.save_dir = QLineEdit(self.config.get("save_dir"))
        self.save_dir.setPlaceholderText("(default: Pictures\\Screenshots)")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.save_dir, 1)
        row.addWidget(browse)
        wrap = QWidget()
        wrap.setLayout(row)
        form.addRow("Save folder", wrap)

        self.template = QLineEdit(self.config.get("filename_template"))
        form.addRow("Filename template", self.template)
        hint = QLabel("Tokens: {date} {time} {display} {format} {hdr} {n}")
        hint.setObjectName("subtle")
        form.addRow("", hint)

        self.hk_region = QLineEdit(self.config.get("hotkey_region"))
        form.addRow("Region hotkey", self.hk_region)
        self.hk_screen = QLineEdit(self.config.get("hotkey_screen"))
        form.addRow("Whole-screen hotkey", self.hk_screen)

        self.quality = QSpinBox()
        self.quality.setRange(50, 100)
        self.quality.setValue(int(self.config.get("gainmap_quality")))
        form.addRow("UltraHDR quality", self.quality)

        self.downscale = QSpinBox()
        self.downscale.setRange(1, 4)
        self.downscale.setValue(int(self.config.get("gainmap_downscale")))
        self.downscale.setToolTip("Gain-map resolution divisor (1 = full res)")
        form.addRow("Gain-map downscale", self.downscale)

        self.copy_clip = QCheckBox("Also copy the image to the clipboard on save")
        self.copy_clip.setChecked(bool(self.config.get("copy_to_clipboard")))
        form.addRow("", self.copy_clip)

        self.notify = QCheckBox("Show a notification after each capture")
        self.notify.setChecked(bool(self.config.get("notifications")))
        form.addRow("", self.notify)

        self.run_login = QCheckBox("Start HDR Shot when I sign in")
        self.run_login.setChecked(startup.is_enabled())
        form.addRow("", self.run_login)

        root.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        self._save_button = buttons.button(QDialogButtonBox.Save)
        root.addWidget(buttons)
        self._update_format_state()

    def _select_saved_format(self):
        checked = self.config.format_validation()
        if checked.valid and checked.canonical:
            for index in range(self.fmt.count()):
                if self.fmt.itemData(index) == checked.canonical:
                    # Deliberately select an unavailable saved profile too.  It
                    # must remain visible rather than becoming Auto or item 0.
                    self.fmt.setCurrentIndex(index)
                    break
        elif checked.value is not None:
            self.fmt.addItem(f"Invalid saved value: {checked.value}", checked.value)
            index = self.fmt.count() - 1
            self.fmt.model().item(index).setEnabled(False)
            self.fmt.setItemData(index, checked.message(), Qt.ToolTipRole)
            self.fmt.setCurrentIndex(index)

    def _update_format_state(self):
        checked = validate_format(self.fmt.currentData(), allow_legacy=False)
        if checked.selectable:
            self.format_hint.setText("")
            self._save_button.setEnabled(True)
        else:
            self.format_hint.setText(checked.message())
            self._save_button.setEnabled(False)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Choose save folder", self.save_dir.text())
        if d:
            self.save_dir.setText(d)

    def _save(self):
        # Validate the template + hotkeys before persisting.
        checked = validate_format(self.fmt.currentData(), allow_legacy=False)
        if not checked.valid or not checked.selectable:
            QMessageBox.warning(self, "Unavailable default format", checked.message())
            return
        try:
            pipeline.validate_template(self.template.text())
        except ValueError as e:
            QMessageBox.warning(self, "Invalid filename template", str(e))
            return
        for label, field in (("Region", self.hk_region), ("Whole-screen", self.hk_screen)):
            try:
                hotkeys.parse_hotkey(field.text())
            except hotkeys.HotkeyError as e:
                QMessageBox.warning(self, f"Invalid {label} hotkey", str(e))
                return

        c = self.config
        try:
            c.set("default_format", checked.canonical)
        except InvalidFormatError as e:
            QMessageBox.warning(self, "Invalid default format", e.validation.message())
            return
        c.set("save_dir", self.save_dir.text().strip())
        c.set("filename_template", self.template.text())
        c.set("hotkey_region", self.hk_region.text())
        c.set("hotkey_screen", self.hk_screen.text())
        c.set("gainmap_quality", self.quality.value())
        c.set("gainmap_downscale", self.downscale.value())
        c.set("copy_to_clipboard", self.copy_clip.isChecked())
        c.set("notifications", self.notify.isChecked())
        c.set("run_at_login", self.run_login.isChecked())
        startup.set_enabled(self.run_login.isChecked())
        c.save()
        if self.on_apply:
            self.on_apply()
        self.accept()
