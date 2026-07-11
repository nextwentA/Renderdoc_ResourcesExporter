# -*- coding: utf-8 -*-
"""VS Output FBX exporter."""

import os
import struct

import renderdoc as rd

from ..core.math_utils import fetch_view_matrix, rigid_inverse, mat4_transform
from ..core.vertex_attr import apply_aliases
from .fbx_writer import write_fbx, FBX_TEMPLATE, build_material_block
from .fbx_skin import scan_bones, build_skin_block


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read_index_buffer(fmt, controller):
    """Read the index buffer referenced by *fmt* and return a list of ints.

    Returns ``None`` when:
    - no index buffer is attached (non-indexed draw), or
    - an error occurs while reading.

    Returned indices are normalized to 0-based (minimum index is subtracted).
    Supports uint16 (stride=2) and uint32 (stride=4) index formats.
    """
    if fmt.indexResourceId == rd.ResourceId.Null():
        return None
    try:
        raw        = bytes(controller.GetBufferData(fmt.indexResourceId, 0, 0))
        byte_off   = fmt.indexByteOffset
        idx_stride = fmt.indexByteStride
        count      = fmt.numIndices

        if idx_stride == 2:
            pack_char = "H"   # uint16
        elif idx_stride == 4:
            pack_char = "I"   # uint32
        else:
            return None

        indices = []
        for i in range(count):
            base = byte_off + i * idx_stride
            if base + idx_stride > len(raw):
                break
            indices.append(struct.unpack_from("<" + pack_char, raw, base)[0])

        if not indices:
            return None

        # Normalize: subtract base-vertex so indices start at 0
        min_idx = min(indices)
        if min_idx != 0:
            indices = [v - min_idx for v in indices]
        return indices
    except Exception:
        return None




