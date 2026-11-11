#!/bin/sh
# Regenerate the interface list embedded in l4d1ctl/mine.py from this tree's
# headers.
#
# The point of the list is the diff it enables: strings in the retail module
# that this SDK does not declare are L4D-only interfaces (work to do), and
# families whose version differs are the ones that make a module fail to load.
#
# Usage: sh scripts/gen-sdk-interfaces.sh [--check]
#   --check  exit 1 if mine.py is out of date (for CI / pre-commit)

set -e

ROOT=$(cd "$(dirname "$0")/.." && pwd)
TARGET="$ROOT/l4d1ctl/mine.py"
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT

# This tree names interface-version macros at least four ways:
#   VCLIENT_INTERFACE_VERSION        "VClient017"
#   INTERFACEVERSION_SERVERGAMEDLL   "ServerGameDLL005"
#   INTERFACEVERSION_ENGINEVGUI?     ...
#   VENGINE_GAMEUIFUNCS_VERSION      "VENGINE_GAMEUIFUNCS_VERSION005"
# so rather than enumerate conventions, take every define whose *value* has the
# shape of an interface string: a name ending in a three-digit version 0xx or in
# VersionNNN. Games of "which macro family" is a losing one, and each miss was
# invisible until a mined name turned up as "unknown".
grep -rhoE '#define[[:space:]]+[A-Za-z0-9_]+[[:space:]]+"[^"]+"' \
	--include='*.h' "$ROOT/public" "$ROOT/game" 2>/dev/null \
	| sed -E 's/.*"([^"]+)"/\1/' \
	| grep -E '^[A-Za-z][A-Za-z0-9_]*(0[0-9]{2}|Version[0-9]{3})$' \
	| sort -u > "$TMP"

count=$(wc -l < "$TMP")
if [ "$count" -lt 50 ]; then
	echo "error: only $count interface strings found - the extraction broke" >&2
	exit 1
fi

python3 - "$TARGET" "$TMP" "$( [ "$1" = "--check" ] && echo check )" <<'PY'
import sys
from pathlib import Path

target, listing, mode = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
names = [line.strip() for line in listing.read_text().splitlines() if line.strip()]

start = "# BEGIN GENERATED SDK INTERFACES"
end = "# END GENERATED SDK INTERFACES"
block = [start, "SDK_INTERFACES = frozenset({"]
for index in range(0, len(names), 3):
    chunk = ", ".join(f'"{name}"' for name in names[index:index + 3])
    block.append(f"    {chunk},")
block.append("})")
block.append(end)

text = target.read_text()
if start not in text or end not in text:
    sys.exit("error: generated block markers are missing from mine.py")

head, rest = text.split(start, 1)
_, tail = rest.split(end, 1)
updated = head + "\n".join(block) + tail

if mode == "check":
    if updated != text:
        sys.exit("error: l4d1ctl/mine.py is out of date - run scripts/gen-sdk-interfaces.sh")
    print(f"sdk interface list is current ({len(names)} entries)")
    raise SystemExit(0)

target.write_text(updated)
print(f"wrote {len(names)} interface versions into {target}")
PY
