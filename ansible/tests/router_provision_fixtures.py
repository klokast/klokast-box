"""Load the first-router provisioning entry point without executing it."""
from importlib.machinery import SourceFileLoader
import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CLI = REPO / 'ansible/bin/provision-router'


def load_cli():
    loader = SourceFileLoader('router_template_cli_test', str(CLI))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module
