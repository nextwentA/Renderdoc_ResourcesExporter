# -*- coding: utf-8 -*-
import os
import time
import tempfile
from functools import partial

from PySide2 import QtCore

from ..core.mesh_io import read_table_mesh, scan_attributes
from ..core.vertex_attr import apply_aliases
from ..exporters.fbx_writer import write_fbx
from ..exporters.obj_writer import write_obj
from ..exporters.vsout_writer import write_vsout_fbx
from ..exporters.tex_writer import write_input_textures, write_output_textures, _save_texture_cb, _save_output_texture_cb
from ..exporters.shader_writer import write_shaders, _save_shader_cb
from ..ui.dialog import ExportDialog
from ..ui.progress import MProgressDialog
from .helpers import remove_empty_dir, error_handler
from .batch import run_batch


def build_settings(settings):
    """Reconstruct a mapper dict from a QSettings object (for Quick Export)."""
    mapper = {}
    for key, _ in ExportDialog.edit_config:
        mapper[key] = settings.value(key, "")

    mapper["ENGINE"]                 = settings.value("Engine",               "unity")
    mapper["EXPORT_VSIN"]            = settings.value("ExportVSIn",           "true")  == "true"
    mapper["EXPORT_VSOUT"]           = settings.value("ExportVSOut",          "false") == "true"
    mapper["EXPORT_FORMAT"]          = settings.value("ExportFormat",         "FBX")
    mapper["FLIP_U"]                 = settings.value("FlipU",  "false") == "true"
    mapper["FLIP_V"]                 = settings.value("FlipV",  "true")  == "true"
    mapper["EXPORT_TEXTURES"]        = settings.value("ExportTextures",       "true") == "true"
    mapper["EXPORT_OUTPUT_TEXTURES"] = settings.value("ExportOutputTextures", "true") == "true"
    mapper["TEX_FORMAT"]             = settings.value("TexFormat",            "PNG")
    mapper["TEX_DEFAULT_NAME"]       = settings.value("TexDefaultName",       "true") == "true"
    mapper["TEX_PREFIX"]             = settings.value("TexPrefix",            "")
    mapper["TEX_INFIX"]              = settings.value("TexInfix",             "")
    mapper["TEX_SUFFIX"]             = settings.value("TexSuffix",            "")
    mapper["TEX_FBX_PREFIX"]         = settings.value("TexFbxPrefix",         "true") == "true"
    mapper["EXPORT_SHADERS"]         = settings.value("ExportShaders",        "true") == "true"
    mapper["SHADER_FMT"]             = settings.value("ShaderFmt",            "Binary")
    mapper["SHADER_FBX_PREFIX"]      = settings.value("ShaderFbxPrefix",      "true") == "true"
    mapper["SHADER_STAGES"]          = {
        k: settings.value("ShaderStage_%s" % k,
                          "true" if ExportDialog.STAGE_DEFAULTS.get(k, False) else "false") == "true"
        for k in ExportDialog.STAGE_KEYS
    }
    mapper["VSOUT_INCLUDE_VSIN_UV"]      = settings.value("VSOutIncludeVSInUV",      "true") == "true"
    mapper["VSOUT_INCLUDE_VSIN_UV2"]     = settings.value("VSOutIncludeVSInUV2",     "true") == "true"
    mapper["VSOUT_INCLUDE_VSIN_NORMAL"]  = settings.value("VSOutIncludeVSInNormal",  "true") == "true"
    mapper["VSOUT_INCLUDE_VSIN_TANGENT"] = settings.value("VSOutIncludeVSInTangent", "true") == "true"
    mapper["VSOUT_INCLUDE_VSIN_BINORMAL"]= settings.value("VSOutIncludeVSInBinormal","true") == "true"
    mapper["VSOUT_INCLUDE_VSIN_COLOR"]   = settings.value("VSOutIncludeVSInColor",   "true") == "true"
    mapper["BAKE_WORLD_SPACE"]           = settings.value("BakeWorldSpace",           "false") == "true"
    mapper["EXPORT_SKIN"]                = settings.value("ExportSkin",                "false") == "true"
    mapper["BATCH_EIDS"]                 = settings.value("BatchEIDs",                 "")
    return mapper


