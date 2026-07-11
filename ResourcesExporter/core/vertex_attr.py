# -*- coding: utf-8 -*-
"""Vertex attribute alias mapping and per-corner data remapping."""


def apply_aliases(data, attr_list):
    """Build a complete alias web for vertex attribute names.

    Three alias layers are applied so any engine preset can find its data
    regardless of which name the mapper stores:

    1. Bidirectional _inputN <-> ATTRIBUTE{N}
    2. Forward semantic: add semantic name from location-based name
    3. Reverse semantic: add location-based name from semantic name
       (handles switching between Vulkan and D3D11 captures)
    """
    if data is None:
        return data, attr_list

    added = {}

    # 1. Bidirectional _inputN <-> ATTRIBUTE{N}
    for k in list(data.keys()):
        if k.startswith("_input"):
            try:
                n     = int(k[len("_input"):])
                alias = "ATTRIBUTE%d" % n
                if alias not in data:
                    added[alias] = data[k]
            except ValueError:
                pass
        elif k.startswith("ATTRIBUTE"):
            try:
                n     = int(k[len("ATTRIBUTE"):])
                alias = "_input%d" % n
                if alias not in data:
                    added[alias] = data[k]
            except ValueError:
                pass

    combined = dict(data)
    combined.update(added)

    # 2. Forward: location -> semantic
    _FWD = [
        ("POSITION",    ["_input0",  "ATTRIBUTE0"]),
        ("VERTEX",      ["_input0",  "ATTRIBUTE0"]),
        ("SV_Position", ["_input0",  "ATTRIBUTE0"]),
        ("NORMAL",      ["_input2",  "ATTRIBUTE2"]),
        ("TANGENT",     ["_input1",  "ATTRIBUTE1"]),
        ("BINORMAL",    ["_input3",  "ATTRIBUTE3"]),
        ("TEXCOORD0",   ["_input3",  "ATTRIBUTE3",  "_input5",  "ATTRIBUTE5"]),
        ("TEXCOORD1",   ["_input4",  "ATTRIBUTE4",  "_input6",  "ATTRIBUTE6"]),
        ("UV",          ["_input3",  "ATTRIBUTE3",  "_input5",  "ATTRIBUTE5"]),
        ("UV2",         ["_input4",  "ATTRIBUTE4",  "_input6",  "ATTRIBUTE6"]),
        ("COLOR",       ["_input13", "ATTRIBUTE13", "_input5",  "ATTRIBUTE5"]),
        ("COLOR0",      ["_input13", "ATTRIBUTE13", "_input5",  "ATTRIBUTE5"]),
    ]
    for sem, cands in _FWD:
        if sem in combined:
            continue
        for c in cands:
            if c in combined:
                added[sem]    = combined[c]
                combined[sem] = combined[c]
                break

    # 3. Reverse: semantic -> location
    _REV = [
        (["_input0",  "ATTRIBUTE0"],  ["POSITION", "VERTEX", "SV_Position"]),
        (["_input2",  "ATTRIBUTE2"],  ["NORMAL"]),
        (["_input1",  "ATTRIBUTE1"],  ["TANGENT"]),
        (["_input3",  "ATTRIBUTE3"],  ["BINORMAL"]),
        (["_input13", "ATTRIBUTE13"], ["COLOR", "COLOR0"]),
    ]
    for locs, sems in _REV:
        src = None
        for s in sems:
            if s in combined:
                src = combined[s]
                break
        if src is None:
            continue
        for loc in locs:
            if loc not in combined:
                added[loc]    = src
                combined[loc] = src

    if added:
        data.update(added)
        if attr_list is not None:
            attr_list = set(attr_list) | set(added.keys())
    return data, attr_list


def remap_vertex_data(attr_data, index_list):
    """Convert per-vertex attr_data dict to per-face-corner format.

    attr_data: {attr_name: {vertex_idx: value}}
    index_list: list of vertex indices (one per face corner)

    Returns (data dict, attr_list set) matching the format expected by
    write_fbx / write_obj.
    """
    from collections import defaultdict

    if not attr_data or not index_list:
        return None, None

    data      = defaultdict(list)
    attr_list = set(attr_data.keys())

    data["IDX"] = [str(idx) for idx in index_list]

    for attr, vmap in attr_data.items():
        vals = []
        for idx in index_list:
            v = vmap.get(idx)
            if v is None:
                v = next(iter(vmap.values())) if vmap else []
            vals.append(v)
        data[attr] = vals
        attr_list.add(attr)

    return data, attr_list
