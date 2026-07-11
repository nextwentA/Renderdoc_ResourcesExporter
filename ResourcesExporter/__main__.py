# -*- coding: utf-8 -*-
"""
RenderDoc extension entry point.

This module registers the FBX/OBJ Mesh Exporter extension with RenderDoc
and provides menu items in the Mesh Preview panel.
"""

try:
    import qrenderdoc
except ImportError:
    qrenderdoc = None

from rd_exporter import run_export, run_quick_export


def register(version, pyrenderdoc):
    """Register the extension with RenderDoc."""
    print("Registering FBX/OBJ Mesh Exporter extension (refactored) for RenderDoc {}".format(version))
    ext = pyrenderdoc.Extensions()
    ext.RegisterPanelMenu(
        qrenderdoc.PanelMenu.MeshPreview,
        ["Export Mesh"],
        run_export,
    )
    ext.RegisterPanelMenu(
        qrenderdoc.PanelMenu.MeshPreview,
        ["Quick Export (last settings)"],
        run_quick_export,
    )


def unregister():
    """Unregister the extension."""
    print("Unregistering FBX/OBJ Mesh Exporter extension (refactored)")
