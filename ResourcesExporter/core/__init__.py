# -*- coding: utf-8 -*-
"""Core mesh data processing and transformation utilities."""

from .mesh_io import read_table_mesh, scan_attributes
from .vertex_attr import apply_aliases
from .math_utils import fetch_view_matrix, rigid_inverse, mat4_transform

__all__ = [
    "read_table_mesh",
    "scan_attributes",
    "apply_aliases",
    "fetch_view_matrix",
    "rigid_inverse",
    "mat4_transform",
]
