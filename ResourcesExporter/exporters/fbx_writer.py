# -*- coding: utf-8 -*-
"""FBX ASCII exporter."""

import os
import time
import inspect
from collections import defaultdict
from textwrap import dedent

from ..core.vertex_attr import apply_aliases
from .fbx_skin import scan_bones, build_skin_block


FBX_TEMPLATE = """
    ; FBX 7.3.0 project file
    ; ----------------------------------------------------

    ; Object definitions
    ;------------------------------------------------------------------

    Definitions:  {

        ObjectType: "Geometry" {
            Count: 1
            PropertyTemplate: "FbxMesh" {
                Properties70:  {
                    P: "Primary Visibility", "bool", "", "",1
                }
            }
        }

        ObjectType: "Model" {
            Count: 1
            PropertyTemplate: "FbxNode" {
                Properties70:  {
                    P: "Visibility", "Visibility", "", "A",1
                }
            }
        }

        ObjectType: "Deformer" {
            Count: 1
        }
    }

    ; Object properties
    ;------------------------------------------------------------------

    Objects:  {
        Geometry: 2035541511296, "Geometry::", "Mesh" {
            Vertices: *%(vertices_num)s {
                a: %(vertices)s
            }
            PolygonVertexIndex: *%(polygons_num)s {
                a: %(polygons)s
            }
            GeometryVersion: 124
            %(LayerElementNormal)s
            %(LayerElementBiNormal)s
            %(LayerElementTangent)s
            %(LayerElementColor)s
            %(LayerElementUV)s
            %(LayerElementUV2)s
            %(LayerElementUV3)s
            %(LayerElementUV4)s
            Layer: 0 {
                Version: 100
                %(LayerElementNormalInsert)s
                %(LayerElementBiNormalInsert)s
                %(LayerElementTangentInsert)s
                %(LayerElementColorInsert)s
                %(LayerElementUVInsert)s

            }
            Layer: 1 {
                Version: 100
                %(LayerElementUV2Insert)s
            }
            Layer: 2 {
                Version: 100
                %(LayerElementUV3Insert)s
            }
            Layer: 3 {
                Version: 100
                %(LayerElementUV4Insert)s
            }
        }
        Model: 2035615390896, "Model::%(model_name)s", "Mesh" {
            Properties70:  {
                P: "DefaultAttributeIndex", "int", "Integer", "",0
            }
        }
%(FbxMaterialObjects)s
%(FbxSkinObjects)s
    }

    ; Object connections
    ;------------------------------------------------------------------

    Connections:  {

        ;Model::pCube1, Model::RootNode
        C: "OO",2035615390896,0

        ;Geometry::, Model::pCube1
        C: "OO",2035541511296,2035615390896

%(FbxMaterialConnections)s
%(FbxSkinConnections)s
    }

    """



