# -*- coding: utf-8 -*-
"""Bone/skin data scanning and FBX SkinDeformer block builder."""

import struct


def scan_bones(vb_data, stride, nv, nat_cum, info_list=None):
    """Scan the 'extra' region of a vertex buffer for bone weight/index data.

    Looks for two adjacent regions:
      • BoneWeights: N×float or N×uint8  all in [0,1], sum ≈ 1.0
      • BoneIndices: N×uint8 or N×uint16 all small integers (0-255)

    Returns (weights_list, indices_list) where each list has *nv* entries of
    length N (N = 4 or 8), or (None, None) if nothing convincing is found.
    """
    if nat_cum >= stride or nv < 5:
        return None, None

    IDENTITY_4x4 = [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]

    # Try n_bones=4 first, then 8 (common in UE4/UE5)
    for n_bones in (4, 8):
        # Strategy A: float weights (4B each) + byte indices (1B each)
        for wfmt, wbpc in [("f", 4), ("B", 1)]:  # try float32 then uint8 weights
            for ifmt, ibpc in [("B", 1), ("H", 2)]:  # byte then uint16 indices
                _w_sz = n_bones * wbpc
                _i_sz = n_bones * ibpc
                # Try every 4-byte-aligned offset in the extra region
                for _woff in range(nat_cum, stride - _w_sz - _i_sz + 1, 4):
                    _ioff = _woff + _w_sz
                    if _ioff + _i_sz > stride:
                        continue
                    # Sample first min(20, nv) vertices
                    _wsamples = []
                    _isamples = []
                    ok = True
                    for _vi in range(min(20, nv)):
                        _wb = _vi * stride + _woff
                        _ib = _vi * stride + _ioff
                        if _wb + _w_sz > len(vb_data) or _ib + _i_sz > len(vb_data):
                            ok = False; break
                        _w = list(struct.unpack_from("<%d%s" % (n_bones, wfmt), vb_data, _wb))
                        _i = list(struct.unpack_from("<%d%s" % (n_bones, ifmt), vb_data, _ib))
                        if wfmt == "B": _w = [v/255.0 for v in _w]
                        # Validate: weights in [0,1], sum ≈ 1, indices are small ints
                        if any(w < -0.01 or w > 1.05 for w in _w): ok = False; break
                        if abs(sum(_w) - 1.0) > 0.15: ok = False; break
                        if any(idx > 512 for idx in _i): ok = False; break
                        _wsamples.append(_w)
                        _isamples.append(_i)
                    if not ok or len(_wsamples) < 5:
                        continue
                    # Passed all checks — read all vertices
                    all_w, all_i = [], []
                    for _vi in range(nv):
                        _wb = _vi * stride + _woff
                        _ib = _vi * stride + _ioff
                        if _wb + _w_sz > len(vb_data) or _ib + _i_sz > len(vb_data):
                            break
                        _w = list(struct.unpack_from("<%d%s" % (n_bones, wfmt), vb_data, _wb))
                        _i = list(struct.unpack_from("<%d%s" % (n_bones, ifmt), vb_data, _ib))
                        if wfmt == "B": _w = [v/255.0 for v in _w]
                        all_w.append(_w)
                        all_i.append(_i)
                    if len(all_w) >= nv * 0.9:
                        if info_list is not None:
                            info_list.append(
                                "bone_scan: %d bones woff=%d wfmt=%s ioff=%d ifmt=%s "
                                "(%d verts)" % (n_bones, _woff, wfmt, _ioff, ifmt, len(all_w)))
                        return all_w, all_i
    return None, None


def build_skin_block(weights_list, indices_list, n_verts, geom_id=2035541511296):
    """Build FBX ASCII SkinDeformer + Cluster nodes from bone data.

    *weights_list*  – list of per-vertex weight arrays  (len == n_verts)
    *indices_list*  – list of per-vertex bone-index arrays
    *n_verts*       – total vertex count (needed for identity transforms)
    *geom_id*       – the Geometry node ID to attach the deformer to

    Returns (objects_str, connections_str) to embed in the FBX file.
    """
    if not weights_list or not indices_list:
        return "", ""

    n_bones_per_vert = len(weights_list[0])

    # Discover unique bone indices
    all_bone_ids = set()
    for idxs in indices_list:
        all_bone_ids.update(idxs)
    all_bone_ids = sorted(all_bone_ids)

    # Build per-bone influence lists
    bone_verts   = {b: [] for b in all_bone_ids}
    bone_weights = {b: [] for b in all_bone_ids}
    for vi, (ws, idxs) in enumerate(zip(weights_list, indices_list)):
        for w, bi in zip(ws, idxs):
            if w > 0.001:
                bone_verts[bi].append(vi)
                bone_weights[bi].append(w)

    SKIN_ID = 4000000000001
    _objs = ""
    _cons = ""

    # Skin deformer node
    _objs += """
        Deformer: %d, "Deformer::", "Skin" {
            Version: 101
            Link_DeformAcuracy: 50
        }""" % SKIN_ID
    _cons += "\n        C: \"OO\",%d,%d\n" % (SKIN_ID, geom_id)

    # Identity transform (4×4 row-major, but FBX wants column-major 16 floats)
    _identity = ",".join(["1" if i % 5 == 0 else "0" for i in range(16)])

    for bone_idx in all_bone_ids:
        _cluster_id = SKIN_ID + bone_idx + 1
        _bverts  = bone_verts[bone_idx]
        _bwgts   = bone_weights[bone_idx]
        if not _bverts:
            continue
        _vi_str = ",".join(str(v) for v in _bverts)
        _wg_str = ",".join("%.6f" % w for w in _bwgts)

        # Joint node (placeholder — RenderDoc has no bone hierarchy)
        _joint_id = _cluster_id + 100000
        _objs += """
        Model: %d, "Model::Joint_%d", "LimbNode" {
            Version: 232
            Properties70: {
                P: "RotationActive",  "bool", "", "",1
                P: "InheritType",     "enum", "", "",1
            }
        }""" % (_joint_id, bone_idx)

        _objs += """
        Deformer: %d, "SubDeformer::", "Cluster" {
            Version: 100
            UserData: "", ""
            Indexes: *%d { a: %s }
            Weights: *%d { a: %s }
            Transform: *16 { a: %s }
            TransformLink: *16 { a: %s }
        }""" % (_cluster_id, len(_bverts), _vi_str,
                len(_bwgts), _wg_str, _identity, _identity)

        # Connect joint to model root, cluster to skin deformer, cluster to joint
        _cons += "        C: \"OO\",%d,0\n" % _joint_id
        _cons += "        C: \"OO\",%d,%d\n" % (_cluster_id, SKIN_ID)
        _cons += "        C: \"OO\",%d,%d\n" % (_joint_id, _cluster_id)

    return _objs, _cons
