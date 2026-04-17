#!/usr/bin/env bash
# Bump the patch segment of `mailctl`'s version in both `pyproject.toml`
# and `src/mailctl/__init__.py`, then stage both files.
#
# Usage:
#   ./scripts/bump-patch.sh         # 0.1.0 -> 0.1.1
#   ./scripts/bump-patch.sh --minor # 0.1.1 -> 0.2.0 (and resets patch)
#   ./scripts/bump-patch.sh --major # 0.2.0 -> 1.0.0 (and resets minor/patch)
#
# Design note: two files carry the version instead of one so that
# `import mailctl; mailctl.__version__` works without pulling in
# `importlib.metadata`. Keep them in lockstep — this script is the
# canonical way to do that.

set -euo pipefail

cd "$(dirname "$0")/.."

MODE="${1:---patch}"

python3 - "$MODE" <<'PY'
import re
import sys
from pathlib import Path

mode = sys.argv[1]
if mode not in {"--patch", "--minor", "--major"}:
    print(f"unknown mode: {mode}", file=sys.stderr)
    sys.exit(2)

pyproject = Path("pyproject.toml")
init_py = Path("src/mailctl/__init__.py")

py_text = pyproject.read_text()
m = re.search(r'^version\s*=\s*"(\d+)\.(\d+)\.(\d+)"\s*$', py_text, re.MULTILINE)
if not m:
    print("couldn't find version line in pyproject.toml", file=sys.stderr)
    sys.exit(1)

major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
old = f"{major}.{minor}.{patch}"

if mode == "--patch":
    patch += 1
elif mode == "--minor":
    minor += 1
    patch = 0
elif mode == "--major":
    major += 1
    minor = 0
    patch = 0

new = f"{major}.{minor}.{patch}"

# pyproject.toml
pyproject.write_text(
    re.sub(
        r'^(version\s*=\s*)"\d+\.\d+\.\d+"',
        rf'\g<1>"{new}"',
        py_text,
        count=1,
        flags=re.MULTILINE,
    )
)

# src/mailctl/__init__.py
init_text = init_py.read_text()
init_py.write_text(
    re.sub(
        r'^(__version__\s*=\s*)"\d+\.\d+\.\d+"',
        rf'\g<1>"{new}"',
        init_text,
        count=1,
        flags=re.MULTILINE,
    )
)

print(f"{old} -> {new}")
PY

git add pyproject.toml src/mailctl/__init__.py
