"""Core logic: read layouts from a .qgs/.qgz file and import them into a QgsProject.

The source project is parsed as XML only (its layers are never loaded), so this is
fast and does not trigger credential prompts or broken-datasource dialogs.
"""

import os
import re
import zipfile

from qgis.PyQt.QtXml import QDomDocument
from qgis.core import (
    QgsLayoutItemMap,
    QgsPathResolver,
    QgsPrintLayout,
    QgsReadWriteContext,
    QgsReport,
)

# Characters that may appear in a QGIS layer id; used to match ids as whole tokens.
_ID_CHARS = r"A-Za-z0-9_\-"

MATCH_ID = "same ID"
MATCH_NAME = "by name"
MATCH_NAME_NOCASE = "by name (case-insensitive)"
MATCH_SOURCE = "by data source"
MATCH_MANUAL = "manual"
MATCH_NONE = "not found"

CONFLICT_RENAME = "rename"
CONFLICT_REPLACE = "replace"
CONFLICT_SKIP = "skip"


class SourceLayer:
    def __init__(self, layer_id, name, source, provider):
        self.id = layer_id
        self.name = name
        self.source = source
        self.provider = provider


class SourceLayout:
    def __init__(self, name, kind, xml, layer_ids):
        self.name = name
        self.kind = kind  # "Layout" or "Report"
        self.xml = xml
        self.layer_ids = layer_ids


class ImportResult:
    def __init__(self, source_name, imported_name=None, status="imported"):
        self.source_name = source_name
        self.imported_name = imported_name
        self.status = status  # imported / replaced / skipped / failed
        self.warnings = []


def _set_content(doc, data):
    res = doc.setContent(data)
    if isinstance(res, tuple):
        return res[0], (res[1] if len(res) > 1 else "")
    # Qt 6 ParseResult
    try:
        return bool(res), getattr(res, "errorMessage", "")
    except Exception:
        return bool(res), ""


def _element_to_string(element):
    doc = QDomDocument()
    doc.appendChild(doc.importNode(element, True))
    return doc.toString()


def _child_elements(element, tag=None):
    child = element.firstChildElement(tag) if tag else element.firstChildElement()
    while not child.isNull():
        yield child
        child = child.nextSiblingElement(tag) if tag else child.nextSiblingElement()


def _id_regex(ids):
    ids = sorted((i for i in ids if i), key=len, reverse=True)
    if not ids:
        return None
    alternation = "|".join(re.escape(i) for i in ids)
    return re.compile(r"(?<![{c}])({a})(?![{c}])".format(c=_ID_CHARS, a=alternation))


def _read_project_bytes(path):
    if path.lower().endswith(".qgz"):
        with zipfile.ZipFile(path) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".qgs")]
            if not names:
                raise ValueError("No .qgs file found inside {}".format(os.path.basename(path)))
            return zf.read(names[0])
    with open(path, "rb") as f:
        return f.read()


class SourceProject:
    """A .qgs/.qgz project parsed just enough to list its layers and layouts."""

    def __init__(self, path):
        self.path = path
        doc = QDomDocument()
        ok, err = _set_content(doc, _read_project_bytes(path))
        if not ok:
            raise ValueError("Cannot parse project file: {}".format(err))
        root = doc.documentElement()
        self.layers = self._read_layers(root)
        self.layouts = self._read_layouts(root)

    def _read_layers(self, root):
        layers = {}
        project_layers = root.firstChildElement("projectlayers")
        for e in _child_elements(project_layers, "maplayer"):
            if e.attribute("embedded") == "1":
                layer_id, name = e.attribute("id"), e.attribute("name")
            else:
                layer_id = e.firstChildElement("id").text()
                name = e.firstChildElement("layername").text()
            if not layer_id:
                continue
            layers[layer_id] = SourceLayer(
                layer_id,
                name,
                e.firstChildElement("datasource").text(),
                e.firstChildElement("provider").text(),
            )
        return layers

    def _read_layouts(self, root):
        regex = _id_regex(self.layers.keys())
        layouts = []
        for e in _child_elements(root.firstChildElement("Layouts")):
            kind = e.tagName()
            if kind not in ("Layout", "Report"):
                continue
            xml = _element_to_string(e)
            ids = set(regex.findall(xml)) if regex else set()
            layouts.append(SourceLayout(e.attribute("name"), kind, xml, ids))
        return layouts

    def absolute_source(self, layer):
        """Data source of a source layer with relative file paths resolved."""
        return _normalize_source(layer.source, QgsPathResolver(self.path))


def _normalize_source(source, resolver=None):
    if not source:
        return ""
    head, sep, tail = source.partition("|")
    if resolver is not None and (head.startswith("./") or head.startswith("../")):
        head = resolver.readPath(head)
    if os.path.sep in head or "/" in head:
        head = os.path.normcase(os.path.normpath(head))
    return head + sep + tail


