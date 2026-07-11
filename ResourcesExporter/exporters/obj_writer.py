# -*- coding: utf-8 -*-
"""Wavefront OBJ exporter."""

import os
from collections import defaultdict


def write_obj(save_path, mapper, data, attr_list, controller):
    """Write *data* to *save_path* in Wavefront OBJ format.

    OBJ is widely supported by Blender, Maya, 3ds Max, Houdini and many other
    DCC tools without requiring the FBX SDK.  Vertex positions, UVs, normals
    and (as comments) vertex colors are written.
    """
    if not data:
        return

    save_name  = os.path.basename(os.path.splitext(save_path)[0])
    idx_dict   = [int(v) for v in data["IDX"]]
    value_dict = defaultdict(list)
    vertex_data = defaultdict(dict)

    for i, idx in enumerate(idx_dict):
        for attr in attr_list:
            value = data[attr][i]
            value_dict[attr].append(value)
            if idx not in vertex_data[attr]:
                vertex_data[attr][idx] = value

    POSITION = mapper.get("POSITION")
    NORMAL   = mapper.get("NORMAL")
    UV       = mapper.get("UV")
    COLOR    = mapper.get("COLOR")
    ENGINE   = mapper.get("ENGINE")
    flip_u   = mapper.get("FLIP_U", False)
    flip_v   = mapper.get("FLIP_V", True)

    min_poly = min(idx_dict)
    idx_list = [idx - min_poly for idx in idx_dict]

    def xform(values):
        """Apply engine-specific coordinate conversion."""
        if ENGINE != "unreal":
            return list(values[:3])
        x, y, z = values[:3]
        return [-x, z, -y]

    lines = [
        "# OBJ exported from RenderDoc by renderdoc2fbx",
        "# Mesh: %s" % save_name,
        "# Vertices: %d  Triangles: %d" % (
            len(set(idx_dict)) , len(idx_list) // 3),
        "",
    ]

    # ── Vertex positions (unique per vertex index) ───────────────────────────
    has_pos = POSITION and vertex_data.get(POSITION)
    if has_pos:
        for _, v in sorted(vertex_data[POSITION].items()):
            p = xform(v)
            lines.append("v %.6f %.6f %.6f" % (p[0], p[1], p[2]))
        lines.append("")

    # ── Texture coordinates (unique per vertex index, IndexToDirect) ─────────
    has_uv = UV and vertex_data.get(UV)
    if has_uv:
        for _, v in sorted(vertex_data[UV].items()):
            u  = (1.0 - v[0]) if flip_u else v[0]
            vv = (1.0 - v[1]) if flip_v else v[1]
            lines.append("vt %.6f %.6f" % (u, vv))
        lines.append("")

    # ── Normals (per polygon vertex — one entry per face corner) ─────────────
    has_normal = NORMAL and vertex_data.get(NORMAL)
    if has_normal:
        for nvals in value_dict[NORMAL]:
            n = xform(nvals)
            lines.append("vn %.6f %.6f %.6f" % (n[0], n[1], n[2]))
        lines.append("")

    # ── Vertex color as OBJ extension comments ────────────────────────────────
    has_color = COLOR and vertex_data.get(COLOR)
    if has_color:
        lines.append("# Vertex colors (r g b a per unique vertex):")
        for idx, cv in sorted(vertex_data[COLOR].items()):
            comps = " ".join("%.4f" % c for c in cv[:4])
            lines.append("# vc %d %s" % (idx - min_poly, comps))
        lines.append("")

    # ── Faces ─────────────────────────────────────────────────────────────────
    lines.append("g %s" % save_name)
    num_tris = len(idx_list) // 3
    for tri in range(num_tris):
        parts = []
        for corner in range(3):
            li = tri * 3 + corner          # linear poly-vert index (0-based)
            vi = idx_list[li] + 1          # OBJ position index (1-based)
            ni = li + 1                    # OBJ normal index  (1-based, per-poly-vert)
            if has_uv and has_normal:
                parts.append("%d/%d/%d" % (vi, vi, ni))
            elif has_uv:
                parts.append("%d/%d" % (vi, vi))
            elif has_normal:
                parts.append("%d//%d" % (vi, ni))
            else:
                parts.append(str(vi))
        lines.append("f " + " ".join(parts))

    with open(save_path, "w") as f:
        f.write("\n".join(lines))


