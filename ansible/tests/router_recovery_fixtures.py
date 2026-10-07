"""Load retained router recovery guest helpers for isolated tests."""
import importlib.machinery
import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def module(name):
    loader = importlib.machinery.SourceFileLoader(name.replace('-', '_'),
        str(REPO / 'ansible/roles/router-state-copy/files' / name))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    result = importlib.util.module_from_spec(spec)
    loader.exec_module(result)
    return result