def export_mesh(save_path, mapper, data, attr_list, pyrenderdoc, fbx_info, fbx_errors):
    """Dispatch to FBX or OBJ exporter based on EXPORT_FORMAT in *mapper*."""
    export_format = mapper.get("EXPORT_FORMAT", "FBX")
    mesh_mode     = mapper.get("MESH_MODE", "VS Input")

    if mesh_mode == "VS Input":
        if export_format == "OBJ":
            pyrenderdoc.Replay().BlockInvoke(
                partial(write_obj, save_path, mapper, data, attr_list)
            )
        else:
            pyrenderdoc.Replay().BlockInvoke(
                partial(write_fbx, save_path, mapper, data, attr_list)
            )
    else:
        # data / attr_list here are the VS Input attributes (UV, Normal, ...)
        # collected by the caller for pass-through into VS Output export.
        pyrenderdoc.Replay().BlockInvoke(
            partial(write_vsout_fbx, save_path, mapper, fbx_info, fbx_errors,
                    data, attr_list)
        )


def export_secondary(save_dir, mapper, pyrenderdoc):
    """Export textures and shaders if requested. Returns (tex_in, tex_out, shaders, shader_errs)."""
    tex_results        = []
    tex_output_results = []
    shader_results     = []
    shader_errors      = []

    if mapper.get("EXPORT_TEXTURES", False):
        pyrenderdoc.Replay().BlockInvoke(
            partial(_save_texture_cb, save_dir, mapper, tex_results)
        )

    if mapper.get("EXPORT_OUTPUT_TEXTURES", False):
        pyrenderdoc.Replay().BlockInvoke(
            partial(_save_output_texture_cb, save_dir, mapper, tex_output_results)
        )

    if mapper.get("EXPORT_SHADERS", False):
        pyrenderdoc.Replay().BlockInvoke(
            partial(_save_shader_cb, save_dir, mapper, shader_results, shader_errors)
        )

    return tex_results, tex_output_results, shader_results, shader_errors


def format_result_msg(save_path, mapper, fbx_info,
                      tex_results, tex_output_results,
                      shader_results, shader_errors):
    """Build the text shown in the success dialog."""
    export_format = mapper.get("EXPORT_FORMAT", "FBX")
    msg = "%s Output Successful!" % export_format

    if fbx_info:
        msg += "\n\nVS Out info:\n" + "\n".join(fbx_info)

    if tex_results:
        msg += "\n\nInput Textures saved (%d):\n" % len(tex_results)
        msg += "\n".join(os.path.basename(p) for p in tex_results[:20])
        if len(tex_results) > 20:
            msg += "\n... and %d more" % (len(tex_results) - 20)
    elif mapper.get("EXPORT_TEXTURES", False):
        msg += "\n\nNo bound input textures found."

    if tex_output_results:
        msg += "\n\nOutput Textures saved (%d):\n" % len(tex_output_results)
        msg += "\n".join(os.path.basename(p) for p in tex_output_results)

    if shader_results:
        msg += "\n\nShaders saved (%d):\n" % len(shader_results)
        msg += "\n".join(os.path.basename(p) for p in shader_results)
        msg += "\n[fmt=%s]" % mapper.get("SHADER_FMT", "?")

    if shader_errors:
        msg += "\n\nShader export errors:\n" + "\n".join(shader_errors[:5])

    if mapper.get("EXPORT_SHADERS", False) and not shader_results and not shader_errors:
        stages  = mapper.get("SHADER_STAGES", {})
        enabled = [k for k, v in stages.items() if v]
        msg += "\n\nShader export: no stages enabled (checked: %s)" % (enabled or "none")

    return msg


