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
from ..codecs import USER_FORMATS, capability
from ..core import pipeline

FORMATS = list(USER_FORMATS)


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
            self.fmt.addItem(fmt)
            if fmt in {"auto", "avif"}:
                continue
            cap = capability(fmt)
            if not cap.available:
                idx = self.fmt.count() - 1
                self.fmt.model().item(idx).setEnabled(False)
                self.fmt.setItemData(idx, cap.reason or cap.status, Qt.ToolTipRole)
        self.fmt.setCurrentText(self.config.get("default_format"))
        form.addRow("Default format", self.fmt)

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
        root.addWidget(buttons)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Choose save folder", self.save_dir.text())
        if d:
            self.save_dir.setText(d)

    def _save(self):
        # Validate the template + hotkeys before persisting.
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
        c.set("default_format", self.fmt.currentText())
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
