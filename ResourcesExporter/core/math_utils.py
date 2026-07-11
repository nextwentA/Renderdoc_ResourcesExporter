# -*- coding: utf-8 -*-
"""Matrix and coordinate utilities for view-space reconstruction."""

import struct

try:
    import renderdoc as rd
except ImportError:
    rd = None


def fetch_view_matrix(controller):
    """Try to extract the View matrix from VS constant buffer 0.

    Scans the first 512 bytes of the first VS constant buffer for a 4x4
    float matrix whose upper-left 3x3 is approximately orthonormal.
    Returns a flat 16-element row-major list, or None on failure.
    """
    try:
        state  = controller.GetPipelineState()
        vs_cbs = state.GetConstantBuffers(rd.ShaderStage.Vertex)
        if not vs_cbs:
            return None
        cb0   = vs_cbs[0]
        cb_id = getattr(cb0, "resourceId", None)
        if not cb_id or cb_id == rd.ResourceId.Null():
            return None

        raw = bytes(controller.GetBufferData(cb_id, 0, 512))

        for off in range(0, len(raw) - 64, 4):
            m = list(struct.unpack_from("<16f", raw, off))

            def col(c):
                return [m[r * 4 + c] for r in range(3)]

            def dot(a, b):
                return sum(x * y for x, y in zip(a, b))

            def norm(v):
                return dot(v, v) ** 0.5

            c0, c1, c2 = col(0), col(1), col(2)
            n0, n1, n2 = norm(c0), norm(c1), norm(c2)
            if not (0.9 < n0 < 1.1 and 0.9 < n1 < 1.1 and 0.9 < n2 < 1.1):
                continue
            if abs(dot(c0, c1)) > 0.05 or abs(dot(c0, c2)) > 0.05 or abs(dot(c1, c2)) > 0.05:
                continue
            cross = [
                c0[1] * c1[2] - c0[2] * c1[1],
                c0[2] * c1[0] - c0[0] * c1[2],
                c0[0] * c1[1] - c0[1] * c1[0],
            ]
            if abs(abs(dot(cross, c2)) - 1.0) > 0.1:
                continue
            return m

    except Exception:
        pass
    return None


def rigid_inverse(m):
    """Invert a rigid-body 4x4 row-major matrix (rotation + translation only).

    For a View matrix V, returns V^{-1} (camera-to-world).
    Layout: row r, col c -> m[r*4+c].
    """
    r  = [[m[r2 * 4 + c2] for c2 in range(3)] for r2 in range(3)]
    rt = [[r[c2][r2] for c2 in range(3)] for r2 in range(3)]
    t  = [m[0 * 4 + 3], m[1 * 4 + 3], m[2 * 4 + 3]]
    nt = [-sum(rt[i][j] * t[j] for j in range(3)) for i in range(3)]
    return [
        rt[0][0], rt[0][1], rt[0][2], nt[0],
        rt[1][0], rt[1][1], rt[1][2], nt[1],
        rt[2][0], rt[2][1], rt[2][2], nt[2],
        0,        0,        0,        1,
    ]


def mat4_transform(m, x, y, z):
    """Transform point (x, y, z) by a 4x4 row-major matrix m (w=1)."""
    return (
        m[0]  * x + m[1]  * y + m[2]  * z + m[3],
        m[4]  * x + m[5]  * y + m[6]  * z + m[7],
        m[8]  * x + m[9]  * y + m[10] * z + m[11],
    )
