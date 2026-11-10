"""Tests for the vendored sse2neon header.

`sse2neon.h` is third-party, but this fork patches it, and the patch exists for
one specific reason: NDK r10e ships GCC 4.9, whose `arm_neon.h` has no
`vreinterpretq_*_f64` intrinsics. Nothing else in the tree can catch that on a
non-ARM machine, so these tests check the shim structurally (every intrinsic the
header uses is covered) and semantically (it is a bit cast, not a conversion),
by compiling and running the shim against stand-in vector types.

The stand-ins are deliberately `vector_size(16)` so the sizes and the memcpy
behaviour match the real 16-byte NEON vectors.
"""

import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HEADER = ROOT / "common" / "sse2neon.h"
CXX = shutil.which("g++") or shutil.which("clang++")

# Short-hands the header defines itself in terms of the real intrinsics; they
# are not intrinsics and do not need a shim.
OWN_SHORTHANDS = re.compile(r"^vreinterpretq_(m128|m128d|m128i)_")

INTRINSIC = re.compile(r"\bvreinterpretq_(f64_[a-z0-9]+|[a-z0-9]+_f64)\b")


def header_text():
    return HEADER.read_text(encoding="utf-8", errors="replace")


def used_f64_intrinsics(text):
    names = set()
    for match in INTRINSIC.finditer(text):
        name = "vreinterpretq_" + match.group(1)
        if not OWN_SHORTHANDS.match(name):
            names.add(name)
    return names


def shimmed_intrinsics(text):
    """Names the shim defines. Run it on shim_block(), not the whole header:
    the header also defines vreinterpretq_m128d_* in terms of these."""
    return set(re.findall(r"#define\s+(vreinterpretq_(?:f64_[a-z0-9]+|[a-z0-9]+_f64))\s*\(", text))


def shim_block(text):
    start = text.index("#define SSE2NEON_DEFINE_REINTERPRET")
    end = text.index("#endif /* __aarch64__ */", start)
    return text[start:end]


class ShimCoverageTests(unittest.TestCase):
    def test_every_f64_reinterpret_used_is_shimmed(self):
        text = header_text()
        used = used_f64_intrinsics(text)
        self.assertTrue(used, "no f64 reinterprets found - did the header change?")
        missing = sorted(used - shimmed_intrinsics(text))
        self.assertEqual(missing, [], f"no shim for: {missing}")

    def test_shim_covers_only_intrinsics(self):
        for name in shimmed_intrinsics(shim_block(header_text())):
            self.assertFalse(OWN_SHORTHANDS.match(name), f"{name} is an sse2neon shorthand")

    def test_shim_is_aarch64_only(self):
        text = header_text()
        self.assertIn("#if defined(__aarch64__)", text[: text.index("#endif /* __aarch64__ */")])

    def test_undef_precedes_each_define(self):
        """A real intrinsic would otherwise be a redefinition, and worse, the
        macro and the inline function would disagree about argument evaluation."""
        block = shim_block(header_text())
        for name in sorted(shimmed_intrinsics(block)):
            undef = block.index(f"#undef {name}")
            define = block.index(f"#define {name}(")
            self.assertLess(undef, define, f"{name}: #define before #undef")


@unittest.skipIf(CXX is None, "no C++ compiler")
class ShimBehaviourTests(unittest.TestCase):
    """Compiles the shim for the host with stand-in vector types and runs it."""

    PRELUDE = """\
    #include <cstdio>
    #include <cstring>

    typedef double float64x2_t __attribute__((vector_size(16)));
    typedef int int32x4_t __attribute__((vector_size(16)));
    typedef long long int64x2_t __attribute__((vector_size(16)));
    typedef unsigned long long uint64x2_t __attribute__((vector_size(16)));
    typedef unsigned int uint32x4_t __attribute__((vector_size(16)));
    typedef float float32x4_t __attribute__((vector_size(16)));

    """

    def build_and_run(self, body):
        source = self.PRELUDE + shim_block(header_text()).replace(
            "#if defined(__aarch64__)", ""
        ).rsplit("#endif /* __aarch64__ */", 1)[0] + textwrap.dedent(body)
        with tempfile.TemporaryDirectory() as tmp:
            cpp = Path(tmp) / "shim.cpp"
            exe = Path(tmp) / "shim"
            cpp.write_text(source, encoding="utf-8")
            compile_ = subprocess.run(
                [CXX, "-std=c++11", "-O2", "-Wall", "-Wextra", "-Werror", str(cpp), "-o", str(exe)],
                capture_output=True, text=True,
            )
            self.assertEqual(compile_.returncode, 0, compile_.stderr)
            run = subprocess.run([str(exe)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            return run.stdout

    def test_all_vectors_are_the_same_width(self):
        self.build_and_run("""
        int main() {
            if (sizeof(float64x2_t) != 16 || sizeof(int64x2_t) != 16 ||
                sizeof(uint32x4_t) != 16 || sizeof(float32x4_t) != 16)
                return 1;
            return 0;
        }
        """)

    def test_round_trip_is_a_bit_cast(self):
        out = self.build_and_run("""
        int main() {
            float64x2_t value = {-0.0, 1.0 / 3.0};
            int64x2_t bits = vreinterpretq_s64_f64(value);
            float64x2_t back = vreinterpretq_f64_s64(bits);
            if (std::memcmp(&value, &back, sizeof value) != 0) {
                std::printf("round trip changed the bits\\n");
                return 1;
            }
            return 0;
        }
        """)
        self.assertEqual(out, "")

    def test_double_and_single_views_share_the_bits(self):
        self.build_and_run("""
        int main() {
            float64x2_t value = {1.0, 2.0};
            float32x4_t narrowed = vreinterpretq_f32_f64(value);
            float64x2_t wide = vreinterpretq_f64_f32(narrowed);
            if (std::memcmp(&value, &wide, sizeof value) != 0)
                return 1;
            uint32x4_t halves = vreinterpretq_u32_f64(value);
            if (std::memcmp(&value, &halves, sizeof value) != 0)
                return 1;
            return 0;
        }
        """)

    def test_sign_bit_survives_without_conversion(self):
        """0.0 vs -0.0 is the cheap proof this is not a value conversion."""
        self.build_and_run("""
        int main() {
            float64x2_t negative_zero = {-0.0, -0.0};
            uint64x2_t bits = vreinterpretq_u64_f64(negative_zero);
            if (bits[0] != 0x8000000000000000ULL || bits[1] != 0x8000000000000000ULL)
                return 1;
            return 0;
        }
        """)


if __name__ == "__main__":
    unittest.main()