@error_handler
def run_export(pyrenderdoc, data):
    """Open the Export Options dialog then perform the full export."""
    manager = pyrenderdoc.Extensions()
    if not pyrenderdoc.HasMeshPreview():
        manager.ErrorDialog("No preview mesh!", "Error")
        return

    # Pre-scan available vertex attributes so the dialog can display / auto-fill them
    main_window     = pyrenderdoc.GetMainWindow().Widget()
    available_attrs = scan_attributes(main_window)

    mqt    = manager.GetMiniQtHelper()
    dialog = ExportDialog(mqt, available_attrs=available_attrs)
    _dlg_widget = dialog.init_ui()

    # ExportDialog now returns a raw PySide2 QDialog (for scroll support).
    # ShowWidgetAsDialog doesn't handle QDialog correctly — use exec() instead.
    if hasattr(_dlg_widget, 'exec'):
        _accepted = bool(_dlg_widget.exec())
    else:
        _accepted = bool(mqt.ShowWidgetAsDialog(_dlg_widget))
    if not _accepted:
        return

    export_vsin   = dialog.mapper.get("EXPORT_VSIN",   True)
    export_vsout  = dialog.mapper.get("EXPORT_VSOUT",  False)
    export_format = dialog.mapper.get("EXPORT_FORMAT", "FBX")

    if not export_vsin and not export_vsout:
        manager.ErrorDialog("请至少勾选一种模式 (VS Input / VS Output)", "Error")
        return

    # ── Batch EID mode: resolve EIDs first, then show ONE save dialog ────────
    _batch_eids_str = dialog.mapper.get("BATCH_EIDS", "").strip()
    if _batch_eids_str:
        from .helpers import parse_event_ids
        try:
            _eids = parse_event_ids(_batch_eids_str)
        except Exception as _pe:
            manager.ErrorDialog("EID 格式错误: %s\n请用逗号和短横线，如: 100,200-210" % _pe, "Error")
            return
        if not _eids:
            manager.ErrorDialog("没有解析到有效 EID", "Error")
            return
        # Folder-picker dialog: user selects the output directory directly
        from PySide2 import QtWidgets as _QW
        _out_dir = _QW.QFileDialog.getExistingDirectory(
            None, "选择批量导出目录", "")
        if not _out_dir:
            return
        _batch_info = []
        run_batch(_eids, _out_dir, dialog.mapper, pyrenderdoc, _batch_info)
        os.startfile(_out_dir)
        manager.MessageDialog(
            "批量导出完成  (%d / %d EIDs)\n\n%s" % (
                len(_batch_info), len(_eids), "\n".join(_batch_info[-30:])),
            "Done!")
        return

    # ── Single export: show ONE save dialog ──────────────────────────────────
    if export_format == "OBJ":
        save_path = manager.SaveFileName("Save OBJ File", "", "*.obj")
    else:
        save_path = manager.SaveFileName("Save FBX File", "", "*.fbx")
    if not save_path:
        return

    save_dir  = os.path.dirname(save_path)
    save_base = os.path.splitext(save_path)[0]          # path without extension
    save_ext  = os.path.splitext(save_path)[1] or (".obj" if export_format == "OBJ" else ".fbx")
    fbx_name  = os.path.basename(save_base)
    dialog.mapper["FBX_NAME"] = fbx_name
    current = time.time()

    both = export_vsin and export_vsout   # both modes selected → add suffixes

    fbx_info   = []
    fbx_errors = []

    def _add_input_aliases(data, attr_list):
        """Delegate to apply_aliases (same logic, one place)."""
        return apply_aliases(data, attr_list)

    # ── Collect VS Input table data (shared for both modes) ──────────────
    vsin_data, vsin_attr_list = read_table_mesh(main_window)

    last_exported_path = None

    # ── VS Input export ───────────────────────────────────────────────────
    if export_vsin:
        vsin_path = (save_base + "_vsin" + save_ext) if both else save_path
        vsin_name = os.path.basename(os.path.splitext(vsin_path)[0])
        if vsin_data is None:
            manager.ErrorDialog("Mesh data table not found for VS Input mode.", "Error")
            if not export_vsout:
                return
        else:
            _data, _alist = _add_input_aliases(vsin_data, vsin_attr_list)
            _vsin_mapper  = dict(dialog.mapper)
            _vsin_mapper["MESH_MODE"] = "VS Input"
            _vsin_mapper["FBX_NAME"]  = vsin_name
            print("elapsed time unpack: %s" % (time.time() - current))
            export_mesh(vsin_path, _vsin_mapper, _data, _alist,
                        pyrenderdoc, fbx_info, fbx_errors)
            last_exported_path = vsin_path

    # ── VS Output export ──────────────────────────────────────────────────
    if export_vsout:
        vsout_path = (save_base + "_vsout" + save_ext) if both else save_path
        vsout_name = os.path.basename(os.path.splitext(vsout_path)[0])
        need_vsin = any(dialog.mapper.get(k, True) for k in (
            "VSOUT_INCLUDE_VSIN_UV", "VSOUT_INCLUDE_VSIN_UV2",
            "VSOUT_INCLUDE_VSIN_NORMAL", "VSOUT_INCLUDE_VSIN_TANGENT",
            "VSOUT_INCLUDE_VSIN_BINORMAL", "VSOUT_INCLUDE_VSIN_COLOR",
        ))
        _vs_mapper = dict(dialog.mapper)
        _vs_mapper["MESH_MODE"] = "VS Output"
        _vs_mapper["FBX_NAME"]  = vsout_name
        _vsout_errors = []
        export_mesh(vsout_path, _vs_mapper,
                    vsin_data if need_vsin else None,
                    vsin_attr_list if need_vsin else None,
                    pyrenderdoc, fbx_info, _vsout_errors)
        if _vsout_errors:
            manager.ErrorDialog("VS Output export failed:\n" +
                                 "\n".join(_vsout_errors), "Error")
            if not export_vsin:
                return
        else:
            last_exported_path = vsout_path

    tex_in, tex_out, shaders, shader_errs = export_secondary(
        save_dir, dialog.mapper, pyrenderdoc
    )

    _show_path = last_exported_path or save_path
    if os.path.exists(_show_path):
        msg = format_result_msg(_show_path, dialog.mapper, fbx_info,
                                tex_in, tex_out, shaders, shader_errs)
        os.startfile(save_dir)
        manager.MessageDialog(msg, "Done!")