def read_gpu_attributes(mapper, info_list, controller):
    """Read VS Input vertex attributes directly from the GPU vertex buffer.

    This bypasses the Qt Mesh-Viewer table entirely, which is unreliable in
    VS Output view mode because RenderDoc repopulates the same table widget
    with VS Output attribute data (named ``_input0``…``_inputN`` for Vulkan /
    DX12 DXIL shaders) instead of VS Input data.

    Attribute-name mapping
    ----------------------
    The pipeline vertex-input layout may use:
    - D3D11/D3D12 semantic style: ``TEXCOORD0``, ``NORMAL``, ``ATTRIBUTE5`` …
    - Vulkan location style:      ``_input0``, ``_input1``, ``_input4`` …

    For each layout slot we generate ALL plausible aliases so that a mapper
    configured with ``UV = "ATTRIBUTE5"`` still matches the slot that lives at
    Vulkan location 5 (or slot index 5) regardless of what RenderDoc calls it.

    Returns
    -------
    attr_data : dict
        ``{attr_name: [[comp0, comp1, …], …]}`` — one list entry per **unique
        vertex** in the VS Input vertex buffer.
    vsin_nidxs : list[int]
        Normalized 0-based vertex index for every face corner (draw index),
        built from the VS Input index buffer.  Directly usable as UV-index
        array for FBX ``IndexToDirect``.
    """
    attr_data  = {}
    vsin_nidxs = []

    try:
        # ── VS Input vertex buffer ────────────────────────────────────────────
        fmt_in = controller.GetPostVSData(0, 0, rd.MeshDataStage.VSIn)
        if fmt_in.numIndices == 0:
            info_list.append("vsin_gpu: 0 indices – no VS Input data")
            return attr_data, vsin_nidxs

        raw     = bytes(controller.GetBufferData(fmt_in.vertexResourceId, 0, 0))
        vbo_off = fmt_in.vertexByteOffset
        vb_data = raw[vbo_off:] if vbo_off < len(raw) else raw
        stride  = fmt_in.vertexByteStride
        nv      = len(vb_data) // stride    # unique vertex count

        info_list.append("vsin_gpu: %d unique verts  stride=%d bytes" % (nv, stride))

        # ── VS Input index buffer → face-corner vertex index list ─────────────
        idx_raw  = _read_index_buffer(fmt_in, controller)
        if idx_raw is None:
            idx_raw = list(range(nv))
        min_idx   = min(idx_raw) if idx_raw else 0
        vsin_nidxs = [v - min_idx for v in idx_raw]

        # ── Collect ALL vertex buffers: VS Input primary + VS Output buffer ────
        # GetVertexBuffers() is unavailable in some RenderDoc versions.
        # However, the VS Output vertex buffer IS always accessible and the
        # vertex shader typically passes UV through as an output attribute
        # (TEXCOORD0 etc.).  We add the VS Output buffer as a secondary scan
        # source so we can find UV even when the VS Input VB has all-zero UV.
        all_vb = []   # list of (raw_bytes, stride)
        all_vb.append((vb_data, stride))   # primary VS Input buffer

        # Try GetVertexBuffers() (newer RenderDoc)
        try:
            state0  = controller.GetPipelineState()
            vb_list = state0.GetVertexBuffers()
            for bi, vb_b in enumerate(vb_list):
                rid  = getattr(vb_b, 'resourceId', None)
                boff = int(getattr(vb_b, 'byteOffset', 0) or 0)
                bstr = int(getattr(vb_b, 'byteStride', 0) or 0)
                if not rid or rid == rd.ResourceId.Null() or bstr <= 0:
                    continue
                if rid == fmt_in.vertexResourceId:
                    continue
                try:
                    raw_bi  = bytes(controller.GetBufferData(rid, 0, 0))
                    data_bi = raw_bi[boff:] if boff < len(raw_bi) else raw_bi
                    nv_bi   = len(data_bi) // bstr
                    if nv_bi >= nv // 2:   # plausible vertex count
                        all_vb.append((data_bi, bstr))
                        info_list.append("vsin_gpu: extra VB binding %d stride=%d verts=%d" % (
                            bi, bstr, nv_bi))
                except Exception:
                    pass
        except Exception as e:
            info_list.append("vsin_gpu: GetVertexBuffers() skipped: %s" % str(e))

        # Try to add VS Output vertex buffer as an additional scan source.
        # The vertex shader typically passes UV through to its outputs, so
        # the UV should be present in the VS Output buffer even when it's
        # missing or mis-offset in the VS Input buffer.
        try:
            fmt_out_scan = controller.GetPostVSData(0, 0, rd.MeshDataStage.VSOut)
            if (fmt_out_scan.numIndices > 0 and
                    fmt_out_scan.vertexResourceId != rd.ResourceId.Null()):
                raw_vs = bytes(controller.GetBufferData(
                    fmt_out_scan.vertexResourceId, 0, 0))
                vs_off = fmt_out_scan.vertexByteOffset
                vs_str = fmt_out_scan.vertexByteStride
                vb_vs  = raw_vs[vs_off:] if vs_off < len(raw_vs) else raw_vs
                nv_vs  = len(vb_vs) // vs_str if vs_str > 0 else 0
                if nv_vs >= nv // 2:
                    all_vb.append((vb_vs, vs_str))
                    info_list.append("vsin_gpu: added VS Output VB stride=%d verts=%d" % (
                        vs_str, nv_vs))
        except Exception as e:
            info_list.append("vsin_gpu: VS Output VB: %s" % str(e))

        # Hex dump of first vertex for diagnostics (bytes 0-63 of VB0)
        if vb_data and stride > 0:
            _hdump = " ".join("%02X" % vb_data[i] for i in range(min(stride, 64)))
            info_list.append("vsin_gpu v0_hex: " + _hdump)
            _fdump = " ".join("@%d:%.3f" % (i*4, struct.unpack_from("<f", vb_data, i*4)[0])
                              for i in range(min(stride//4, 16)))
            info_list.append("vsin_gpu v0_f32: " + _fdump)

        # ── All vertex buffer bindings: slot → (bytes, stride, nv) ──────────────
        # Build this BEFORE reading the layout, so each attribute can be read
        # from the correct VB binding (split-VB layout: position in slot 0,
        # normals/UVs in slot 1 etc.).
        #
        # CRITICAL: fmt_in.vertexByteOffset already accounts for both the
        # binding byte-offset AND the base-vertex adjustment:
        #   vertexByteOffset = binding_offset + vertex_start × stride
        # GetVertexBuffers()[slot].byteOffset is ONLY the binding_offset.
        # We must NOT overwrite slot 0 with the GetVertexBuffers data because
        # that would lose the vertex_start correction and cause wrong reads.
        #
        # Strategy:
        #  - slot 0 (primary) → always use vb_data (already correct)
        #  - slot N (secondary) → use GetVertexBuffers data, but apply the
        #    same vertex_start offset so all slots start at the same logical
        #    first vertex.
        _vb_map = {0: (vb_data, stride, nv)}   # slot 0: primary, correct offset

        try:
            _state0  = controller.GetPipelineState()
            _vb_list = _state0.GetVertexBuffers()

            # Compute vertex_start from primary VB (binding_offset for slot 0)
            _bind0_off  = 0
            for _vb_b in _vb_list:
                if getattr(_vb_b, 'resourceId', None) == fmt_in.vertexResourceId:
                    _bind0_off = int(getattr(_vb_b, 'byteOffset', 0) or 0)
                    break
            _vertex_start = (vbo_off - _bind0_off) // stride if stride > 0 else 0

            for _bi, _vb_b in enumerate(_vb_list):
                _rid  = getattr(_vb_b, 'resourceId', None)
                _boff = int(getattr(_vb_b, 'byteOffset', 0) or 0)
                _bstr = int(getattr(_vb_b, 'byteStride', 0) or 0)
                if not _rid or _rid == rd.ResourceId.Null() or _bstr <= 0:
                    continue
                if _rid == fmt_in.vertexResourceId:
                    # PRIMARY VB: reuse vb_data (already has correct vertex_start)
                    _vb_map[_bi] = (vb_data, stride, nv)
                    continue
                # SECONDARY VB: apply vertex_start so all slots start at same vertex
                _raw_b       = bytes(controller.GetBufferData(_rid, 0, 0))
                _eff_off     = _boff + _vertex_start * _bstr
                _data_b      = _raw_b[_eff_off:] if _eff_off < len(_raw_b) else \
                               _raw_b[_boff:] if _boff < len(_raw_b) else _raw_b
                _nv_b        = len(_data_b) // _bstr
                _vb_map[_bi] = (_data_b, _bstr, _nv_b)
        except Exception as _vbe:
            info_list.append("vsin_gpu: GetVertexBuffers err: %s" % _vbe)

        # ── Vertex input layout → byte-offset map ─────────────────────────────
        state   = controller.GetPipelineState()
        va_list = state.GetVertexInputs()

        # Sort by declared location so we can accumulate offsets in HW order
        def _va_loc(iv):
            return getattr(iv[1], 'location', iv[0])
        va_sorted = sorted(enumerate(va_list), key=_va_loc)

        # Collect raw info per slot, including VB binding slot
        slot_info = []   # (slot_i, loc, comp, width, reported_off, vb_binding)
        for slot_i, va in va_sorted:
            fmt_va  = getattr(va, 'format', None)
            comp    = getattr(fmt_va, 'compCount',     4) if fmt_va else 4
            width   = getattr(fmt_va, 'compByteWidth', 4) if fmt_va else 4
            rep_off = int(getattr(va, 'byteOffset', 0) or 0)
            loc     = getattr(va, 'location', slot_i)
            # VB binding slot — try several possible attribute names
            vb_slot = getattr(va, 'binding',           None)
            if vb_slot is None:
                vb_slot = getattr(va, 'vertexBufferSlot', None)
            if vb_slot is None:
                vb_slot = getattr(va, 'inputSlot',        0)
            vb_slot = int(vb_slot or 0)
            slot_info.append((slot_i, loc, comp, width, rep_off, vb_slot))

        # If ALL reported byteOffsets are 0, the API isn't providing them.
        # Compute accumulated offsets instead.  Try two strategies per VB slot
        # (attributes in the same slot share one stride; natural/aligned
        # selection must be done per-slot).
        any_nonzero = any(si[4] > 0 for si in slot_info)

        if any_nonzero:
            # Use API-reported offsets (most accurate)
            computed_offsets = [si[4] for si in slot_info]
            info_list.append("vsin_gpu offsets: api-reported")
        else:
            # Compute offsets per-VB-slot (split VB: each slot has its own stride).
            # Group attributes by their VB binding, compute natural/aligned within
            # each group, then pick the strategy that fits that slot's stride.
            slot_cums = {}   # vb_slot → running natural offset
            aln_cums  = {}   # vb_slot → running aligned offset
            nat_offs  = []
            aln_offs  = []
            for _, _, comp, width, _, vb_slot in slot_info:
                nc = slot_cums.get(vb_slot, 0)
                ac = aln_cums.get(vb_slot, 0)
                nat_offs.append(nc)
                aln_offs.append(ac)
                slot_cums[vb_slot] = nc + comp * width
                raw_size = comp * width
                aln_cums[vb_slot] = ac + (
                    ((raw_size + 4 * width - 1) // (4 * width)) * (4 * width))

            # For each VB slot, choose natural vs aligned by comparing with its stride
            computed_offsets = []
            _strat_log = {}
            for i, (_, _, comp, width, _, vb_slot) in enumerate(slot_info):
                _, _s, _ = _vb_map.get(vb_slot, (vb_data, stride, nv))
                nat_total = slot_cums.get(vb_slot, 0)
                aln_total = aln_cums.get(vb_slot, 0)
                if vb_slot not in _strat_log:
                    if abs(aln_total - _s) <= 4:
                        _strat_log[vb_slot] = "aln"
                    elif abs(nat_total - _s) <= 4:
                        _strat_log[vb_slot] = "nat"
                    else:
                        _strat_log[vb_slot] = "nat+pad"
                computed_offsets.append(
                    aln_offs[i] if _strat_log[vb_slot] == "aln" else nat_offs[i])
            info_list.append("vsin_gpu offsets: per-slot %s" % _strat_log)

        # Identify comp=2 slots as UV candidates (hint for user)
        uv_hints = [
            "_input%d/ATTRIBUTE%d(off%d,vb%d)" % (loc, loc, computed_offsets[i], vb_slot)
            for i, (_, loc, comp, _, _, vb_slot) in enumerate(slot_info) if comp == 2
        ]
        if uv_hints:
            info_list.append("vsin_gpu UV_candidates(comp=2): %s" % " ".join(uv_hints))

        layout = {}   # name_variant -> (byteOffset, compCount, compByteWidth, fmtChar, vb_slot)
        for idx, (slot_i, loc, comp, width, _, vb_slot) in enumerate(slot_info):
            off  = computed_offsets[idx]
            # Format char: 4-byte→float, 2-byte→half-float, 1-byte→signed byte
            if   width == 4: fc = "f"
            elif width == 2: fc = "e"   # half-float (Python ≥ 3.6)
            elif width == 1: fc = "b"   # int8 (packed SNORM tangent/normal)
            else:            fc = "f"
            info = (off, comp, width, fc, vb_slot)   # 5-tuple incl. vb_slot

            # Collect name aliases
            va    = va_sorted[idx][1]
            sname = (getattr(va, 'semanticName', '') or '').strip()
            sidx  = getattr(va, 'semanticIndex', 0)
            candidates = set()
            if sname:
                candidates.add(sname + str(sidx))
                if sidx == 0:
                    candidates.add(sname)
                candidates.add(sname.upper() + str(sidx))
            candidates.add("_input%d" % loc)
            candidates.add("_input%d" % slot_i)
            candidates.add("ATTRIBUTE%d" % loc)
            candidates.add("ATTRIBUTE%d" % slot_i)

            for name in candidates:
                if name and name not in layout:
                    layout[name] = info

        # Show slot details so user can identify UV / Color names
        slot_detail = []
        for idx, (slot_i, loc, comp, width, _, vb_slot) in enumerate(slot_info):
            slot_detail.append("loc%d:off%d:comp%d:vb%d" % (
                loc, computed_offsets[idx], comp, vb_slot))
        info_list.append("vsin_gpu slots=[%s]" % " ".join(slot_detail))
        info_list.append("vsin_gpu layout keys=[%s]" %
                         ",".join(sorted(layout.keys())[:14]))

        # ── Read each requested attribute ──────────────────────────────────────
        for key in ("POSITION", "NORMAL", "TANGENT", "BINORMAL", "COLOR", "UV", "UV2"):
            attr_name = mapper.get(key, "")
            if not attr_name:
                continue
            if attr_name not in layout:
                info_list.append("  %s=%r -> MISSING (not in layout)" % (key, attr_name))
                continue
            off, comp, width, fc, vb_slot = layout[attr_name]
            # Read from the correct VB binding (split-VB aware)
            _vbd, _vbstr, _vbnv = _vb_map.get(vb_slot, (vb_data, stride, nv))
            verts = []
            for vi in range(_vbnv):
                base = vi * _vbstr + off
                if base + comp * width > len(_vbd):
                    break
                raw = list(struct.unpack_from("<%d%s" % (comp, fc), _vbd, base))
                # Normalise packed integer types to float range
                if fc == "b":          # int8 SNORM → [-1, 1]
                    raw = [v / 127.0 for v in raw]
                elif fc == "B":        # uint8 UNORM → [0, 1]
                    raw = [v / 255.0 for v in raw]
                # half-float "e" is already a Python float — no conversion needed
                verts.append(raw)

            # ── For UV/UV2: verify values look like UV, scan if not ────────────
            # "Collapsed to a point" happens when:
            # (a) offset is wrong (reads from padding → all zeros), or
            # (b) format is half-float but API reports width=4 (float32) → tiny
            #
            # Detection: if max absolute value across first 20 verts < 0.01,
            # scan the ENTIRE vertex stride for float16 / float32 pairs whose
            # values are in the plausible UV range [0.001, 10] with variation.
            if key in ("UV", "UV2") and comp == 2:
                _sample = [abs(v) for e in verts[:20] for v in e
                           if v == v and not (v != v)]  # filter NaN
                _max_v  = max(_sample) if _sample else 0.0

                if _max_v < 0.01:
                    # Current read gives no real UV data.
                    # Scan EVERY bound vertex buffer at EVERY 2-byte-aligned offset,
                    # trying both float16 and float32.
                    # Rejection heuristics (to avoid bone-weights / normals):
                    #   • Normals (SNORM): u+v ≈ const per vertex → low std-dev of sums
                    #   • Bone-weights: u+v ≈ 1.0 → mean(abs(u+v-1)) < 0.05
                    #   • UV: values cover 2-D area, sums NOT pinned to 1
                    info_list.append("  %s: near-zero (max=%.2e), scanning all VBs…" % (key, _max_v))
                    _best_score, _best_verts, _best_desc = -1, None, ""

                    for _vbi, (_vbd, _vbstr) in enumerate(all_vb):
                        _nvb = len(_vbd) // _vbstr if _vbstr > 0 else 0
                        # For primary VB (vbi=0): skip known attribute region
                        # (offsets 0..nat_cum-1 are position/tangent/normal, NOT UV).
                        # For VS Output buffer: skip SV_Position (first 16 bytes).
                        if _vbi == 0:
                            _start_off = nat_cum      # scan only the "extra" region
                        elif _vbi == len(all_vb) - 1:
                            _start_off = 16           # skip SV_Position in VS Out
                        else:
                            _start_off = 0
                        for _scan_off in range(_start_off, _vbstr - 1, 2):
                            for _sfmt, _sbpc in (("e", 2), ("f", 4)):
                                _need = comp * _sbpc
                                if _scan_off + _need > _vbstr:
                                    continue
                                _sv = []
                                for _vi in range(min(50, _nvb)):
                                    _b = _vi * _vbstr + _scan_off
                                    if _b + _need > len(_vbd):
                                        break
                                    _sv.append(list(struct.unpack_from(
                                        "<%d%s" % (comp, _sfmt), _vbd, _b)))
                                if len(_sv) < 5:
                                    continue
                                _vals = [v for _e in _sv for v in _e]
                                _in_range = sum(1 for v in _vals if 0.001 <= abs(v) <= 10.0)
                                _nonzero  = sum(1 for v in _vals if abs(v) > 0.001)
                                _vmax     = max(abs(v) for v in _vals)
                                _unique   = len(set(round(v, 3) for v in _vals))
                                # Bone-weight detection: u+v must be BOTH pinned near
                                # 1.0 (>95%) AND very low variance — normal UV can
                                # have u+v≈1 coincidentally, but bone weights ALWAYS
                                # sum to exactly 1 with near-zero variance.
                                _sums = [_sv[i][0] + _sv[i][1] for i in range(len(_sv))]
                                _mean_s = sum(_sums) / max(len(_sums), 1)
                                _var_s  = sum((s - _mean_s)**2 for s in _sums) / max(len(_sums), 1)
                                _n1 = sum(1 for s in _sums if abs(s - 1.0) < 0.03)
                                if _n1 > len(_sums) * 0.95 and _var_s < 0.005:
                                    continue   # almost certainly bone weights
                                # Require genuine 2D spread in both axes
                                _us = [_sv[i][0] for i in range(len(_sv))]
                                _vs_v = [_sv[i][1] for i in range(len(_sv))]
                                _urange = max(_us) - min(_us) if _us else 0
                                _vrange = max(_vs_v) - min(_vs_v) if _vs_v else 0
                                if _urange < 0.02 or _vrange < 0.02:
                                    continue   # degenerate (collapsed axis)
                                # Bonus for all-positive values (UV coords are ≥0)
                                _all_pos = sum(1 for v in _vals if v >= 0)
                                _score = (_in_range + _nonzero * 2 + min(_unique, 20)
                                          + (_all_pos // 2))
                                if (0.001 <= _vmax <= 10.0 and
                                        _nonzero >= len(_vals) * 0.3 and
                                        _in_range >= len(_vals) * 0.7 and
                                        _score > _best_score):
                                    _best_score = _score
                                    _best_desc  = "vb%d off=%d fmt=%s str=%d" % (
                                        _vbi, _scan_off, _sfmt, _vbstr)
                                    _best_verts = (_vbd, _vbstr, _nvb, _scan_off, _sfmt, _sbpc)

                    if _best_verts is not None:
                        _vbd, _vbstr, _nvb, _scan_off, _sfmt, _sbpc = _best_verts
                        _new_verts = []
                        for _vi in range(_nvb):
                            _b = _vi * _vbstr + _scan_off
                            if _b + comp * _sbpc > len(_vbd):
                                break
                            _new_verts.append(list(struct.unpack_from(
                                "<%d%s" % (comp, _sfmt), _vbd, _b)))
                        if _new_verts:
                            verts = _new_verts
                            info_list.append("  %s: scan→ %s (score=%d  uR=%.2f vR=%.2f)" % (
                                key, _best_desc, _best_score,
                                max(v[0] for v in _new_verts[:20]) - min(v[0] for v in _new_verts[:20]),
                                max(v[1] for v in _new_verts[:20]) - min(v[1] for v in _new_verts[:20])))
                    else:
                        info_list.append("  %s: scan found nothing in any VB" % key)

            attr_data[attr_name] = verts
            info_list.append("  %s=%r -> OK  %d verts  comp=%d  byteOff=%d  fmt=%s" % (
                key, attr_name, len(verts), comp, off, fc))

        # ── Auto-detect missing UV2 / Color when Vulkan remaps attribute IDs ──
        # Vulkan relocates ATTRIBUTE6 (UV2) and ATTRIBUTE13 (Color) to lower
        # locations that don't match Unreal's D3D semantic numbers.  When the
        # mapper name isn't found in the layout, scan the vertex buffer for a
        # candidate that matches the expected component pattern and value range.
        _uv_attr  = mapper.get("UV",    "")
        _uv2_attr = mapper.get("UV2",   "")
        _col_attr = mapper.get("COLOR", "")

        # Compute UV0 centre-of-mass so UV2 candidates can be discriminated.
        _uv0_cx = _uv0_cy = 0.0
        _uv0_n  = 0
        if _uv_attr and attr_data.get(_uv_attr):
            _uv0s = attr_data[_uv_attr][:50]
            if _uv0s:
                _uv0_cx = sum(e[0] for e in _uv0s) / len(_uv0s)
                _uv0_cy = sum(e[1] for e in _uv0s) / len(_uv0s)
                _uv0_n  = len(_uv0s)

        for _mkey, _mattr, _mcomp in [("UV2", _uv2_attr, 2),
                                       ("COLOR", _col_attr, 4)]:
            if not _mattr or attr_data.get(_mattr):
                continue       # already found or not requested
            info_list.append("  auto-scan %s (attr=%r)…" % (_mkey, _mattr))
            _best_sc, _best_info = -1, None

            for _avbd, _avbstr in all_vb:
                _anvb = len(_avbd) // _avbstr if _avbstr > 0 else 0
                for _aso in range(0, _avbstr, 2):
                    for _asfmt, _asbpc in [("f", 4), ("e", 2), ("B", 1), ("b", 1)]:
                        _anb = _mcomp * _asbpc
                        if _aso + _anb > _avbstr:
                            continue
                        _asv = []
                        for _avi in range(min(50, _anvb)):
                            _ab = _avi * _avbstr + _aso
                            if _ab + _anb > len(_avbd): break
                            _araw = list(struct.unpack_from(
                                "<%d%s" % (_mcomp, _asfmt), _avbd, _ab))
                            if _asfmt == "b": _araw = [v/127.0 for v in _araw]
                            elif _asfmt == "B": _araw = [v/255.0 for v in _araw]
                            _asv.append(_araw)
                        if len(_asv) < 5: continue
                        _avals = [v for e in _asv for v in e]

                        if _mkey == "UV2":
                            _aus = [e[0] for e in _asv]
                            _avs = [e[1] for e in _asv]
                            _ura = max(_aus) - min(_aus) if _aus else 0
                            _vra = max(_avs) - min(_avs) if _avs else 0
                            _amx = max(abs(v) for v in _avals)
                            if not (0.001 <= _amx <= 10 and _ura >= 0.02 and _vra >= 0.02):
                                continue
                            # Reject if centre matches UV0 (same channel)
                            if _uv0_n:
                                _acx = sum(_aus) / len(_aus)
                                _acy = sum(_avs) / len(_avs)
                                if abs(_acx - _uv0_cx) + abs(_acy - _uv0_cy) < 0.05:
                                    continue
                            _asc = (sum(1 for v in _avals if 0.001 <= abs(v) <= 10) +
                                    len(set(round(v, 2) for v in _avals)))
                        else:  # COLOR
                            # UNORM: all values in [0,1], none negative
                            if any(v < -0.01 for v in _avals): continue
                            if max(_avals) > 1.05: continue
                            _asc = (sum(1 for v in _avals if v > 0.001) +
                                    len(set(round(v, 2) for v in _avals)))

                        if _asc > _best_sc:
                            _best_sc   = _asc
                            _best_info = (_avbd, _avbstr, _anvb, _aso, _asfmt, _asbpc)

            if _best_info:
                _avbd, _avbstr, _anvb, _aso, _asfmt, _asbpc = _best_info
                _afinal = []
                for _avi in range(_anvb):
                    _ab = _avi * _avbstr + _aso
                    if _ab + _mcomp * _asbpc > len(_avbd): break
                    _araw = list(struct.unpack_from(
                        "<%d%s" % (_mcomp, _asfmt), _avbd, _ab))
                    if _asfmt == "b": _araw = [v/127.0 for v in _araw]
                    elif _asfmt == "B": _araw = [v/255.0 for v in _araw]
                    _afinal.append(_araw)
                if _afinal:
                    attr_data[_mattr] = _afinal
                    info_list.append("  %s=%r auto-found: off=%d fmt=%s score=%d %dverts" % (
                        _mkey, _mattr, _aso, _asfmt, _best_sc, len(_afinal)))

    except Exception:
        import traceback
        info_list.append("vsin_gpu ERROR: " + traceback.format_exc().split('\n')[-2])

    # ── Supplemental: read ALL layout slots by _inputN / ATTRIBUTE{N} names ──
    # _read_vsin_attrs_from_gpu only reads mapper-specified attributes by their
    # exact name (e.g. "POSITION").  For Vulkan captures the vertex input layout
    # only contains location-based names (_input0, ATTRIBUTE0, …) — semantic
    # names like "POSITION" or "VERTEX" don't appear.  Without this pass,
    # Unity/Godot presets find nothing to export in batch mode even though the
    # single-export path works (because _collect_mesh_data + _add_input_aliases
    # covers the mismatch).
    #
    # By reading every slot here, _alias_vsin_data can later build POSITION from
    # _input0, TEXCOORD0 from _input3, etc. — matching single-export behaviour.
    try:
        _seen_off_vb_comp = set()   # deduplicate slots: (vb_slot, offset, comp)
        for _sname, (_soff, _scomp, _swidth, _sfc, _svb) in layout.items():
            # Only process the canonical _inputN / ATTRIBUTE{N} names once
            if not (_sname.startswith("_input") or _sname.startswith("ATTRIBUTE")):
                continue
            if _sname in attr_data:
                continue                     # already populated by main read loop
            _dedup_key = (_svb, _soff, _scomp)
            if _dedup_key in _seen_off_vb_comp:
                continue
            _seen_off_vb_comp.add(_dedup_key)

            _svbd, _svbstr, _svbnv = _vb_map.get(_svb, (vb_data, stride, nv))
            _sverts = []
            for _vi in range(_svbnv):
                _base = _vi * _svbstr + _soff
                if _base + _scomp * _swidth > len(_svbd):
                    break
                _raw = list(struct.unpack_from(
                    "<%d%s" % (_scomp, _sfc), _svbd, _base))
                if   _sfc == "b": _raw = [v / 127.0 for v in _raw]
                elif _sfc == "B": _raw = [v / 255.0 for v in _raw]
                _sverts.append(_raw)
            if _sverts:
                attr_data[_sname] = _sverts
    except Exception:
        pass   # supplemental read is best-effort; don't mask the main result

    return attr_data, vsin_nidxs



def write_vsout_fbx(save_path, mapper, info_list, err_list,
                      vs_in_data, vs_in_attr_list, controller):
    """Export VS Output mesh with reconstructed view-space positions.

    Vertex positions come from SV_Position (clip-space) + projection-matrix
    reconstruction.  UV and Normal channels are optionally sourced from the
    VS Input vertex buffer (same draw call, same index buffer) and written as
    proper FBX LayerElement blocks so DCC tools receive correct texture
    mapping without a second import step.

    Args:
        vs_in_data:      dict returned by _collect_mesh_data (VS Input table),
                         or None if the caller chose not to include VS In attrs.
        vs_in_attr_list: set of attribute names present in vs_in_data, or None.
    """
    import traceback
    try:
        fmt_out    = controller.GetPostVSData(0, 0, rd.MeshDataStage.VSOut)
        status_str = str(fmt_out.status) if fmt_out.status else ""
        if status_str:
            err_list.append("GetPostVSData(VSOut) failed: %s" % status_str)
            return

        if fmt_out.numIndices == 0:
            err_list.append("VS Output has 0 vertices")
            return

        out_buf   = bytes(controller.GetBufferData(fmt_out.vertexResourceId, 0, 0))
        out_vbo   = fmt_out.vertexByteOffset
        out_bytes = out_buf[out_vbo:] if out_vbo < len(out_buf) else out_buf
        stride    = fmt_out.vertexByteStride
        cc        = fmt_out.format.compCount
        cw        = fmt_out.format.compByteWidth
        char      = "f" if cw == 4 else "d"
        actual    = len(out_bytes) // stride
        near      = fmt_out.nearPlane
        far       = fmt_out.farPlane

        clip_pos = []
        for i in range(actual):
            base = i * stride
            if base + 4 * cw > len(out_bytes):
                break
            comps = struct.unpack_from("<%d%s" % (min(cc, 4), char), out_bytes, base)
            clip_pos.append(comps)

        if not clip_pos:
            err_list.append("No clip positions read")
            return

        m00 = m11 = None
        try:
            fmt_in    = controller.GetPostVSData(0, 0, rd.MeshDataStage.VSIn)
            si        = str(fmt_in.status) if fmt_in.status else ""
            if not si and fmt_in.numIndices > 0:
                in_buf    = bytes(controller.GetBufferData(fmt_in.vertexResourceId, 0, 0))
                in_vbo    = fmt_in.vertexByteOffset
                in_bytes  = in_buf[in_vbo:] if in_vbo < len(in_buf) else in_buf
                in_stride = fmt_in.vertexByteStride
                in_cc     = fmt_in.format.compCount
                in_cw     = fmt_in.format.compByteWidth
                in_char   = "f" if in_cw == 4 else "d"
                in_actual = min(len(in_bytes) // in_stride, actual)
                m00_list, m11_list = [], []
                for i in range(min(in_actual, 100)):
                    base = i * in_stride
                    if base + in_cc * in_cw > len(in_bytes):
                        break
                    vp     = struct.unpack_from("<%d%s" % (in_cc, in_char), in_bytes, base)
                    if len(clip_pos) <= i:
                        break
                    cp     = clip_pos[i]
                    cw_val = cp[3] if len(cp) >= 4 else 1.0
                    if abs(cw_val) < 0.001:
                        continue
                    if len(vp) >= 1 and abs(vp[0]) > 0.01:
                        m00_list.append(cp[0] / vp[0])
                    if len(vp) >= 2 and abs(vp[1]) > 0.01:
                        m11_list.append(cp[1] / vp[1])
                if m00_list:
                    m00_list.sort()
                    m00 = m00_list[len(m00_list) // 2]
                if m11_list:
                    m11_list.sort()
                    m11 = m11_list[len(m11_list) // 2]
        except Exception:
            pass

        aspect = 1.0
        try:
            state = controller.GetPipelineState()
            vp    = state.GetViewport(0)
            if vp.height > 0:
                aspect = float(vp.width) / float(vp.height)
        except Exception:
            pass

        if not (m00 and m11 and abs(m00 - 1.0) > 0.01):
            m11 = 1.732
            m00 = m11 / aspect if aspect > 0 else m11

        info_list.append("aspect=%.4f m00=%.4f m11=%.4f actual=%d" % (
            aspect, m00, m11, actual))

        # Build vertex positions.
        # IMPORTANT: write a placeholder (0,0,0) instead of skipping degenerate
        # vertices (w≈0) so that every clip_pos[i] maps to vertex index i.
        # Skipping with `continue` would shift subsequent indices and corrupt faces.
        vertices = []
        for cp in clip_pos:
            cx, cy, cz = cp[0], cp[1], cp[2]
            cw_val = cp[3] if len(cp) >= 4 else 1.0
            if abs(cw_val) < 1e-6:
                # Degenerate clip vertex — emit a placeholder to keep index alignment
                vertices.extend([0.0, 0.0, 0.0])
                continue
            ndc_z = cz / cw_val
            if far > 1e30:
                denom  = 1.0 - ndc_z
                view_z = near / denom if abs(denom) > 1e-9 else cw_val
            else:
                denom  = far - ndc_z * (far - near)
                view_z = (near * far / denom) if abs(denom) > 1e-9 else cw_val
            vx = cx / m00
            vy = cy / m11
            vertices.extend([vx, vy, view_z])

        # ── Optional: bake view-space → world-space via inv(ViewMatrix) ───────
        bake_world = mapper.get("BAKE_WORLD_SPACE", False)
        _view_inv  = None
        if bake_world:
            _vm = fetch_view_matrix(controller)
            if _vm:
                _view_inv = rigid_inverse(_vm)
                info_list.append("world-space: ViewMatrix found, baking positions")
            else:
                info_list.append("world-space: ViewMatrix NOT found, staying view-space")
        if _view_inv and len(vertices) % 3 == 0:
            _ws = []
            for _vi3 in range(len(vertices) // 3):
                _vx, _vy, _vz = vertices[_vi3*3], vertices[_vi3*3+1], vertices[_vi3*3+2]
                _wx, _wy, _wz = mat4_transform(_view_inv, _vx, _vy, _vz)
                _ws.extend([_wx, _wy, _wz])
            vertices = _ws

        if len(vertices) >= 9:
            info_list.append("v0=%s v1=%s v2=%s" % (
                [round(x, 3) for x in vertices[0:3]],
                [round(x, 3) for x in vertices[3:6]],
                [round(x, 3) for x in vertices[6:9]]))

        # Read the real index buffer that connects vertices into triangles.
        # Sequential fallback is used only for non-indexed (vertex-array) draws.
        idx_list = _read_index_buffer(fmt_out, controller)
        has_ib   = idx_list is not None
        if not has_ib:
            idx_list = list(range(len(clip_pos)))
        n_fc = len(idx_list)   # face corners — must be defined before GPU attr read
        info_list.append("index_buf=%s  faces=%d" % (
            "yes" if has_ib else "no (sequential)", n_fc // 3))

        polygons = [~idx if i % 3 == 2 else idx for i, idx in enumerate(idx_list)]

        save_name = os.path.basename(os.path.splitext(save_path)[0])

        # ── VS Input attribute pass-through ───────────────────────────────────
        # VS Output MeshFormat only exposes SV_Position.  All other channels
        # (UV, UV2, Normal, Tangent, BiNormal, Color) are borrowed from the VS
        # Input vertex buffer, which uses the same index buffer and therefore
        # the same vertex ordering as the VS Output buffer.
        #
        # Mapping strategy
        # ─────────────────
        # UV / UV2  →  per-unique-vertex (IndexToDirect).
        #   The table has one row per draw-index; we deduplicate by vertex-index
        #   to build a compact UV array, then use idx_list as the UV-index array.
        #   This matches how export_fbx writes UV.
        #
        # Normal / Tangent / BiNormal / Color  →  per-polygon-vertex (Direct /
        #   IndexToDirect-with-sequential-indices).
        #   We write vs_in_data[attr][i] for face-corner i because the VS Input
        #   table rows are in draw-index order, identical to idx_list order.
        #   This preserves hard-edge (seam) normals correctly.

        ENGINE  = mapper.get("ENGINE",   "unity")
        flip_u  = mapper.get("FLIP_U",  False)
        flip_v  = mapper.get("FLIP_V",  True)

        UV      = mapper.get("UV",       "")
        UV2     = mapper.get("UV2",      "")
        NORMAL  = mapper.get("NORMAL",   "")
        TANGENT = mapper.get("TANGENT",  "")
        BINORM  = mapper.get("BINORMAL", "")
        COLOR   = mapper.get("COLOR",    "")

        vsout_uv      = mapper.get("VSOUT_INCLUDE_VSIN_UV",      True)
        vsout_uv2     = mapper.get("VSOUT_INCLUDE_VSIN_UV2",     True)
        vsout_normal  = mapper.get("VSOUT_INCLUDE_VSIN_NORMAL",  True)
        vsout_tangent = mapper.get("VSOUT_INCLUDE_VSIN_TANGENT", True)
        vsout_binorm  = mapper.get("VSOUT_INCLUDE_VSIN_BINORMAL",True)
        vsout_color   = mapper.get("VSOUT_INCLUDE_VSIN_COLOR",   True)

        # Warn when all pass-through options are disabled
        flags = [vsout_uv, vsout_uv2, vsout_normal, vsout_tangent, vsout_binorm, vsout_color]
        if not any(flags):
            info_list.append("WARNING: ALL VS-In pass-through checkboxes are OFF "
                             "→ open Export Mesh dialog and check them in 'VS Output Extras'")

        layer_uv      = "";  layer_uv_ins  = ""
        layer_uv2     = "";  layer_uv2_ins = ""
        layer_nrm     = "";  layer_nrm_ins = ""
        layer_tan     = "";  layer_tan_ins = ""
        layer_bn      = "";  layer_bn_ins  = ""
        layer_col     = "";  layer_col_ins = ""

        # ── Diagnostic: always report what VS Input data we have ──────────────
        _d = []
        if vs_in_data is None:
            _d.append("qt_table=None")
        else:
            _tmp_idx  = vs_in_data.get("IDX", [])
            _tmp_att  = vs_in_attr_list or set()
            _d.append("qt_rows=%d attrs=[%s]" % (
                len(_tmp_idx), ",".join(sorted(_tmp_att)[:6])))
        info_list.append("vsin_qt: " + "  ".join(_d))

        # ── Read VS Input attributes directly from GPU vertex buffer ──────────
        # This is reliable regardless of which tab (VS In / VS Out) the user
        # has selected in the Mesh Viewer, because the Qt table repopulates with
        # VS Output data in VS Output view mode (Vulkan: _inputN names).
        vsin_raw, vsin_nidxs_gpu = read_gpu_attributes(
            mapper, info_list, controller)

        # vsin_nidxs: normalized 0-based VS Input vertex index per face corner
        # Used as UV IndexToDirect index array — must come from the VS Input IB,
        # NOT from idx_list (the VS Output expanded sequential indices).
        vsin_nidxs = vsin_nidxs_gpu[:n_fc] if vsin_nidxs_gpu else []
        if len(vsin_nidxs) < n_fc:
            vsin_nidxs.extend([0] * (n_fc - len(vsin_nidxs)))

        # ── Override UV with Qt-table data (RenderDoc decoded the format) ────
        # The Qt mesh-viewer table has per-draw-corner float UV already decoded
        # from whatever GPU format (float16, float32 …).  This is more reliable
        # than our own GPU byte-offset + format guessing in _read_vsin_attrs_from_gpu.
        # The "IDX" column gives raw VS-Input vertex indices matching the draw IB.
        _qt_uv_key = None
        # Search order: mapper UV key first, then common Vulkan locations.
        # For each candidate, verify the data actually looks like UV (float2,
        # values in [0, 10] range, non-zero variation) so we skip normals /
        # tangents / other comp=2 attributes that aren't UV.
        def _uv_candidate_score(key):
            """Return a score ≥1 if key looks like UV, else 0."""
            if not (vs_in_data and vs_in_data.get(key)):
                return 0
            _s = vs_in_data[key]
            if not (_s and hasattr(_s[0], '__len__') and len(_s[0]) >= 2):
                return 0
            _vals = [abs(v) for e in _s[:30] for v in e[:2] if v == v]
            _mx = max(_vals) if _vals else 0
            _nz = sum(1 for v in _vals if v > 0.001)
            if not (0.001 <= _mx <= 10.0 and _nz >= len(_vals) * 0.3):
                return 0
            return _nz + len(set(round(v, 2) for v in _vals))  # higher = more varied

        _qt_uv_key = None
        _best_uv_score = 0
        for _qk in ([UV] if UV else []) + ["_input4", "_input3", "_input2"]:
            _sc = _uv_candidate_score(_qk)
            if _sc > _best_uv_score:
                _best_uv_score = _sc
                _qt_uv_key = _qk
            # Stop early if the mapper's own key scored well — trust it
            if _qt_uv_key == UV and _sc >= 10:
                break

        if vsout_uv and _qt_uv_key:
            try:
                _qt_uv_corn = vs_in_data[_qt_uv_key]   # [u,v] per draw corner
                _qt_idx_raw = vs_in_data.get("IDX", [])
                if _qt_idx_raw and len(_qt_uv_corn) >= n_fc:
                    _qi = [int(float(x)) for x in _qt_idx_raw[:n_fc]]
                    _min_qi = min(_qi) if _qi else 0
                    _uv_map = {}
                    for _fc in range(n_fc):
                        _ni = _qi[_fc] - _min_qi
                        if _ni not in _uv_map:
                            _uv_map[_ni] = list(_qt_uv_corn[_fc][:2])
                    if _uv_map:
                        _max_ni = max(_uv_map.keys())
                        vsin_raw[UV] = [_uv_map.get(_i, [0.0, 0.0])
                                        for _i in range(_max_ni + 1)]
                        # Rebuild corner→vertex index from Qt IDX (consistency)
                        vsin_nidxs = [_qi[fc] - _min_qi for fc in range(n_fc)]
                        info_list.append("UV: Qt-table override (%s score=%d) %d unique/%d corners" % (
                            _qt_uv_key, _best_uv_score, len(_uv_map), n_fc))
            except Exception as _e_qt:
                info_list.append("UV: Qt-table override failed: %s" % str(_e_qt))

        # ── UV2 Qt-table override (second comp=2 key, different from UV0) ─────
        if vsout_uv2 and UV2 and not vsin_raw.get(UV2) and vs_in_data:
            try:
                _uv2_score, _qt_uv2_key = -1, None
                for _qk2 in ([UV2] if UV2 else []) + ["_input3", "_input4", "_input2"]:
                    if _qk2 == _qt_uv_key:
                        continue    # don't re-use the UV0 key
                    _s2 = vs_in_data.get(_qk2)
                    if not _s2: continue
                    if not (hasattr(_s2[0], '__len__') and len(_s2[0]) >= 2): continue
                    _v2 = [abs(v) for e in _s2[:30] for v in e[:2] if v == v]
                    if not _v2 or max(_v2) < 0.001: continue
                    _sc2 = sum(1 for v in _v2 if 0.001 <= v <= 10) + len(set(round(v, 2) for v in _v2))
                    if _sc2 > _uv2_score:
                        _uv2_score  = _sc2
                        _qt_uv2_key = _qk2

                if _qt_uv2_key:
                    _qt_uv2_corn = vs_in_data[_qt_uv2_key]
                    _qt_idx2     = vs_in_data.get("IDX", [])
                    if _qt_idx2 and len(_qt_uv2_corn) >= n_fc:
                        _qi2 = [int(float(x)) for x in _qt_idx2[:n_fc]]
                        _min2 = min(_qi2)
                        _uv2_map = {}
                        for _fc2 in range(n_fc):
                            _ni2 = _qi2[_fc2] - _min2
                            if _ni2 not in _uv2_map:
                                _uv2_map[_ni2] = list(_qt_uv2_corn[_fc2][:2])
                        if _uv2_map:
                            vsin_raw[UV2] = [_uv2_map.get(_i2, [0.0, 0.0])
                                             for _i2 in range(max(_uv2_map) + 1)]
                            info_list.append("UV2: Qt-table (%s score=%d) %d unique" % (
                                _qt_uv2_key, _uv2_score, len(_uv2_map)))
            except Exception as _e_uv2:
                info_list.append("UV2: Qt-table failed: %s" % str(_e_uv2))

        def _xform3(vals):
            if ENGINE != "unreal":
                return list(vals[:3])
            x, y, z = vals[:3]
            return [-x, z, -y]

        def _safe_vert(attr_verts, vi, default):
            """Return vertex data at normalized index vi, or default."""
            return attr_verts[vi] if vi < len(attr_verts) else default

        # ── UV0 (IndexToDirect, per-unique-vertex) ───────────────────────────
        if vsout_uv and UV and vsin_raw.get(UV):
            uv_verts = vsin_raw[UV]
            uvs = [
                str((1.0 - v if flip_u else v) if dim == 0
                    else (1.0 - v if flip_v else v))
                for vals in uv_verts
                for dim, v in enumerate(vals[:2])
            ]
            uvi = ",".join(str(i) for i in vsin_nidxs)
            layer_uv = """
                LayerElementUV: 0 {
                    Version: 101
                    Name: "map1"
                    MappingInformationType: "ByPolygonVertex"
                    ReferenceInformationType: "IndexToDirect"
                    UV: *%(uvs_num)s {
                        a: %(uvs)s
                    }
                    UVIndex: *%(uvi_num)s {
                        a: %(uvi)s
                    }
                }
            """ % {"uvs": ",".join(uvs), "uvs_num": len(uvs),
                   "uvi": uvi,            "uvi_num": n_fc}
            layer_uv_ins = """
                LayerElement: {
                    Type: "LayerElementUV"
                    TypedIndex: 0
                }
            """
            info_list.append("uv=%s (%d unique)" % (UV, len(uv_verts)))

        # ── UV1 (IndexToDirect, per-unique-vertex) ───────────────────────────
        if vsout_uv2 and UV2 and vsin_raw.get(UV2):
            uv2_verts = vsin_raw[UV2]
            uvs2 = [
                str((1.0 - v if flip_u else v) if dim == 0
                    else (1.0 - v if flip_v else v))
                for vals in uv2_verts
                for dim, v in enumerate(vals[:2])
            ]
            uvi2 = ",".join(str(i) for i in vsin_nidxs)
            layer_uv2 = """
                LayerElementUV: 1 {
                    Version: 101
                    Name: "map2"
                    MappingInformationType: "ByPolygonVertex"
                    ReferenceInformationType: "IndexToDirect"
                    UV: *%(uvs_num)s {
                        a: %(uvs)s
                    }
                    UVIndex: *%(uvi_num)s {
                        a: %(uvi)s
                    }
                }
            """ % {"uvs": ",".join(uvs2), "uvs_num": len(uvs2),
                   "uvi": uvi2,            "uvi_num": n_fc}
            layer_uv2_ins = """
                LayerElement: {
                    Type: "LayerElementUV"
                    TypedIndex: 1
                }
            """
            info_list.append("uv2=%s (%d unique)" % (UV2, len(uv2_verts)))

        # ── Normal (ByPolygonVertex Direct, via vertex-index lookup) ─────────
        info_list.append("nrm_check: vsout_normal=%s NORMAL=%r has=%s" % (
            vsout_normal, NORMAL, bool(vsin_raw.get(NORMAL, None))))
        if vsout_normal and NORMAL and vsin_raw.get(NORMAL):
            nrm_verts = vsin_raw[NORMAL]
            nrms = []
            for fc_i in range(n_fc):
                vi = vsin_nidxs[fc_i]
                n  = _xform3(_safe_vert(nrm_verts, vi, [0.0, 0.0, 1.0]))
                nrms.extend(str(x) for x in n)
            layer_nrm = """
                LayerElementNormal: 0 {
                    Version: 101
                    Name: ""
                    MappingInformationType: "ByPolygonVertex"
                    ReferenceInformationType: "Direct"
                    Normals: *%(n)s {
                        a: %(v)s
                    }
                }
            """ % {"n": len(nrms), "v": ",".join(nrms)}
            layer_nrm_ins = """
                LayerElement: {
                    Type: "LayerElementNormal"
                    TypedIndex: 0
                }
            """
            info_list.append("normal=%s (%d corners)" % (NORMAL, n_fc))

        # ── Tangent (ByPolygonVertex Direct) ─────────────────────────────────
        if vsout_tangent and TANGENT and vsin_raw.get(TANGENT):
            tan_verts = vsin_raw[TANGENT]
            tans = []
            for fc_i in range(n_fc):
                vi = vsin_nidxs[fc_i]
                t  = _xform3(_safe_vert(tan_verts, vi, [1.0, 0.0, 0.0]))
                tans.extend(str(x) for x in t)
            layer_tan = """
                LayerElementTangent: 0 {
                    Version: 101
                    Name: "map1"
                    MappingInformationType: "ByPolygonVertex"
                    ReferenceInformationType: "Direct"
                    Tangents: *%(n)s {
                        a: %(v)s
                    }
                }
            """ % {"n": len(tans), "v": ",".join(tans)}
            layer_tan_ins = """
                LayerElement: {
                    Type: "LayerElementTangent"
                    TypedIndex: 0
                }
            """
            info_list.append("tangent=%s" % TANGENT)

        # ── BiNormal (ByPolygonVertex Direct) ────────────────────────────────
        if vsout_binorm and BINORM and vsin_raw.get(BINORM):
            bn_verts = vsin_raw[BINORM]
            bns = []
            for fc_i in range(n_fc):
                vi = vsin_nidxs[fc_i]
                b  = _xform3(_safe_vert(bn_verts, vi, [0.0, 1.0, 0.0]))
                bns.extend(str(-float(x)) for x in b)
            layer_bn = """
                LayerElementBinormal: 0 {
                    Version: 101
                    Name: "map1"
                    MappingInformationType: "ByPolygonVertex"
                    ReferenceInformationType: "Direct"
                    Binormals: *%(n)s {
                        a: %(v)s
                    }
                    BinormalsW: *%(wn)s {
                        a: %(w)s
                    }
                }
            """ % {"n":  len(bns), "v": ",".join(bns),
                   "wn": n_fc,     "w": ",".join(["1"] * n_fc)}
            layer_bn_ins = """
                LayerElement: {
                    Type: "LayerElementBinormal"
                    TypedIndex: 0
                }
            """
            info_list.append("binormal=%s" % BINORM)

        # ── Color (ByPolygonVertex IndexToDirect, sequential) ─────────────────
        if vsout_color and COLOR and vsin_raw.get(COLOR):
            col_verts = vsin_raw[COLOR]
            cols = []
            for fc_i in range(n_fc):
                vi = vsin_nidxs[fc_i]
                c  = _safe_vert(col_verts, vi, [1.0, 1.0, 1.0, 1.0])
                cols.extend(str(x) for x in c[:4])
            col_idx = ",".join(str(i) for i in range(n_fc))
            layer_col = """
                LayerElementColor: 0 {
                    Version: 101
                    Name: "colorSet1"
                    MappingInformationType: "ByPolygonVertex"
                    ReferenceInformationType: "IndexToDirect"
                    Colors: *%(n)s {
                        a: %(v)s
                    }
                    ColorIndex: *%(in_num)s {
                        a: %(idx)s
                    }
                }
            """ % {"n": len(cols), "v": ",".join(cols),
                   "in_num": n_fc,  "idx": col_idx}
            layer_col_ins = """
                LayerElement: {
                    Type: "LayerElementColor"
                    TypedIndex: 0
                }
            """
            info_list.append("color=%s" % COLOR)

        # ── Diagnostic: which FBX layers were actually written ────────────────
        info_list.append("layers: UV=%s UV2=%s Nrm=%s Tan=%s BN=%s Col=%s" % (
            "Y" if layer_uv  else "N",
            "Y" if layer_uv2 else "N",
            "Y" if layer_nrm else "N",
            "Y" if layer_tan else "N",
            "Y" if layer_bn  else "N",
            "Y" if layer_col else "N",
        ))

        # ── Bone weight / SkinDeformer (optional) ────────────────────────────
        _skin_objs = ""
        _skin_cons = ""
        if mapper.get("EXPORT_SKIN", False):
            try:
                # Read VS Input vertex buffer — bone data lives here, NOT in
                # VS Output.  Use the same GetPostVSData(VSIn) path as
                # _read_vsin_attrs_from_gpu so we don't rely on GetVertexBuffers().
                _fmt_in  = controller.GetPostVSData(0, 0, rd.MeshDataStage.VSIn)
                _vb_res  = _fmt_in.vertexResourceId
                _vb_str  = _fmt_in.vertexByteStride
                _vb_off  = getattr(_fmt_in, 'vertexByteOffset', 0)
                _vb_data = bytes(controller.GetBufferData(_vb_res, _vb_off, 0))
                _nv_total = len(vertices) // 3

                # Estimate nat_cum from the known VS Input attribute layout.
                # Walk GetVertexInputs() and sum component sizes to find where
                # the "named" attributes end and extra data (bones) begins.
                _nat = 0
                try:
                    _va_list = controller.GetPipelineState().GetVertexInputs()
                    for _va in _va_list:
                        _bpc = getattr(_va.format, 'compByteWidth', 4)
                        _nc  = getattr(_va.format, 'compCount',     4)
                        _nat += _bpc * _nc
                except Exception:
                    _nat = 20   # Position(12) + Tangent(4) + Normal(4) fallback

                info_list.append("skin: vb_stride=%d nat=%d nv=%d" % (
                    _vb_str, _nat, _nv_total))

                _bw, _bi = scan_bones(_vb_data, _vb_str,
                                           _nv_total, _nat, info_list)
                if _bw:
                    _skin_objs, _skin_cons = build_skin_block(_bw, _bi, _nv_total)
                    _nb = max(max(idxs) for idxs in _bi) + 1
                    info_list.append("skin: OK — %d bones, %d verts" % (_nb, _nv_total))
                else:
                    info_list.append("skin: bone data not found (check stride/nat)")
            except Exception as _se:
                import traceback as _tb
                info_list.append("skin ERROR: %s" % _tb.format_exc().split('\n')[-2])

        _mat_objs, _mat_cons = build_material_block(
            os.path.dirname(save_path), save_name)
        ARGS = {
            "model_name":                save_name,
            "vertices":                  ",".join(str(v) for v in vertices),
            "vertices_num":              len(vertices),
            "polygons":                  ",".join(str(p) for p in polygons),
            "polygons_num":              len(polygons),
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
            "FbxSkinObjects":            _skin_objs,
            "FbxSkinConnections":        _skin_cons,
        }
        fbx = FBX_TEMPLATE % ARGS
        with open(save_path, "w") as f:
            f.write(dedent(fbx).strip())

    except Exception:
        import traceback
        err_list.append(traceback.format_exc())


