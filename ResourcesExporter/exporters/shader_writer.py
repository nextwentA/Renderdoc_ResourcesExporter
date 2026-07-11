# -*- coding: utf-8 -*-
"""Shader export helpers for rd_exporter."""

import os
import renderdoc as rd

_SHADER_EXT = {
    0: "bin",    # Unknown
    1: "dxbc",   # DXBC
    2: "glsl",   # GLSL
    3: "spv",    # SPIRV
    4: "spvasm", # SPIRVAsm
    5: "hlsl",   # HLSL
    6: "spv",    # OpenGLSPIRV
    7: "spv",    # VulkanSPIRV
    8: "dxil",   # DXIL
}

_STAGE_MAP = {
    "VS": rd.ShaderStage.Vertex,
    "PS": rd.ShaderStage.Pixel,
    "GS": rd.ShaderStage.Geometry,
    "HS": rd.ShaderStage.Hull,
    "DS": rd.ShaderStage.Domain,
    "CS": rd.ShaderStage.Compute,
}


def write_shaders(save_dir, mapper, controller):
    import traceback
    stages_enabled = mapper.get("SHADER_STAGES", {})
    shader_fmt     = mapper.get("SHADER_FMT", "Binary")
    use_disasm     = (shader_fmt == "Disasm (txt)")
    fbx_name       = mapper.get("FBX_NAME", "") or ""
    shader_fbx_pfx = mapper.get("SHADER_FBX_PREFIX", True)
    name_prefix    = (fbx_name + "_") if shader_fbx_pfx and fbx_name else ""
    state          = controller.GetPipelineState()
    pipeline       = state.GetGraphicsPipelineObject()

    saved  = []
    errors = []

    for stage_key, stage in _STAGE_MAP.items():
        if not stages_enabled.get(stage_key, False):
            continue
        try:
            refl = state.GetShaderReflection(stage)
            if refl is None:
                continue

            entry_name = str(state.GetShaderEntryPoint(stage))
            res_id     = state.GetShader(stage)
            if res_id == rd.ResourceId.Null():
                continue

            enc_val  = int(refl.encoding)
            base_ext = _SHADER_EXT.get(enc_val, "bin")

            if use_disasm:
                pipe     = state.GetComputePipelineObject() if stage == rd.ShaderStage.Compute else pipeline
                text     = controller.DisassembleShader(pipe, refl, "")
                if not text:
                    errors.append("%s: disassembly returned empty" % stage_key)
                    continue
                text_str = text if isinstance(text, str) else text.decode("utf-8", errors="replace")
                name     = "%s%s_%s.%s.txt" % (name_prefix, stage_key, entry_name, base_ext)
                out_path = os.path.join(save_dir, name)
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(text_str)
            else:
                raw = bytes(refl.rawBytes)
                if not raw:
                    errors.append("%s: rawBytes is empty" % stage_key)
                    continue
                name     = "%s%s_%s.%s" % (name_prefix, stage_key, entry_name, base_ext)
                out_path = os.path.join(save_dir, name)
                with open(out_path, "wb") as f:
                    f.write(raw)

            saved.append(out_path)
        except Exception:
            errors.append("%s: %s" % (stage_key, traceback.format_exc()))

    return saved, errors


def _save_shader_cb(save_dir, mapper, out_list, err_list, controller):
    saved, errors = write_shaders(save_dir, mapper, controller)
    out_list.extend(saved)
    err_list.extend(errors)