def build_material_block(save_dir, fbx_name):
    """Scan *save_dir* for images previously exported alongside the FBX and
    build FBX ASCII Material + Texture node strings (Objects section) and
    the corresponding Connection entries.

    Returns (material_objects_str, material_connections_str).  Both are empty
    strings when no suitable textures are found.
    """
    import re as _re

    # Collect image files in the same directory
    _img_exts = {".png", ".jpg", ".jpeg", ".tga", ".dds", ".bmp", ".hdr", ".exr"}
    try:
        _all_files = [
            f for f in os.listdir(save_dir)
            if os.path.splitext(f)[1].lower() in _img_exts
        ]
    except OSError:
        return "", ""

    if not _all_files:
        return "", ""

    # Classify textures by common suffix patterns
    _NORMAL_SUFFIXES  = _re.compile(r"_n(rm|ormal|ormal_map)?$", _re.IGNORECASE)
    _ROUGH_SUFFIXES   = _re.compile(r"_(rough|roughness|orm|pbr)$", _re.IGNORECASE)
    _METAL_SUFFIXES   = _re.compile(r"_(metal|metallic|m)$",        _re.IGNORECASE)
    _EMIT_SUFFIXES    = _re.compile(r"_(emit|emissive|e)$",          _re.IGNORECASE)

    _diffuse   = None   # first non-special image (probably diffuse / albedo)
    _normal_m  = None
    _rough_m   = None
    _emissive  = None

    for _f in sorted(_all_files):
        _stem = os.path.splitext(_f)[0]
        if _NORMAL_SUFFIXES.search(_stem):
            _normal_m = _normal_m or _f
        elif _ROUGH_SUFFIXES.search(_stem) or _METAL_SUFFIXES.search(_stem):
            _rough_m = _rough_m or _f
        elif _EMIT_SUFFIXES.search(_stem):
            _emissive = _emissive or _f
        else:
            _diffuse = _diffuse or _f

    # Build texture list: (channel_name, filename, prop_name)
    _TEX_SLOTS = []
    if _diffuse:   _TEX_SLOTS.append(("DiffuseColor", _diffuse,  "DiffuseColor"))
    if _normal_m:  _TEX_SLOTS.append(("NormalMap",    _normal_m, "NormalMap"))
    if _rough_m:   _TEX_SLOTS.append(("Roughness",    _rough_m,  "SpecularColor"))
    if _emissive:  _TEX_SLOTS.append(("Emissive",     _emissive, "EmissiveColor"))

    if not _TEX_SLOTS:
        return "", ""

    # Assign deterministic IDs (well beyond the Geometry/Model IDs above)
    _MAT_ID  = 3000000000001
    _OBJ_BLK = ""
    _CON_BLK = ""

    # Material node
    _OBJ_BLK += """
        Material: %d, "Material::%s_mat", "" {
            Version: 102
            ShadingModel: "phong"
            MultiLayer: 0
            Properties70:  {
                P: "AmbientColor",  "Color", "", "A",0.1,0.1,0.1
                P: "DiffuseColor",  "Color", "", "A",0.8,0.8,0.8
            }
        }""" % (_MAT_ID, fbx_name)

    _CON_BLK += "\n        ;Material, Model\n"
    _CON_BLK += "        C: \"OO\",%d,2035615390896\n" % _MAT_ID

    for _ci, (_chan, _fname, _prop) in enumerate(_TEX_SLOTS):
        _tex_id = _MAT_ID + _ci + 1
        _rel    = "./%s" % _fname
        _OBJ_BLK += """
        Texture: %d, "Texture::%s", "" {
            Type: "TextureVideoClip"
            Version: 202
            TextureName: "Texture::%s"
            Properties70:  {
                P: "CurrentTextureBlendMode", "enum", "", "",0
                P: "UVSet",                  "KString","","", "map1"
            }
            Media: "Video::%s"
            FileName: "%s"
            RelativeFilename: "%s"
            ModelUVTranslation: 0,0
            ModelUVScaling: 1,1
            Texture_Alpha_Source: "None"
            Cropping: 0,0,0,0
        }""" % (_tex_id, _chan, _chan, _chan, _fname, _rel)

        _CON_BLK += "        ;Texture::%s, Material::%s_mat\n" % (_chan, fbx_name)
        _CON_BLK += "        C: \"OP\",%d,%d, \"%s\"\n" % (_tex_id, _MAT_ID, _prop)

    return _OBJ_BLK, _CON_BLK




