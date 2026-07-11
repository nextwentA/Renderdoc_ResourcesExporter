# -*- coding: utf-8 -*-
"""Mesh data collection from the RenderDoc Mesh Viewer Qt table."""

from collections import defaultdict
from PySide2 import QtWidgets, QtCore


def scan_attributes(main_window):
    """Return a sorted list of vertex attribute root names from the Mesh Viewer table."""
    for tbl_name in ("vsinData", "inTable"):
        tbl = main_window.findChild(QtWidgets.QTableView, tbl_name)
        if tbl:
            mdl = tbl.model()
            attrs = set()
            for c in range(mdl.columnCount()):
                head = mdl.headerData(c, QtCore.Qt.Horizontal)
                if head and "." in head:
                    attrs.add(head.split(".")[0])
            return sorted(attrs)
    return []


def read_table_mesh(main_window):
    """Read all vertex attribute columns from the Mesh Viewer table.

    Returns (data, attr_list) where data is a defaultdict(list) keyed by
    attribute name and attr_list is the set of multi-component attribute names.
    Returns (None, None) when no table widget is found.
    """
    from ResourcesExporter.ui.progress import MProgressDialog

    table = None
    for tbl_name in ("vsinData", "inTable"):
        table = main_window.findChild(QtWidgets.QTableView, tbl_name)
        if table:
            break

    if not table:
        return None, None

    model        = table.model()
    row_count    = model.rowCount()
    column_count = model.columnCount()
    rows         = range(row_count)

    data      = defaultdict(list)
    attr_list = set()

    # Single progress dialog covering both phases (collect + rearrange)
    total = column_count + len(attr_list) + 1   # rough estimate; updated after phase 1
    dlg   = MProgressDialog(status="Collect Mesh Data", maximum=column_count)

    # Phase 1: collect columns
    for c in range(column_count):
        if dlg.wasCanceled():
            break
        head   = model.headerData(c, QtCore.Qt.Horizontal)
        values = [model.data(model.index(r, c)) for r in rows]
        if "." not in head:
            data[head] = values
        else:
            attr = head.split(".")[0]
            attr_list.add(attr)
            data[attr].append(values)
        dlg.setValue(c + 1)

    # Phase 2: rearrange multi-component attributes
    dlg.setLabelText("Rearrange Mesh Data")
    dlg.setMaximum(len(attr_list))
    for i, attr in enumerate(attr_list):
        if dlg.wasCanceled():
            break
        values_list = data[attr]
        data[attr]  = [[float(values[r]) for values in values_list] for r in rows]
        dlg.setValue(i + 1)

    dlg.deleteLater()
    return data, attr_list
