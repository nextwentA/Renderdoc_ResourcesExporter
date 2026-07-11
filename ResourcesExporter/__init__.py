# -*- coding: utf-8 -*-
"""
RenderDoc FBX/OBJ Mesh Exporter

A modular RenderDoc extension for exporting mesh geometry, textures, and shaders
from captured frames to FBX, OBJ, and other formats.
"""

from .pipeline import run_export, run_quick_export

__all__ = ["run_export", "run_quick_export"]


def register(version, pyrenderdoc):
    import qrenderdoc
    ext = pyrenderdoc.Extensions()
    ext.RegisterPanelMenu(
        qrenderdoc.PanelMenu.MeshPreview,
        ["Export Resource"],
        run_export,
    )
    ext.RegisterPanelMenu(
        qrenderdoc.PanelMenu.MeshPreview,
        ["Quick Export (last settings)"],
        run_quick_export,
    )


def unregister():
    pass
