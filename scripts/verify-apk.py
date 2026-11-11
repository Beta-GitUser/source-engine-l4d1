#!/usr/bin/env python3
"""Structural checks on a packaged APK, without the Android SDK.

`apksigner verify` answers a signature question, and a signature is valid whether
or not the package has any code in it. This answers the install question: would
the PackageManager accept this file? It exists because the first APK built here
passed `apksigner verify` and then refused to install - "package appears to be
invalid" - because classes.dex had been staged one directory too deep.

Checks, in the order the PackageManager would trip over them:

  * AndroidManifest.xml at the root (binary XML, so only existence is checked);
  * classes.dex at the root, where the dex loader looks;
  * at least one lib/<abi>/*.so for the requested ABI;
  * no entry that is absolute, starts with "./", or contains "..", and no
    duplicate names - zip is happy to store all three and the installer is not;
  * every entry decompresses (CRC), so a truncated upload cannot pass.

Usage:
  verify-apk.py APK [--abi arm64-v8a] [--expect-libs N]
"""

import argparse
import sys
import zipfile


def check(apk_path, abi, expect_libs=None):
    """Returns (problems, facts). Empty problems means it should install."""
    problems = []
    facts = {}

    if not zipfile.is_zipfile(apk_path):
        return ["not a zip file at all"], facts

    try:
        archive = zipfile.ZipFile(apk_path)
    except (zipfile.BadZipFile, OSError) as error:
        return [f"cannot open: {error}"], facts

    with archive:
        names = archive.namelist()
        facts["entries"] = len(names)

        if "AndroidManifest.xml" not in names:
            problems.append("no AndroidManifest.xml at the root")

        # The one that actually bit: dex loaders only look at the root.
        if "classes.dex" not in names:
            misplaced = [name for name in names if name.endswith("classes.dex")]
            if misplaced:
                problems.append(
                    f"classes.dex is at {misplaced[0]!r}, not at the root - the "
                    "installer will call the package invalid")
            else:
                problems.append("no classes.dex at the root - the package has no code")

        libraries = [name for name in names
                     if name.startswith(f"lib/{abi}/") and name.endswith(".so")]
        facts["libraries"] = len(libraries)
        if not libraries:
            problems.append(
                f"no lib/{abi}/*.so entries - the ABI is wrong or nothing was packaged")
        if expect_libs is not None and len(libraries) != expect_libs:
            problems.append(
                f"expected {expect_libs} libraries in lib/{abi}/, found {len(libraries)}")

        for name in names:
            if name.startswith(("/", "./")):
                problems.append(f"entry has a relative or absolute prefix: {name!r}")
                break
            if ".." in name.split("/"):
                problems.append(f"entry escapes the archive: {name!r}")
                break

        duplicates = len(names) - len(set(names))
        if duplicates:
            problems.append(f"{duplicates} duplicate entries")

        if archive.testzip() is not None:
            problems.append("an entry is corrupt (CRC mismatch)")

    return problems, facts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("apk")
    parser.add_argument("--abi", default="arm64-v8a")
    parser.add_argument("--expect-libs", type=int, default=None)
    parser.add_argument("--quiet", action="store_true")
    options = parser.parse_args(argv)

    problems, facts = check(options.apk, options.abi, options.expect_libs)

    if problems:
        print(f"{options.apk}: not installable", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    if not options.quiet:
        print(f"{options.apk}: {facts['entries']} entries, "
              f"{facts['libraries']} in lib/{options.abi}/, "
              f"classes.dex at the root")
    return 0


if __name__ == "__main__":
    sys.exit(main())
