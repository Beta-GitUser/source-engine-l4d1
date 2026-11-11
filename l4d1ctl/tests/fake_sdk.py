"""A stand-in Android SDK, so the packaging script can be tested without one.

The real tools (aapt, d8, zipalign, apksigner) are not this repository's code.
What *is* this repository's code is the script that drives them: which
build-tools it picks, which JDK it runs them with, where it stages classes.dex,
what it puts inside the archive, and whether it checks the result. So the tools
are replaced with the smallest thing that behaves like them, and the script runs
for real.

This exists because the first APK here signed correctly and refused to install,
and the only place that could have been caught was a check that ran nowhere.
"""

import os
from pathlib import Path

AAPT = """#!/usr/bin/env python3
import sys, zipfile
a = sys.argv[1:]
out, man = a[a.index("-F") + 1], a[a.index("-M") + 1]
with zipfile.ZipFile(out, "w") as z:
    z.writestr("AndroidManifest.xml", open(man, "rb").read()[:20])
"""

AAPT2 = """#!/usr/bin/env python3
import sys, zipfile
a = sys.argv[1:]
out, man = a[a.index("-o") + 1], a[a.index("--manifest") + 1]
with zipfile.ZipFile(out, "w") as z:
    z.writestr("AndroidManifest.xml", open(man, "rb").read()[:20])
"""

D8 = """#!/usr/bin/env python3
import sys, os
a = sys.argv[1:]
out = a[a.index("--output") + 1]
open(os.path.join(out, "classes.dex"), "wb").write(b"dex")
"""

ZIPALIGN = """#!/bin/sh
prev=""; last=""
for a in "$@"; do prev=$last; last=$a; done
cp "$prev" "$last"
"""

APKSIGNER = """#!/bin/sh
if [ "$1" = "sign" ]; then
  while [ "$#" -gt 0 ]; do
    if [ "$1" = "--out" ]; then cp "$3" "$2"; exit 0; fi
    shift
  done
  exit 1
fi
[ -f "$2" ]
"""

JAVAC = """#!/bin/sh
while [ "$#" -gt 0 ]; do
  if [ "$1" = "-d" ]; then
    mkdir -p "$2/com/valvesoftware"
    : > "$2/com/valvesoftware/ValveActivity2.class"
    shift
  fi
  shift
done
"""

TOOLS = {
    "aapt": AAPT,
    "aapt2": AAPT2,
    "d8": D8,
    "zipalign": ZIPALIGN,
    "apksigner": APKSIGNER,
}


def write_executable(path, text):
    path.write_text(text)
    os.chmod(path, 0o755)


def make_sdk(root, build_tools):
    """build_tools: {"34.0.0": ["aapt", "d8", ...], "37.0.0": ["aapt2", ...]}

    Returns (sdk_dir, bin_dir) - bin_dir holds the fake javac, sdk_dir is what
    ANDROID_HOME should point at.
    """
    root = Path(root)
    sdk = root / "sdk"
    platform = sdk / "platforms" / "android-33"
    platform.mkdir(parents=True, exist_ok=True)
    (platform / "android.jar").write_bytes(b"jar")

    for version, tools in build_tools.items():
        directory = sdk / "build-tools" / version
        directory.mkdir(parents=True, exist_ok=True)
        for tool in tools:
            write_executable(directory / tool, TOOLS[tool])

    binaries = root / "bin"
    binaries.mkdir(exist_ok=True)
    write_executable(binaries / "javac", JAVAC)
    return sdk, binaries


def make_libraries(directory, names=("client", "server", "launcher")):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        (directory / f"lib{name}.so").write_bytes(b"\x7fELF fake")
    return directory
