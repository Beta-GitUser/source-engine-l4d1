#!/bin/sh
# Runs every test that does not need the engine build: the Python launcher
# tooling and the dependency-free C++ launcher core.
set -e

cd "$(dirname "$0")/.."

echo "== l4d1ctl (python) =="
python3 -m unittest discover -s l4d1ctl/tests -t .

echo "== launcher portcore (c++) =="
scripts/test-portcore.sh
