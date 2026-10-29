import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)
from qgis.core import Qgis, QgsApplication, QgsMessageLog, QgsProject, QgsSettings
from qgis.gui import QgsFileWidget, QgsFilterLineEdit, QgsMapLayerComboBox

from . import importer

SETTINGS_DIR = "import_layouts/last_dir"
LOG_TAG = "Import Layouts"

COL_SOURCE, COL_USED, COL_DEST, COL_MATCH = range(4)


class ImportLayoutsDialog(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.source = None
        self.mapping = {}  # source layer id -> (dest layer id or None, method)
        self._filling = False

        self.setWindowTitle("Import Layouts from Project")
        self.setWindowIcon(QgsApplication.getThemeIcon("/mActionNewLayout.svg"))
        self.resize(820, 620)

        layout = QVBoxLayout(self)

        # --- source project ---
        row = QHBoxLayout()
        row.addWidget(QLabel("Source project:"))
        self.file_widget = QgsFileWidget()
        self.file_widget.setFilter("QGIS projects (*.qgz *.qgs *.QGZ *.QGS)")
        self.file_widget.setDialogTitle("Select source QGIS project")
        self.file_widget.setDefaultRoot(QgsSettings().value(SETTINGS_DIR, ""))
        self.file_widget.fileChanged.connect(self.load_source)
        row.addWidget(self.file_widget, 1)
        layout.addLayout(row)

        splitter = QSplitter(Qt.Orientation.Vertical)
        layout.addWidget(splitter, 1)

        # --- layouts ---
        box = QGroupBox("Layouts to import")
        box_layout = QVBoxLayout(box)
        self.filter_edit = QgsFilterLineEdit()
        self.filter_edit.setPlaceholderText("Filter layouts…")
        self.filter_edit.textChanged.connect(self.apply_filter)
        box_layout.addWidget(self.filter_edit)
        self.layout_list = QListWidget()
        self.layout_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.layout_list.itemChanged.connect(self.update_mapping_table)
        self.layout_list.itemDoubleClicked.connect(self._toggle_item)
        box_layout.addWidget(self.layout_list)
        buttons = QHBoxLayout()
        for text, slot in (
            ("Select All", lambda: self.set_all_checked(True)),
            ("Deselect All", lambda: self.set_all_checked(False)),
            ("Check Highlighted", self.check_highlighted),
        ):
            b = QPushButton(text)
            b.clicked.connect(slot)
            buttons.addWidget(b)
        buttons.addStretch(1)
        self.count_label = QLabel()
        buttons.addWidget(self.count_label)
        box_layout.addLayout(buttons)
        splitter.addWidget(box)

        # --- layer mapping ---
        box = QGroupBox("Layer mapping (layers referenced by the checked layouts)")
        box_layout = QVBoxLayout(box)
        self.mapping_table = QTableWidget(0, 4)
        self.mapping_table.setHorizontalHeaderLabels(
            ["Source layer", "Used in", "Destination layer", "Match"])
        header = self.mapping_table.horizontalHeader()
        header.setSectionResizeMode(COL_SOURCE, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_USED, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_DEST, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_MATCH, QHeaderView.ResizeMode.ResizeToContents)
        self.mapping_table.verticalHeader().setVisible(False)
        self.mapping_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        box_layout.addWidget(self.mapping_table)
        splitter.addWidget(box)
        splitter.setSizes([300, 300])

        # --- options + buttons ---
        row = QHBoxLayout()
        row.addWidget(QLabel("If a layout with the same name exists:"))
        self.conflict_combo = QComboBox()
        self.conflict_combo.addItem("Import with a new name", importer.CONFLICT_RENAME)
        self.conflict_combo.addItem("Replace the existing layout", importer.CONFLICT_REPLACE)
        self.conflict_combo.addItem("Skip", importer.CONFLICT_SKIP)
        row.addWidget(self.conflict_combo)
        row.addStretch(1)
        layout.addLayout(row)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Close)
        self.import_button = self.button_box.button(QDialogButtonBox.StandardButton.Ok)
        self.import_button.setText("Import")
        self.import_button.setEnabled(False)
        self.button_box.accepted.connect(self.do_import)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

    # ------------------------------------------------------------------ source
    def refresh_destination(self):
        """Re-evaluate the mapping against the (possibly changed) current project."""
        self.mapping = {}
        self.update_mapping_table()

    def load_source(self, path):
        self.source = None
        self.mapping = {}
        self.layout_list.clear()
        self.mapping_table.setRowCount(0)
        if not path or not os.path.isfile(path):
            self.update_mapping_table()
            return
        QgsSettings().setValue(SETTINGS_DIR, os.path.dirname(path))
        try:
            self.source = importer.SourceProject(path)
        except Exception as e:  # noqa: BLE001 - show any parse/zip error to the user
            QMessageBox.critical(self, self.windowTitle(),
                                 "Could not read the project:\n{}".format(e))
            self.update_mapping_table()
            return

        layout_icon = QgsApplication.getThemeIcon("/mIconLayout.svg")
        report_icon = QgsApplication.getThemeIcon("/mIconReport.svg")
        self.layout_list.blockSignals(True)
        for src in sorted(self.source.layouts, key=lambda l: l.name.lower()):
            item = QListWidgetItem(report_icon if src.kind == "Report" else layout_icon, src.name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, src)
            if src.kind == "Report":
                item.setToolTip("Report")
            self.layout_list.addItem(item)
        self.layout_list.blockSignals(False)
        self.apply_filter(self.filter_edit.text())
        if not self.source.layouts:
            self.iface.messageBar().pushInfo(LOG_TAG, "The selected project contains no layouts.")
        self.update_mapping_table()

    # ----------------------------------------------------------------- layouts
    def _items(self):
        return [self.layout_list.item(i) for i in range(self.layout_list.count())]

    def checked_layouts(self):
        return [i.data(Qt.ItemDataRole.UserRole) for i in self._items()
                if i.checkState() == Qt.CheckState.Checked]

    def _set_checked(self, items, checked):
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self.layout_list.blockSignals(True)
        for item in items:
            item.setCheckState(state)
        self.layout_list.blockSignals(False)
        self.update_mapping_table()

    def set_all_checked(self, checked):
        self._set_checked([i for i in self._items() if not i.isHidden()], checked)

    def check_highlighted(self):
        selected = set(id(i) for i in self.layout_list.selectedItems())
        self.layout_list.blockSignals(True)
        for item in self._items():
            item.setCheckState(Qt.CheckState.Checked if id(item) in selected
                               else Qt.CheckState.Unchecked)
        self.layout_list.blockSignals(False)
        self.update_mapping_table()

    def _toggle_item(self, item):
        item.setCheckState(Qt.CheckState.Unchecked if item.checkState() == Qt.CheckState.Checked
                           else Qt.CheckState.Checked)

    def apply_filter(self, text):
        text = (text or "").lower()
        for item in self._items():
            item.setHidden(bool(text) and text not in item.text().lower())

    # ----------------------------------------------------------------- mapping
    def update_mapping_table(self, *_):
        layouts = self.checked_layouts() if self.source else []
        self.count_label.setText("{} of {} checked".format(len(layouts), self.layout_list.count()))
        self.import_button.setEnabled(bool(layouts))

        used_in = {}
        for src in layouts:
            for lid in src.layer_ids:
                used_in.setdefault(lid, []).append(src.name)

        missing = [lid for lid in used_in if lid not in self.mapping]
        if missing:
            self.mapping.update(importer.suggest_mapping(self.source, QgsProject.instance(), missing))

        self._filling = True
        table = self.mapping_table
        table.setRowCount(0)
        rows = sorted(used_in, key=lambda lid: self.source.layers[lid].name.lower())
        table.setRowCount(len(rows))
        for row, lid in enumerate(rows):
            src_layer = self.source.layers[lid]
            item = QTableWidgetItem(src_layer.name)
            item.setToolTip("ID: {}\nProvider: {}\nSource: {}".format(
                lid, src_layer.provider, src_layer.source))
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            table.setItem(row, COL_SOURCE, item)

            names = used_in[lid]
            item = QTableWidgetItem(str(len(names)))
            item.setToolTip("\n".join(sorted(names)))
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(row, COL_USED, item)

            combo = QgsMapLayerComboBox()
            try:
                combo.setAllowEmptyLayer(True, "— not mapped —")
            except TypeError:
                combo.setAllowEmptyLayer(True)
            dest_id, method = self.mapping.get(lid, (None, importer.MATCH_NONE))
            combo.setLayer(QgsProject.instance().mapLayer(dest_id) if dest_id else None)
            combo.layerChanged.connect(
                lambda layer, lid=lid, combo=combo: self._layer_changed(lid, layer, combo))
            table.setCellWidget(row, COL_DEST, combo)

            table.setItem(row, COL_MATCH, self._match_item(method))
        self._filling = False

    def _match_item(self, method):
        item = QTableWidgetItem(method)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        if method == importer.MATCH_NONE:
            item.setIcon(QgsApplication.getThemeIcon("/mIconWarning.svg"))
        elif method == importer.MATCH_ID:
            item.setIcon(QgsApplication.getThemeIcon("/mIconSuccess.svg"))
        else:
            item.setIcon(QgsApplication.getThemeIcon("/mActionLink.svg"))
        return item

    def _layer_changed(self, lid, layer, combo):
        if self._filling:
            return
        method = importer.MATCH_MANUAL if layer else importer.MATCH_NONE
        self.mapping[lid] = (layer.id() if layer else None, method)
        for row in range(self.mapping_table.rowCount()):
            if self.mapping_table.cellWidget(row, COL_DEST) is combo:
                self.mapping_table.setItem(row, COL_MATCH, self._match_item(method))
                break

    # ------------------------------------------------------------------ import
    def do_import(self):
        layouts = self.checked_layouts()
        if not self.source or not layouts:
            return
        project = QgsProject.instance()
        mapping = {lid: dest for lid, (dest, _m) in self.mapping.items()}
        conflict = self.conflict_combo.currentData()

        QgsApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            results = importer.import_layouts(self.source, layouts, mapping, project, conflict)
        finally:
            QgsApplication.restoreOverrideCursor()

        done = [r for r in results if r.status in ("imported", "replaced")]
        lines = []
        for r in results:
            if r.status == "skipped":
                line = "• {} — skipped (name exists)".format(r.source_name)
            elif r.status == "failed":
                line = "• {} — FAILED".format(r.source_name)
            elif r.imported_name != r.source_name:
                line = "• {} → {}".format(r.source_name, r.imported_name)
            else:
                line = "• {}{}".format(r.source_name, " (replaced)" if r.status == "replaced" else "")
            lines.append(line)
            lines.extend("    ⚠ " + w for w in r.warnings)
        for line in lines:
            QgsMessageLog.logMessage(line, LOG_TAG, Qgis.MessageLevel.Info)

        has_problems = any(r.warnings or r.status == "failed" for r in results)
        msg = "Imported {} of {} layout(s) from {}.".format(
            len(done), len(results), os.path.basename(self.source.path))
        bar = self.iface.messageBar()
        if has_problems:
            bar.pushWarning(LOG_TAG, msg + " Some layouts need attention.")
            QMessageBox.warning(self, self.windowTitle(), msg + "\n\n" + "\n".join(lines))
        else:
            bar.pushSuccess(LOG_TAG, msg)
        if done:
            project.setDirty(True)
            self.accept()
