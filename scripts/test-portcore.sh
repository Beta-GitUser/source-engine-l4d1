#!/bin/sh
# Build and run the dependency-free launcher-core tests.
# Deliberately does not touch waf or the engine: this must work on a bare
# workstation and in CI before submodules are initialised.
set -e

cd "$(dirname "$0")/.."

CXX="${CXX:-c++}"
OUT="${TMPDIR:-/tmp}/portcore_tests"

"$CXX" -std=c++17 -O1 -Wall -Wextra -Werror \
	-o "$OUT" \
	launcher/portcore/portcfg.cpp \
	launcher/portcore/tests/test_portcfg.cpp

"$OUT"
