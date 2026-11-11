#!/usr/bin/env python3
"""Compare mined retail interface lists against the interfaces this SDK declares.

Input is the output of ``l4d1ctl mine <module> --bucket interfaces --top 0``.
Output groups every name three ways, and can emit the markdown tables used in
docs/L4D1/INTERFACES.md.

    python3 scripts/interface-report.py client.txt server.txt
    python3 scripts/interface-report.py --markdown client.txt server.txt
    python3 scripts/interface-report.py --check-doc docs/L4D1/INTERFACES.md \\
        l4d1ctl/tests/fixtures/retail_client_interfaces.txt \\
        l4d1ctl/tests/fixtures/retail_server_interfaces.txt

Why the comparison is worth making: the SDK can tell you what an interface looks
like, but not which ones a particular shipped build asks for. That set is the
feature list for a port, and the names it does *not* contain are the work this
tree has never had.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from l4d1ctl.mine import _INTERFACE_RE, align_with_sdk  # noqa: E402


def read_names(path: Path):
    raw = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    raw = [line for line in raw if not line.startswith("#")]
    return ([n for n in raw if _INTERFACE_RE.match(n)],
            [n for n in raw if not _INTERFACE_RE.match(n)])


def describe(path: Path):
    names, rejected = read_names(path)
    return {"path": path, "names": names, "rejected": rejected, "alignment": align_with_sdk(names)}


def print_plain(reports):
    for report in reports:
        alignment = report["alignment"]
        print(f"=== {report['path'].name}: {len(report['names'])} interfaces "
              f"({len(report['rejected'])} rejected as non-interface) ===")
        print(f"  matches this SDK : {len(alignment.known)}")
        print(f"  version mismatch : {len(alignment.mismatched)}")
        for family, (name, versions) in sorted(alignment.mismatched.items()):
            declared = ", ".join(f"{family}{version:03d}" for version in versions)
            print(f"      {name:42} SDK declares {declared}")
        print(f"  not in this SDK  : {len(alignment.unknown)}")
        for name in alignment.unknown:
            print(f"      {name}")
        if report["rejected"]:
            print(f"  rejected         : {', '.join(report['rejected'])}")
        print()


def markdown(reports):
    lines = []
    for report in reports:
        alignment = report["alignment"]
        label = report["path"].stem
        lines.append(f"#### {label}: {len(report['names'])} interfaces, "
                     f"{len(alignment.known)} match, {len(alignment.mismatched)} mismatch, "
                     f"{len(alignment.unknown)} unknown")
        lines.append("")
        lines.append("| Interface the build asks for | This SDK declares |")
        lines.append("| --- | --- |")
        for family, (name, versions) in sorted(alignment.mismatched.items()):
            declared = ", ".join(f"`{family}{version:03d}`" for version in versions)
            lines.append(f"| `{name}` | {declared} |")
        lines.append("")
        lines.append("Not in this SDK at all: " +
                     (", ".join(f"`{name}`" for name in alignment.unknown) or "none"))
        lines.append("")
    return "\n".join(lines)


def check_doc(doc: Path, reports):
    """The markdown in the doc must not drift from the fixtures."""
    text = doc.read_text(encoding="utf-8")
    missing = []
    for report in reports:
        for name in report["names"]:
            if f"`{name}`" not in text:
                missing.append(name)
    return missing


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="+", type=Path, help="mined --bucket interfaces dumps")
    parser.add_argument("--markdown", action="store_true", help="emit markdown tables")
    parser.add_argument("--check-doc", type=Path, help="fail if the doc omits any mined name")
    args = parser.parse_args(argv)

    reports = [describe(path) for path in args.files]

    if args.check_doc:
        missing = check_doc(args.check_doc, reports)
        if missing:
            print(f"error: {args.check_doc} does not mention {len(missing)} interface(s): "
                  f"{', '.join(missing[:8])}{' ...' if len(missing) > 8 else ''}", file=sys.stderr)
            return 1
        print(f"{args.check_doc} mentions all {sum(len(r['names']) for r in reports)} names")
        return 0

    print(markdown(reports) if args.markdown else print_plain(reports), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
