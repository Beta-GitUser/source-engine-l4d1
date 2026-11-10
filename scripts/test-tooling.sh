#!/bin/sh
# Runs every test that does not need the engine build: the Python launcher
# tooling and the dependency-free C++ launcher core.
#
# Usage: scripts/test-tooling.sh [all|python|cpp]
#
# Environment:
#   QUIET=true   only print the summary, not every test name
#   CXX=...      compiler for the C++ suite (default: c++)
set -e

cd "$(dirname "$0")/.."

SUITES="${1:-all}"
VERBOSE=""
if [ "${QUIET:-false}" != "true" ]; then
	VERBOSE="-v"
fi

run_python()
{
	echo "== l4d1ctl (python) =="
	# shellcheck disable=SC2086  # VERBOSE is intentionally word-split
	python3 -m unittest discover -s l4d1ctl/tests -t . $VERBOSE
}

run_cpp()
{
	echo "== launcher portcore (c++) =="
	scripts/test-portcore.sh
}

case "$SUITES" in
	all)
		run_python
		run_cpp
		;;
	python)
		run_python
		;;
	cpp)
		run_cpp
		;;
	*)
		echo "usage: $0 [all|python|cpp]" >&2
		exit 2
		;;
esac