@error_handler
def run_quick_export(pyrenderdoc, data):
    """Export using last saved settings — no dialog is shown.

    The save-file dialog is still presented so the user can choose where to
    write the output, but all export options are read from the stored
    QSettings (same INI that the full dialog uses), making repeat exports
    effortless.
    """
    manager = pyrenderdoc.Extensions()
    if not pyrenderdoc.HasMeshPreview():
        manager.ErrorDialog("No preview mesh!", "Error")
        return

    # Load last-used settings
    settings_path = os.path.join(tempfile.gettempdir(), "RenderDoc_ExportDialog.ini")
    settings      = QtCore.QSettings(settings_path, QtCore.QSettings.IniFormat)
    mapper        = build_settings(settings)

    export_format = mapper.get("EXPORT_FORMAT", "FBX")
    if export_format == "OBJ":
        save_path = manager.SaveFileName("Quick Export — Save OBJ File", "", "*.obj")
    else:
        save_path = manager.SaveFileName("Quick Export — Save FBX File", "", "*.fbx")
    if not save_path:
        return

    save_dir = os.path.dirname(save_path)
    fbx_name = os.path.basename(os.path.splitext(save_path)[0])
    mapper["FBX_NAME"] = fbx_name

    main_window  = pyrenderdoc.GetMainWindow().Widget()
    export_vsin  = mapper.get("EXPORT_VSIN",  True)
    export_vsout = mapper.get("EXPORT_VSOUT", False)
    both         = export_vsin and export_vsout
    save_base    = os.path.splitext(save_path)[0]
    save_ext     = os.path.splitext(save_path)[1] or (".obj" if export_format == "OBJ" else ".fbx")
    fbx_info     = []
    fbx_errors   = []

    vsin_data, vsin_attr_list = read_table_mesh(main_window)

    if export_vsin and vsin_data is not None:
        vsin_path = (save_base + "_vsin" + save_ext) if both else save_path
        _vm = dict(mapper)
        _vm["MESH_MODE"] = "VS Input"
        _vm["FBX_NAME"]  = os.path.basename(os.path.splitext(vsin_path)[0])
        export_mesh(vsin_path, _vm, vsin_data, vsin_attr_list,
                    pyrenderdoc, fbx_info, fbx_errors)

    if export_vsout:
        vsout_path = (save_base + "_vsout" + save_ext) if both else save_path
        need_vsin = any(mapper.get(k, True) for k in (
            "VSOUT_INCLUDE_VSIN_UV", "VSOUT_INCLUDE_VSIN_UV2",
            "VSOUT_INCLUDE_VSIN_NORMAL", "VSOUT_INCLUDE_VSIN_TANGENT",
            "VSOUT_INCLUDE_VSIN_BINORMAL", "VSOUT_INCLUDE_VSIN_COLOR",
        ))
        _vom = dict(mapper)
        _vom["MESH_MODE"] = "VS Output"
        _vom["FBX_NAME"]  = os.path.basename(os.path.splitext(vsout_path)[0])
        export_mesh(vsout_path, _vom,
                    vsin_data if need_vsin else None,
                    vsin_attr_list if need_vsin else None,
                    pyrenderdoc, fbx_info, fbx_errors)
        if fbx_errors:
            manager.ErrorDialog(
                "VS Output export failed:\n" + "\n".join(fbx_errors), "Error"
            )
            return

    tex_in, tex_out, shaders, shader_errs = export_secondary(
        save_dir, mapper, pyrenderdoc
    )

    _show = (save_base + ("_vsin" if both and export_vsin else
                          ("_vsout" if export_vsout else "")) + save_ext)
    if not os.path.exists(_show):
        _show = save_path
    if os.path.exists(_show):
        msg = format_result_msg(_show, mapper, fbx_info,
                                tex_in, tex_out, shaders, shader_errs)
        os.startfile(save_dir)
        manager.MessageDialog(msg, "Quick Export Done!")