def suggest_mapping(source_project, dest_project, layer_ids=None):
    """Return {source_layer_id: (dest_layer_id or None, match_method)}."""
    dest_layers = dest_project.mapLayers()
    dest_sources = {}
    for layer in dest_layers.values():
        dest_sources.setdefault(_normalize_source(layer.source()), []).append(layer)

    ids = layer_ids if layer_ids is not None else source_project.layers.keys()
    mapping = {}
    for sid in ids:
        src = source_project.layers.get(sid)
        if src is None:
            continue
        if sid in dest_layers:
            mapping[sid] = (sid, MATCH_ID)
            continue

        src_source = source_project.absolute_source(src)
        method = MATCH_NAME
        candidates = [l for l in dest_layers.values() if l.name() == src.name]
        if not candidates:
            method = MATCH_NAME_NOCASE
            candidates = [l for l in dest_layers.values() if l.name().lower() == src.name.lower()]
        if candidates:
            # Several layers with the same name: prefer same data source, then same provider.
            def score(l):
                return (
                    _normalize_source(l.source()) == src_source,
                    l.providerType() == src.provider,
                )
            best = max(candidates, key=score)
            mapping[sid] = (best.id(), method)
            continue

        by_source = dest_sources.get(src_source)
        if by_source:
            mapping[sid] = (by_source[0].id(), MATCH_SOURCE)
            continue

        mapping[sid] = (None, MATCH_NONE)
    return mapping


def _unique_name(manager, name):
    if manager.layoutByName(name) is None:
        return name
    n = 2
    while manager.layoutByName("{} ({})".format(name, n)) is not None:
        n += 1
    return "{} ({})".format(name, n)


def _check_layout(layout, dest_project, unmapped_names):
    warnings = []
    if unmapped_names:
        warnings.append("unmapped layers: " + ", ".join(sorted(unmapped_names)))
    if not isinstance(layout, QgsPrintLayout):
        return warnings

    themes = set(dest_project.mapThemeCollection().mapThemes())
    for item in layout.items():
        if isinstance(item, QgsLayoutItemMap) and item.followVisibilityPreset():
            theme = item.followVisibilityPresetName()
            if theme and theme not in themes:
                warnings.append("map '{}' follows missing map theme '{}'".format(
                    item.displayName(), theme))

    atlas = layout.atlas()
    if atlas.enabled() and atlas.coverageLayer() is None:
        warnings.append("atlas coverage layer not found")
    return warnings


def import_layouts(source_project, layouts, mapping, dest_project, conflict=CONFLICT_RENAME):
    """Import ``layouts`` (SourceLayout list) into ``dest_project``.

    ``mapping`` is {source_layer_id: dest_layer_id or None}.
    Returns a list of ImportResult.
    """
    manager = dest_project.layoutManager()
    id_map = {s: d for s, d in mapping.items() if d and d != s}
    regex = _id_regex(id_map.keys())
    results = []

    for src in layouts:
        result = ImportResult(src.name)
        results.append(result)

        existing = manager.layoutByName(src.name)
        if existing is not None and conflict == CONFLICT_SKIP:
            result.status = "skipped"
            continue

        xml = regex.sub(lambda m: id_map[m.group(1)], src.xml) if regex else src.xml
        doc = QDomDocument()
        ok, err = _set_content(doc, xml)
        if not ok:
            result.status = "failed"
            result.warnings.append(err)
            continue

        context = QgsReadWriteContext()
        context.setPathResolver(QgsPathResolver(source_project.path))

        layout = QgsPrintLayout(dest_project) if src.kind == "Layout" else QgsReport(dest_project)
        if isinstance(layout, QgsPrintLayout):
            layout.undoStack().blockCommands(True)
        if not layout.readLayoutXml(doc.documentElement(), doc, context):
            result.status = "failed"
            result.warnings.append("QGIS could not read the layout XML")
            continue
        if isinstance(layout, QgsPrintLayout):
            layout.updateBounds()
            layout.undoStack().blockCommands(False)

        if existing is not None and conflict == CONFLICT_REPLACE:
            manager.removeLayout(existing)
            name = src.name
            result.status = "replaced"
        else:
            name = _unique_name(manager, src.name)
        layout.setName(name)

        if not manager.addLayout(layout):
            result.status = "failed"
            result.warnings.append("layout manager refused the layout")
            continue
        result.imported_name = name

        unmapped = {
            source_project.layers[i].name
            for i in src.layer_ids
            if not mapping.get(i) and i in source_project.layers
        }
        result.warnings.extend(_check_layout(layout, dest_project, unmapped))
    return results
