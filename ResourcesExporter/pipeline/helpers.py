# -*- coding: utf-8 -*-
import os
import traceback
from functools import wraps


def remove_empty_dir(path):
    """Remove *path* if it exists and contains no files (export produced nothing)."""
    try:
        if os.path.isdir(path) and not os.listdir(path):
            os.rmdir(path)
    except OSError:
        pass


def parse_event_ids(text):
    """Parse "100,200-210,300" → sorted unique list [100,200,201,...,210,300]."""
    result = []
    for part in text.replace(" ", "").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            result.extend(range(int(a), int(b) + 1))
        else:
            result.append(int(part))
    return sorted(set(result))


def error_handler(func):
    def wrapper(pyrenderdoc, data):
        manager = pyrenderdoc.Extensions()
        try:
            func(pyrenderdoc, data)
        except Exception:
            manager.MessageDialog("Export Failed\n%s" % traceback.format_exc(), "Error!")

    return wrapper
