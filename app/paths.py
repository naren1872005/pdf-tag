# GUI_V1/app/paths.py
"""
Where TagPilot keeps its working data.

Installed (built exe, e.g. under C:\\Program Files\\TagPilot): the install folder
is read-only for normal users, so nothing is written there.
  projects  -> <Documents>\\TagPilot\\workspace   (fallback %LOCALAPPDATA%\\TagPilot\\workspace)
  logs      -> see app.logging_setup (Logs next to the exe if writable, else %LOCALAPPDATA%)
  licence, keys cache, engine state -> per-user folders (unchanged)

From source (python app/main.py): projects stay in <project folder>\\workspace.
"""
import os
import sys


def _writable(d):
    try:
        os.makedirs(d, exist_ok=True)
        probe = os.path.join(d, ".write_test")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except Exception:
        return False


def documents_dir():
    try:
        from PySide6.QtCore import QStandardPaths
        d = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
        if d:
            return d
    except Exception:
        pass
    return os.path.join(os.path.expanduser("~"), "Documents")


def default_workspace_dir():
    if not getattr(sys, "frozen", False):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(root, "workspace")
    for d in (os.path.join(documents_dir(), "TagPilot", "workspace"),
              os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "TagPilot", "workspace")):
        if _writable(d):
            return d
    return os.path.join(os.path.expanduser("~"), "TagPilot", "workspace")
