"""Portability lint for the C++ the Android build compiles.

Run 42989032324 got the engine past the last NEON problem and then failed in our
own code:

    portcfg.cpp:314: 'stoi' is not a member of 'std'
    portcfg.cpp:446: 'snprintf' is not a member of 'std'

Both compile fine on the host with GCC 12, because newer libstdc++ pulls
`<string>` in transitively and puts `snprintf` in namespace std. The Android
toolchain (GCC 4.9 with bionic) does neither. A local build therefore cannot
catch it, which is exactly the kind of failure worth a static check.

Two rules, both learned from that run:

1. A C++ standard-library function must come from a header that is included in
   the file that uses it.
2. C library functions are called unqualified (`snprintf`, not `std::snprintf`).
   `<cstdio>` is allowed not to provide the `std::` spelling on old bionic.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# function -> header that declares it
STD_SYMBOLS = {
    "stoi": "<string>",
    "stol": "<string>",
    "stoll": "<string>",
    "stoul": "<string>",
    "stof": "<string>",
    "stod": "<string>",
    "to_string": "<string>",
    "vector": "<vector>",
    "sort": "<algorithm>",
    "find": "<algorithm>",
    "unique_ptr": "<memory>",
    "make_unique": "<memory>",
    "shared_ptr": "<memory>",
    "ostringstream": "<sstream>",
    "istringstream": "<sstream>",
    "ifstream": "<fstream>",
    "ofstream": "<fstream>",
    "string": "<string>",
    "isdigit": "<cctype>",
    "isspace": "<cctype>",
    "tolower": "<cctype>",
    "memcpy": "<cstring>",
    "strlen": "<cstring>",
    "strcmp": "<cstring>",
    "snprintf": "<cstdio>",
    "printf": "<cstdio>",
    "strtoul": "<cstdlib>",
    "atoi": "<cstdlib>",
}

# calling these through std:: is what broke on GCC 4.9 + bionic
C_LIBRARY_NAMES = ("snprintf", "printf", "sprintf", "memcpy", "memset", "strlen",
                   "strcmp", "strcpy", "atoi", "atof", "strtol", "strtoul", "malloc",
                   "free", "qsort")

# C++11 library additions that the NDK r10e libstdc++ does not provide. The
# language standard comes from the toolchain, not from us, so the launcher code
# stays on facilities that exist in both worlds: strtol instead of stoi, a
# fixed buffer instead of to_string, and so on. This is not style - stoi being
# missing is what failed run 42993727389 after the header fix.
BANNED_STD_SYMBOLS = {
    "stoi": "use strtol (see Config::GetInt)",
    "stol": "use strtol",
    "stoll": "use strtoll",
    "stoul": "use strtoul",
    "stof": "use strtod",
    "stod": "use strtod",
    "to_string": "use snprintf into a buffer",
    "unique_ptr": "use a raw pointer or a plain object",
    "make_unique": "use a raw pointer or a plain object",
    "shared_ptr": "use a raw pointer or a plain object",
    "thread": "not available in this toolchain's STL",
    "regex": "not available in this toolchain's STL",
}

SOURCE_GLOBS = ("launcher/**/*.cpp", "launcher/**/*.h")


def sources():
    found = []
    for pattern in SOURCE_GLOBS:
        found.extend(sorted(ROOT.glob(pattern)))
    return [path for path in found if path.is_file()]


def includes(text):
    """Header names as written, without brackets or quotes."""
    return {match.group(1) for match in
            re.finditer(r"^\s*#\s*include\s*[<\"]([^>\"]+)[>\"]", text, re.MULTILINE)}


def include_closure(path, depth=4):
    """Headers reachable from a file, following its own local includes.

    Including "portcfg.h", which includes <vector>, is a normal way to get
    std::vector; the lint should not demand a redundant include in every .cpp.
    System headers are leaves (we do not chase into the toolchain).
    """
    index = {}
    for candidate in ROOT.glob("launcher/**/*.h"):
        index[str(candidate.relative_to(ROOT / "launcher"))] = candidate
        index[candidate.name] = candidate

    seen = set()
    pending = [(path, depth)]
    while pending:
        current, remaining = pending.pop()
        try:
            text = current.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for name in includes(text):
            if name in seen:
                continue
            seen.add(name)
            if remaining and not name.endswith((".h", ".hpp")):
                continue
            for candidate in (current.parent / name, ROOT / name, index.get(name)):
                if candidate and candidate.is_file():
                    pending.append((candidate, remaining - 1))
                    break
    return seen


def std_symbols_used(text):
    """Qualified std:: uses, ignoring comments and string literals loosely."""
    stripped = re.sub(r"//[^\n]*", "", text)
    stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.DOTALL)
    found = []
    for match in re.finditer(r"\bstd::([A-Za-z_][A-Za-z0-9_]*)", stripped):
        found.append((match.group(1), stripped.count("\n", 0, match.start()) + 1))
    return found


def unqualified_calls_through_std(text):
    stripped = re.sub(r"//[^\n]*", "", text)
    stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.DOTALL)
    problems = []
    for name in C_LIBRARY_NAMES:
        for match in re.finditer(rf"\bstd::{name}\s*\(", stripped):
            problems.append((f"std::{name}", stripped.count("\n", 0, match.start()) + 1))
    return problems


class PortcoreHygieneTests(unittest.TestCase):
    def test_there_are_sources_to_check(self):
        self.assertTrue(sources(), "the glob found no C++ - the lint is vacuous")

    def test_std_functions_come_from_included_headers(self):
        problems = []
        for path in sources():
            text = path.read_text(encoding="utf-8", errors="replace")
            have = include_closure(path)
            for symbol, header in STD_SYMBOLS.items():
                if f"std::{symbol}" not in text:
                    continue
                if header.strip("<>\"") in have:
                    continue
                problems.append(f"{path.relative_to(ROOT)}: std::{symbol} needs {header}")
        self.assertEqual(sorted(set(problems)), [], "\n".join(sorted(set(problems))))

    def test_no_cxx11_only_library_facilities(self):
        problems = []
        for path in sources():
            text = path.read_text(encoding="utf-8", errors="replace")
            stripped = re.sub(r"//[^\n]*", "", text)
            for symbol, advice in BANNED_STD_SYMBOLS.items():
                for match in re.finditer(rf"\bstd::{symbol}\s*[<(:]", stripped):
                    line = stripped.count("\n", 0, match.start()) + 1
                    problems.append(f"{path.relative_to(ROOT)}:{line}: std::{symbol} "
                                    f"is not in the NDK's STL - {advice}")
        self.assertEqual(problems, [], "\n".join(problems))

    def test_c_library_functions_are_unqualified(self):
        problems = []
        for path in sources():
            text = path.read_text(encoding="utf-8", errors="replace")
            for name, line in unqualified_calls_through_std(text):
                problems.append(f"{path.relative_to(ROOT)}:{line}: use {name[5:]} unqualified")
        self.assertEqual(problems, [], "\n".join(problems))


class CheckerSanityTests(unittest.TestCase):
    """The lint must actually catch the two failures from the CI run."""

    def test_detects_stoi_without_string(self):
        text = '#include <cstdio>\nint f(){ return std::stoi("1"); }\n'
        self.assertIn(("stoi", 2), std_symbols_used(text))
        self.assertNotIn("string", includes(text))

    def test_detects_qualified_snprintf(self):
        text = "void f(char* b){ std::snprintf(b, 1, \"x\"); }\n"
        self.assertEqual(unqualified_calls_through_std(text),
                         [("std::snprintf", 1)])

    def test_detects_a_banned_symbol(self):
        text = 'int f(){ return std::stoi("1"); }\n'
        self.assertTrue(re.search(r"\bstd::stoi\s*[<(:]", text))

    def test_comments_and_included_symbols_are_fine(self):
        text = ("// std::stoi in a comment is not a use\n"
                "#include <string>\n#include <cstdio>\n"
                "int f(){ snprintf(nullptr, 0, \"x\"); return std::stoi(\"1\"); }\n")
        self.assertIn("string", includes(text))
        self.assertEqual(unqualified_calls_through_std(text), [])


if __name__ == "__main__":
    unittest.main()