def write_fbx(save_path, mapper, data, attr_list, controller):
    """Write *data* to *save_path* in FBX ASCII format."""

    if not data:
        return

    save_name = os.path.basename(os.path.splitext(save_path)[0])

    # Qt model.data() may return strings; normalise to int so min/sort are numeric.
    idx_dict    = [int(v) for v in data["IDX"]]
    value_dict  = defaultdict(list)
    vertex_data = defaultdict(dict)

    for i, idx in enumerate(idx_dict):
        for attr in attr_list:
            value = data[attr][i]
            value_dict[attr].append(value)
            if idx not in vertex_data[attr]:
                vertex_data[attr][idx] = value

    ARGS = {
        "model_name":                save_name,
        "LayerElementNormal":        "",
        "LayerElementNormalInsert":  "",
        "LayerElementBiNormal":      "",
        "LayerElementBiNormalInsert":"",
        "LayerElementTangent":       "",
        "LayerElementTangentInsert": "",
        "LayerElementColor":         "",
        "LayerElementColorInsert":   "",
        "LayerElementUV":            "",
        "LayerElementUVInsert":      "",
        "LayerElementUV2":           "",
        "LayerElementUV2Insert":     "",
        "LayerElementUV3":           "",
        "LayerElementUV3Insert":     "",
        "LayerElementUV4":           "",
        "LayerElementUV4Insert":     "",
        "FbxMaterialObjects":        "",
        "FbxMaterialConnections":    "",
        "FbxSkinObjects":            "",
        "FbxSkinConnections":        "",
    }

    POSITION = mapper.get("POSITION")
    NORMAL   = mapper.get("NORMAL")
    BINORMAL = mapper.get("BINORMAL")
    TANGENT  = mapper.get("TANGENT")
    COLOR    = mapper.get("COLOR")
    UV       = mapper.get("UV")
    UV2      = mapper.get("UV2")
    UV3      = mapper.get("UV3")
    UV4      = mapper.get("UV4")
    ENGINE   = mapper.get("ENGINE")
    flip_u   = mapper.get("FLIP_U", False)
    flip_v   = mapper.get("FLIP_V", True)

    min_poly = min(idx_dict)
    idx_list = [idx - min_poly for idx in idx_dict]
    idx_len  = len(idx_list)

    def transform_unreal_vector(values):
        # Convert from left-handed (game engine) to right-handed (FBX) coordinate system
        # by flipping the X axis. This prevents the model from appearing mirrored.
        # Use safe indexing in case the attribute has fewer than 3 components.
        x = -values[0] if len(values) > 0 else 0.0
        y = values[1]  if len(values) > 1 else 0.0
        z = values[2]  if len(values) > 2 else 0.0
        return [x, y, z]

    def reorder_triangle_corners(values):
        # Flip winding order (swap corner 0 and corner 1 of each triangle)
        # to compensate for the X-axis flip applied in transform_unreal_vector.
        result = list(values)
        for i in range(0, len(result) - 2, 3):
            result[i], result[i + 1] = result[i + 1], result[i]
        return result

    class ProcessHandler(object):
        def run(self):
            curr = time.time()
            for name, func in inspect.getmembers(self, inspect.isroutine):
                if name.startswith("run_"):
                    func()
            print("elapsed time template: %s" % (time.time() - curr))

        def run_vertices(self):
            transformed = [
                transform_unreal_vector(values)
                for idx, values in sorted(vertex_data[POSITION].items())
            ]
            vertices = [str(v) for values in transformed for v in values]
            ARGS["vertices"]     = ",".join(vertices)
            ARGS["vertices_num"] = len(vertices)

        def run_polygons(self):
            polygon_indices = reorder_triangle_corners(idx_list)
            polygons = [
                str(idx ^ -1 if i % 3 == 2 else idx)
                for i, idx in enumerate(polygon_indices)
            ]
            ARGS["polygons"]     = ",".join(polygons)
            ARGS["polygons_num"] = len(polygons)

        def run_normals(self):
            if not vertex_data.get(NORMAL):
                return
            normal_values      = reorder_triangle_corners(value_dict[NORMAL])
            transformed_normals = [transform_unreal_vector(v) for v in normal_values]
            normals = [str(v) for values in transformed_normals for v in values]
            ARGS["LayerElementNormal"] = """
                LayerElementNormal: 0 {
                    Version: 101
                    Name: ""
                    MappingInformationType: "ByPolygonVertex"
                    ReferenceInformationType: "Direct"
                    Normals: *%(normals_num)s {
                        a: %(normals)s
                    }
                }
            """ % {"normals": ",".join(normals), "normals_num": len(normals)}
            ARGS["LayerElementNormalInsert"] = """
                LayerElement:  {
                        Type: "LayerElementNormal"
                    TypedIndex: 0
                }
            """

        def run_binormals(self):
            if not vertex_data.get(BINORMAL):
                return
            transformed = [transform_unreal_vector(v) for v in reorder_triangle_corners(value_dict[BINORMAL])]
            binormals = [str(v) for values in transformed for v in values]
            ARGS["LayerElementBiNormal"] = """
                LayerElementBinormal: 0 {
                    Version: 101
                    Name: "map1"
                    MappingInformationType: "ByPolygonVertex"
                    ReferenceInformationType: "Direct"
                    Binormals: *%(binormals_num)s {
                        a: %(binormals)s
                    }
                    BinormalsW: *%(binormalsW_num)s {
                        a: %(binormalsW)s
                    }
                }
            """ % {
                "binormals":      ",".join(binormals),
                "binormals_num":  len(binormals),
                "binormalsW":     ",".join(["1"] * (len(binormals) // 3)),
                "binormalsW_num": len(binormals) // 3,
            }
            ARGS["LayerElementBiNormalInsert"] = """
                LayerElement:  {
                        Type: "LayerElementBinormal"
                    TypedIndex: 0
                }
            """

        def run_tangents(self):
            if not vertex_data.get(TANGENT):
                return
            tangent_values = reorder_triangle_corners(value_dict[TANGENT])
            transformed    = [transform_unreal_vector(v) for v in tangent_values]
            tangents = [str(v) for values in transformed for v in values]
            ARGS["LayerElementTangent"] = """
                LayerElementTangent: 0 {
                    Version: 101
                    Name: "map1"
                    MappingInformationType: "ByPolygonVertex"
                    ReferenceInformationType: "Direct"
                    Tangents: *%(tangents_num)s {
                        a: %(tangents)s
                    }
                }
            """ % {"tangents": ",".join(tangents), "tangents_num": len(tangents)}
            ARGS["LayerElementTangentInsert"] = """
                    LayerElement:  {
                        Type: "LayerElementTangent"
                        TypedIndex: 0
                    }
            """

        def run_color(self):
            if not vertex_data.get(COLOR):
                return
            color_values = reorder_triangle_corners(value_dict[COLOR])
            colors = [
                str(v)
                for values in color_values
                for i, v in enumerate(values, 1)
            ]
            ARGS["LayerElementColor"] = """
                LayerElementColor: 0 {
                    Version: 101
                    Name: "colorSet1"
                    MappingInformationType: "ByPolygonVertex"
                    ReferenceInformationType: "IndexToDirect"
                    Colors: *%(colors_num)s {
                        a: %(colors)s
                    }
                    ColorIndex: *%(colors_indices_num)s {
                        a: %(colors_indices)s
                    }
                }
            """ % {
                "colors":             ",".join(colors),
                "colors_num":         len(colors),
                "colors_indices":     ",".join([str(i) for i in range(len(color_values))]),
                "colors_indices_num": idx_len,
            }
            ARGS["LayerElementColorInsert"] = """
                LayerElement:  {
                    Type: "LayerElementColor"
                    TypedIndex: 0
                }
            """

        def run_uv(self):
            if not value_dict.get(UV):
                return
            # UV is per-face-corner; must reorder to match the winding-order
            # swap applied to PolygonVertexIndex in run_polygons.
            uv_reordered = reorder_triangle_corners(value_dict[UV])
            uvs = [
                str((1 - v if flip_u else v) if i == 0 else (1 - v if flip_v else v))
                for values in uv_reordered
                for i, v in enumerate(values)
            ]
            ARGS["LayerElementUV"] = """
                LayerElementUV: 0 {
                    Version: 101
                    Name: "map1"
                    MappingInformationType: "ByPolygonVertex"
                    ReferenceInformationType: "Direct"
                    UV: *%(uvs_num)s {
                        a: %(uvs)s
                    }
                }
            """ % {
                "uvs":     ",".join(uvs),
                "uvs_num": len(uvs),
            }
            ARGS["LayerElementUVInsert"] = """
                LayerElement:  {
                    Type: "LayerElementUV"
                    TypedIndex: 0
                }
            """

        def run_uv2(self):
            if not value_dict.get(UV2):
                return
            uv_reordered = reorder_triangle_corners(value_dict[UV2])
            uvs = [
                str((1 - v if flip_u else v) if i == 0 else (1 - v if flip_v else v))
                for values in uv_reordered
                for i, v in enumerate(values)
            ]
            ARGS["LayerElementUV2"] = """
                LayerElementUV: 1 {
                    Version: 101
                    Name: "map2"
                    MappingInformationType: "ByPolygonVertex"
                    ReferenceInformationType: "Direct"
                    UV: *%(uvs_num)s {
                        a: %(uvs)s
                    }
                }
            """ % {
                "uvs":     ",".join(uvs),
                "uvs_num": len(uvs),
            }
            ARGS["LayerElementUV2Insert"] = """
                LayerElement:  {
                    Type: "LayerElementUV"
                    TypedIndex: 1
                }
            """

        def run_uv3(self):
            if not value_dict.get(UV3):
                return
            uv_reordered = reorder_triangle_corners(value_dict[UV3])
            uvs = [
                str((1 - v if flip_u else v) if i == 0 else (1 - v if flip_v else v))
                for values in uv_reordered
                for i, v in enumerate(values)
            ]
            ARGS["LayerElementUV3"] = """
                LayerElementUV: 2 {
                    Version: 101
                    Name: "map3"
                    MappingInformationType: "ByPolygonVertex"
                    ReferenceInformationType: "Direct"
                    UV: *%(uvs_num)s {
                        a: %(uvs)s
                    }
                }
            """ % {
                "uvs":     ",".join(uvs),
                "uvs_num": len(uvs),
            }
            ARGS["LayerElementUV3Insert"] = """
                LayerElement:  {
                    Type: "LayerElementUV"
                    TypedIndex: 2
                }
            """

        def run_uv4(self):
            if not value_dict.get(UV4):
                return
            uv_reordered = reorder_triangle_corners(value_dict[UV4])
            uvs = [
                str((1 - v if flip_u else v) if i == 0 else (1 - v if flip_v else v))
                for values in uv_reordered
                for i, v in enumerate(values)
            ]
            ARGS["LayerElementUV4"] = """
                LayerElementUV: 3 {
                    Version: 101
                    Name: "map4"
                    MappingInformationType: "ByPolygonVertex"
                    ReferenceInformationType: "Direct"
                    UV: *%(uvs_num)s {
                        a: %(uvs)s
                    }
                }
            """ % {
                "uvs":     ",".join(uvs),
                "uvs_num": len(uvs),
            }
            ARGS["LayerElementUV4Insert"] = """
                LayerElement:  {
                    Type: "LayerElementUV"
                    TypedIndex: 3
                }
            """

    handler = ProcessHandler()
    handler.run()

    save_dir = os.path.dirname(save_path)
    _mat_objs, _mat_cons = build_material_block(save_dir, save_name)
    ARGS["FbxMaterialObjects"]     = _mat_objs
    ARGS["FbxMaterialConnections"] = _mat_cons

    fbx = FBX_TEMPLATE % ARGS

    with open(save_path, "w") as f:
        f.write(dedent(fbx).strip())


