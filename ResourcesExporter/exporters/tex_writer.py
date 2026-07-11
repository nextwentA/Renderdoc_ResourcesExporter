# -*- coding: utf-8 -*-
"""Texture export helpers for rd_exporter."""

import os
import renderdoc as rd
from collections import defaultdict

_FMT_MAP = {
    "PNG": (rd.FileType.PNG, "png"),
    "DDS": (rd.FileType.DDS, "dds"),
    "TGA": (rd.FileType.TGA, "tga"),
    "BMP": (rd.FileType.BMP, "bmp"),
    "HDR": (rd.FileType.HDR, "hdr"),
    "EXR": (rd.FileType.EXR, "exr"),
}


def write_input_textures(save_dir, mapper, controller):
    fmt_name    = mapper.get("TEX_FORMAT", "PNG") or "PNG"
    use_default = mapper.get("TEX_DEFAULT_NAME", True)
    fbx_name    = mapper.get("FBX_NAME", "") or ""
    tex_fbx_pfx = mapper.get("TEX_FBX_PREFIX", False)
    if tex_fbx_pfx and fbx_name:
        prefix = fbx_name + "_"
    else:
        prefix = mapper.get("TEX_PREFIX", "") or ""
    infix     = mapper.get("TEX_INFIX",  "") or ""
    suffix    = mapper.get("TEX_SUFFIX", "") or ""
    file_type, ext = _FMT_MAP.get(fmt_name.upper(), (rd.FileType.PNG, "png"))

    textures = controller.GetTextures()
    tex_set  = {t.resourceId for t in textures}

    accesses     = controller.GetDescriptorAccess()
    store_ranges = defaultdict(list)
    for acc in accesses:
        store_ranges[acc.descriptorStore].append(acc)

    bound_ids = set()
    for store_id, acc_list in store_ranges.items():
        if store_id == rd.ResourceId.Null():
            continue
        try:
            ranges = [rd.DescriptorRange(acc) for acc in acc_list]
            for desc in controller.GetDescriptors(store_id, ranges):
                rid = desc.resource
                if rid != rd.ResourceId.Null() and rid in tex_set:
                    bound_ids.add(rid)
        except Exception:
            pass

    if not bound_ids:
        state = controller.GetPipelineState()
        for stage in [rd.ShaderStage.Vertex, rd.ShaderStage.Pixel,
                      rd.ShaderStage.Geometry, rd.ShaderStage.Hull,
                      rd.ShaderStage.Domain, rd.ShaderStage.Compute]:
            try:
                for binding in state.GetReadOnlyResources(stage):
                    for res in binding.resources:
                        rid = res.resourceId
                        if rid != rd.ResourceId.Null() and rid in tex_set:
                            bound_ids.add(rid)
            except Exception:
                pass

    res_names = {}
    try:
        for rdesc in controller.GetResources():
            res_names[rdesc.resourceId] = rdesc.name
    except Exception:
        pass

    saved = []
    for res_id in bound_ids:
        default_name = res_names.get(res_id, "texture_%s" % int(res_id))
        default_name = default_name.replace("/", "_").replace("\\", "_").replace(":", "_")
        stem     = default_name if use_default else "%s%s%s%s" % (prefix, default_name, infix, suffix)
        out_path = os.path.join(save_dir, "%s.%s" % (stem, ext))
        try:
            save_data            = rd.TextureSave()
            save_data.resourceId = res_id
            save_data.destType   = file_type
            save_data.mip        = 0
            save_data.slice.sliceIndex = 0
            controller.SaveTexture(save_data, out_path)
            saved.append(out_path)
        except Exception as e:
            print("Skipped texture %s: %s" % (stem, e))

    return saved


def write_output_textures(save_dir, mapper, controller):
    """Export render targets (color outputs + depth) bound at the current draw call."""
    fmt_name    = mapper.get("TEX_FORMAT", "PNG") or "PNG"
    fbx_name    = mapper.get("FBX_NAME", "") or ""
    tex_fbx_pfx = mapper.get("TEX_FBX_PREFIX", False)
    prefix      = (fbx_name + "_out_") if tex_fbx_pfx and fbx_name else "out_"
    file_type, ext = _FMT_MAP.get(fmt_name.upper(), (rd.FileType.PNG, "png"))

    textures = controller.GetTextures()
    tex_set  = {t.resourceId for t in textures}

    state     = controller.GetPipelineState()
    bound_ids = {}

    try:
        for i, desc in enumerate(state.GetOutputTargets()):
            rid = desc.resource
            if rid != rd.ResourceId.Null() and rid in tex_set:
                bound_ids[rid] = "color%d" % i
    except Exception:
        pass

    try:
        depth = state.GetDepthTarget()
        if depth and depth.resource != rd.ResourceId.Null() and depth.resource in tex_set:
            bound_ids[depth.resource] = "depth"
    except Exception:
        pass

    saved = []
    for res_id, label in bound_ids.items():
        name     = "%s%s.%s" % (prefix, label, ext)
        out_path = os.path.join(save_dir, name)
        try:
            save_data            = rd.TextureSave()
            save_data.resourceId = res_id
            save_data.destType   = file_type
            save_data.mip        = 0
            save_data.slice.sliceIndex = 0
            controller.SaveTexture(save_data, out_path)
            saved.append(out_path)
        except Exception as e:
            print("Skipped output texture %s: %s" % (label, e))

    return saved


def _save_texture_cb(save_dir, mapper, out_list, controller):
    out_list.extend(write_input_textures(save_dir, mapper, controller))


def _save_output_texture_cb(save_dir, mapper, out_list, controller):
    out_list.extend(write_output_textures(save_dir, mapper, controller))
