# -*- coding: utf-8 -*-
import os
from PySide2 import QtWidgets

from ..core.mesh_io import read_table_mesh
from ..core.vertex_attr import apply_aliases
from ..exporters.fbx_writer import write_fbx
from ..exporters.obj_writer import write_obj
from ..exporters.vsout_writer import write_vsout_fbx
from .helpers import remove_empty_dir, parse_event_ids


def run_batch(eids, out_dir, mapper, pyrenderdoc, info_list):
    """Export VS Input / VS Output mesh + textures + shaders for each EID.

    VS Input path mirrors single export exactly:
      SetFrameEvent → QApplication.processEvents() → read_table_mesh (Qt table)
      → apply_aliases → write_fbx
    This avoids the raw-byte decoding differences that made read_gpu_attributes
    produce a different mesh than the Qt Mesh Viewer.

    VS Output path uses write_vsout_fbx inside BlockInvoke (needs replay thread).
    """
    from PySide2 import QtWidgets as _QW
    from ..exporters.tex_writer import _save_texture_cb, _save_output_texture_cb
    from ..exporters.shader_writer import _save_shader_cb
    from functools import partial

    export_fmt   = mapper.get("EXPORT_FORMAT", "FBX")
    ext          = ".obj" if export_fmt == "OBJ" else ".fbx"
    export_vsin  = mapper.get("EXPORT_VSIN",  True)
    export_vsout = mapper.get("EXPORT_VSOUT", False)
    both         = export_vsin and export_vsout

    # ── Save current EID so we can restore the UI after batch ────────────
    _orig_eid = [None]
    try:
        _orig_eid[0] = pyrenderdoc.CurEvent()
    except Exception:
        pass

    try:
      for eid in eids:
        eid_name = "eid_%05d" % eid
        eid_dir  = os.path.join(out_dir, eid_name)
        try:
            os.makedirs(eid_dir)
        except OSError:
            pass

        eid_mapper = dict(mapper)
        eid_mapper["FBX_NAME"] = eid_name

        # ── Navigate: same as user clicking EID in Event Browser ─────────
        # SetEventID is the UI-level navigation that updates the Qt Mesh
        # Viewer table (just like single export does when the user is at
        # the target EID).  We then spin processEvents() until the table
        # is populated — the Mesh Viewer fills the table asynchronously
        # after receiving the EventChanged signal.
        try:
            pyrenderdoc.SetEventID([], eid, eid)
        except Exception as _e:
            info_list.append("EID %d: SetEventID failed: %s" % (eid, _e))
            remove_empty_dir(eid_dir)
            continue

        from PySide2 import QtWidgets as _QW
        _main_win = pyrenderdoc.GetMainWindow().Widget()
        _tbl = (_main_win.findChild(_QW.QTableView, "vsinData") or
                _main_win.findChild(_QW.QTableView, "inTable"))

        # Spin the event loop until the Mesh Viewer table is non-empty.
        # Typically 1-3 processEvents() cycles are enough; 100 × 20 ms = 2 s max.
        _populated = False
        for _ in range(100):
            _QW.QApplication.processEvents()
            if _tbl and _tbl.model() and _tbl.model().rowCount() > 0:
                _populated = True
                break

        if not _populated:
            remove_empty_dir(eid_dir)
            continue

        # ── Read mesh data — IDENTICAL to single export path ─────────────
        vsin_data, vsin_attr_list = read_table_mesh(_main_win)
        if not vsin_data or not vsin_data.get("IDX"):
            remove_empty_dir(eid_dir)
            continue
        vsin_data, vsin_attr_list = apply_aliases(vsin_data, vsin_attr_list)

        any_ok = False

        # ── VS Input export — same as single export ───────────────────────
        if export_vsin:
            _vsin_path = os.path.join(eid_dir,
                (eid_name + "_vsin" if both else eid_name) + ext)
            _vm = dict(eid_mapper)
            _vm["MESH_MODE"] = "VS Input"
            _vm["FBX_NAME"]  = os.path.basename(os.path.splitext(_vsin_path)[0])
            try:
                if ext == ".obj":
                    write_obj(_vsin_path, _vm, vsin_data, vsin_attr_list, None)
                else:
                    write_fbx(_vsin_path, _vm, vsin_data, vsin_attr_list, None)
                any_ok = True
            except Exception:
                pass

        # ── VS Output export (needs replay thread for GetPostVSData) ──────
        if export_vsout:
            _vsout_path = os.path.join(eid_dir,
                (eid_name + "_vsout" if both else eid_name) + ext)
            _vom = dict(eid_mapper)
            _vom["MESH_MODE"] = "VS Output"
            _vom["FBX_NAME"]  = os.path.basename(os.path.splitext(_vsout_path)[0])
            _vout_info = []
            _vout_errs = []
            try:
                pyrenderdoc.Replay().BlockInvoke(
                    lambda ctrl, p=_vsout_path, m=_vom,
                           vd=vsin_data, va=vsin_attr_list,
                           vi=_vout_info, ve=_vout_errs:
                        write_vsout_fbx(p, m, vi, ve, vd, va, ctrl))
                if not _vout_errs:
                    any_ok = True
            except Exception:
                pass

        if not any_ok:
            remove_empty_dir(eid_dir)
            continue

        # ── Textures & shaders ────────────────────────────────────────────
        from .single import export_secondary
        tex_in, tex_out, shd, shd_err = export_secondary(
            eid_dir, eid_mapper, pyrenderdoc)

        # Only log EIDs that successfully produced output files
        modes = "+".join(filter(None, [
            ("vsin" if export_vsin else ""),
            ("vsout" if export_vsout else ""),
        ]))
        info_list.append("EID %d: OK  %s+tex(%d)+shd(%d)%s" % (
            eid, modes, len(tex_in), len(shd),
            (" ERR:" + shd_err[0][:40]) if shd_err else ""))

    finally:
        # ── Restore the original event ────────────────────────────────────
        if _orig_eid[0] is not None:
            try:
                pyrenderdoc.SetEventID([], _orig_eid[0], _orig_eid[0])
            except Exception:
                try:
                    pyrenderdoc.Replay().BlockInvoke(
                        lambda ctrl, e=_orig_eid[0]: ctrl.SetFrameEvent(e, True))
                except Exception:
                    pass


