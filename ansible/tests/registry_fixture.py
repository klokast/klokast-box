"""A subprocess fixture for legacy lifecycle tests; no production bypass flag."""
import os
from pathlib import Path


def legacy_client(directory):
    path = Path(directory) / "bin"
    path.mkdir(exist_ok=True)
    client = path / "platform-registry"
    client.write_text('''#!/usr/bin/env python3
import sys, json, yaml
if sys.argv[1] == "assert-writable":
    raise SystemExit(0)
assert sys.argv[1] == "read"
with open(sys.argv[sys.argv.index("--registry") + 1]) as stream:
    value = yaml.safe_load(stream)
print(json.dumps({"kind":"klokast.registry-read.v1", "registry":value}))
''')
    client.chmod(0o755)
    return str(path) + os.pathsep + os.environ.get("PATH", "")