def merge_results(save_path, mapper, event_ids, pyrenderdoc,
                  info_list, err_list, main_window):
    """Export *event_ids* (VS Output) and write a combined FBX.

    For each event the replay is advanced to that EID, VS Output data is
    collected, and vertices/polygons are accumulated with index offsets so
    all draw calls end up in a single Geometry node.
    """
    from functools import partial as _partial
    from textwrap import dedent
    from ..exporters.fbx_writer import FBX_TEMPLATE, build_material_block

    save_name = os.path.basename(os.path.splitext(save_path)[0])

    # Accumulate across all events
    all_vertices   = []
    all_polygons   = []
    vert_offset    = 0
    layer_uv       = "";  layer_uv_ins   = ""
    layer_uv2      = "";  layer_uv2_ins  = ""
    layer_nrm      = "";  layer_nrm_ins  = ""
    layer_tan      = "";  layer_tan_ins  = ""
    layer_bn       = "";  layer_bn_ins   = ""
    layer_col      = "";  layer_col_ins  = ""

    # Per-layer accumulation lists (strings from each event)
    _uvs_acc   = [];  _uvi_acc   = []
    _uv2s_acc  = [];  _uv2i_acc  = []
    _nrms_acc  = []
    _tans_acc  = []
    _bns_acc   = []
    _cols_acc  = [];  _coli_acc  = []

    for _eid in event_ids:
        _per_info   = []
        _per_errors = []

        # Switch replay to this event
        try:
            pyrenderdoc.Replay().BlockInvoke(
                lambda ctrl, eid=_eid: ctrl.SetFrameEvent(eid, True)
            )
        except Exception as _e:
            info_list.append("batch: EID %d skip (SetFrameEvent: %s)" % (_eid, _e))
            continue

        # Collect VS Input pass-through data (UV/Normal etc.)
        _vsin_data, _vsin_attrs = read_table_mesh(main_window)

        # Temporary file to capture per-event FBX (we parse it back)
        import tempfile as _tf
        _tmp = _tf.mktemp(suffix=".fbx")

        pyrenderdoc.Replay().BlockInvoke(
            _partial(write_vsout_fbx, _tmp, mapper, _per_info, _per_errors,
                     _vsin_data, _vsin_attrs)
        )

        if _per_errors:
            info_list.append("batch: EID %d errors: %s" % (_eid, _per_errors[0][:80]))
            continue
        if not os.path.exists(_tmp):
            info_list.append("batch: EID %d produced no file" % _eid)
            continue

        # Parse the temporary FBX to extract vertices and polygons
        try:
            with open(_tmp, "r") as _fh:
                _txt = _fh.read()
            import re as _re

            def _extract(pattern, text):
                m = _re.search(pattern, text, _re.DOTALL)
                return m.group(1).strip() if m else ""

            _verts_str = _extract(r"Vertices:\s*\*\d+\s*\{[^}]*a:\s*([^}]+)\}", _txt)
            _polys_str = _extract(r"PolygonVertexIndex:\s*\*\d+\s*\{[^}]*a:\s*([^}]+)\}", _txt)

            if not _verts_str or not _polys_str:
                info_list.append("batch: EID %d empty geometry" % _eid)
                continue

            _verts = [float(v) for v in _verts_str.split(",") if v.strip()]
            _polys = [int(v)   for v in _polys_str.split(",") if v.strip()]
            _nv    = len(_verts) // 3

            # Offset polygon indices
            def _offset_idx(idx):
                return (~(~idx + vert_offset)) if idx < 0 else idx + vert_offset

            all_vertices.extend(_verts)
            all_polygons.extend(_offset_idx(p) for p in _polys)
            vert_offset += _nv

            info_list.append("batch: EID %d → %d verts %d faces" % (
                _eid, _nv, len(_polys) // 3))

            # Accumulate UV, Normal etc. layer data (concatenate)
            # UV
            _uv_s = _extract(r'LayerElementUV:\s*0\s*\{.*?UV:\s*\*\d+\s*\{[^}]*a:\s*([^}]+)\}', _txt)
            _ui_s = _extract(r'LayerElementUV:\s*0\s*\{.*?UVIndex:\s*\*\d+\s*\{[^}]*a:\s*([^}]+)\}', _txt)
            if _uv_s and _ui_s:
                _uvs_acc.append(_uv_s.strip())
                _ui_vals = [str(int(v) + (len(_uvi_acc[0].split(",")) // 2
                                          if _uvi_acc else 0))
                            for v in _ui_s.split(",") if v.strip()]
                # Simpler: just offset UV indices by current UV vertex count
                _cur_uv_verts = sum(s.count(",") + 1 for s in _uvs_acc[:-1]) // 2
                _ui_off = [str(int(v) + _cur_uv_verts) for v in _ui_s.split(",") if v.strip()]
                _uvi_acc.append(",".join(_ui_off))

            # Normal
            _nrm_s = _extract(r'LayerElementNormal:\s*0\s*\{.*?Normals:\s*\*\d+\s*\{[^}]*a:\s*([^}]+)\}', _txt)
            if _nrm_s: _nrms_acc.append(_nrm_s.strip())

        except Exception as _pe:
            info_list.append("batch: EID %d parse error: %s" % (_eid, str(_pe)[:60]))
        finally:
            try: os.remove(_tmp)
            except OSError: pass

    if not all_vertices:
        err_list.append("Batch merge: no geometry collected from any event")
        return

    # Build combined UV layer if available
    if _uvs_acc:
        _all_uvs = ",".join(_uvs_acc)
        _all_uvi = ",".join(_uvi_acc)
        layer_uv = """
            LayerElementUV: 0 {
                Version: 101
                Name: "map1"
                MappingInformationType: "ByPolygonVertex"
                ReferenceInformationType: "IndexToDirect"
                UV: *%(n)s { a: %(v)s }
                UVIndex: *%(in)s { a: %(i)s }
            }""" % {"n":  _all_uvs.count(",") + 1, "v": _all_uvs,
                    "in": _all_uvi.count(",") + 1, "i": _all_uvi}
        layer_uv_ins = """
            LayerElement: { Type: "LayerElementUV" TypedIndex: 0 }"""

    # Build combined Normal layer if available
    if _nrms_acc:
        _all_nrms = ",".join(_nrms_acc)
        layer_nrm = """
            LayerElementNormal: 0 {
                Version: 101
                Name: ""
                MappingInformationType: "ByPolygonVertex"
                ReferenceInformationType: "Direct"
                Normals: *%(n)s { a: %(v)s }
            }""" % {"n": _all_nrms.count(",") + 1, "v": _all_nrms}
        layer_nrm_ins = """
            LayerElement: { Type: "LayerElementNormal" TypedIndex: 0 }"""

    _mat_objs, _mat_cons = build_material_block(os.path.dirname(save_path), save_name)

    ARGS = {
        "model_name":                save_name,
        "vertices":                  ",".join(str(v) for v in all_vertices),
        "vertices_num":              len(all_vertices),
        "polygons":                  ",".join(str(p) for p in all_polygons),
        "polygons_num":              len(all_polygons),
        "LayerElementNormal":        layer_nrm,
        "LayerElementNormalInsert":  layer_nrm_ins,
        "LayerElementBiNormal":      layer_bn,
        "LayerElementBiNormalInsert":layer_bn_ins,
        "LayerElementTangent":       layer_tan,
        "LayerElementTangentInsert": layer_tan_ins,
        "LayerElementColor":         layer_col,
        "LayerElementColorInsert":   layer_col_ins,
        "LayerElementUV":            layer_uv,
        "LayerElementUVInsert":      layer_uv_ins,
        "LayerElementUV2":           layer_uv2,
        "LayerElementUV2Insert":     layer_uv2_ins,
        "FbxMaterialObjects":        _mat_objs,
        "FbxMaterialConnections":    _mat_cons,
        "FbxSkinObjects":            "",
        "FbxSkinConnections":        "",
    }
    fbx = FBX_TEMPLATE % ARGS
    with open(save_path, "w") as _fh:
        _fh.write(dedent(fbx).strip())
    info_list.append("batch: merged %d events → %d total verts" % (
        len(event_ids), len(all_vertices) // 3))
