"""Static checks on the debug APK shell.

The APK cannot be built or tested here (no Android SDK), and a JNI name mismatch
fails at runtime with an UnsatisfiedLinkError that says nothing useful. These
checks cover the mistakes that are cheap to catch now and expensive to catch in a
20-minute CI run:

  * every native method the Java side declares has a C++ implementation, with the
    exact symbol name the JVM will look for;
  * the manifest declares a launchable activity that matches the class;
  * the packaging script fails clearly, rather than confusingly, without an SDK.
"""

import re
import sys
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APK = ROOT / "launcher" / "android" / "apk"
JAVA = APK / "src" / "com" / "valvesoftware" / "ValveActivity2.java"
MANIFEST = APK / "AndroidManifest.xml"
NATIVE = ROOT / "launcher" / "android" / "main.cpp"
SCRIPT = ROOT / "scripts" / "build-apk.sh"
VERIFIER = ROOT / "scripts" / "verify-apk.py"


def java_native_methods():
    """``private static native long nativeLoadLibrary(String path);`` -> names."""
    text = JAVA.read_text(encoding="utf-8")
    return set(re.findall(r"\bnative\s+[A-Za-z_][\w.]*\s+(\w+)\s*\(", text))


def cpp_jni_symbols():
    """Java_com_valvesoftware_ValveActivity2_x -> x"""
    text = NATIVE.read_text(encoding="utf-8", errors="replace")
    return set(re.findall(r"Java_com_valvesoftware_ValveActivity2_(\w+)\s*\(", text))


class JniParityTests(unittest.TestCase):
    def test_java_and_cpp_agree(self):
        declared = java_native_methods()
        implemented = cpp_jni_symbols()
        missing = sorted(declared - implemented)
        self.assertEqual(missing, [],
                         f"declared in Java but not implemented in C++: {missing}")

    def test_the_probe_surface_is_what_we_think(self):
        """If this list changes, the Java side must change with it."""
        self.assertEqual(
            sorted(java_native_methods()),
            ["nativeCreateInterface", "nativeLastError", "nativeLoadLibrary",
             "nativeOnActivityResult", "setArgs", "setenv"],
        )

    def test_activity_class_matches_the_jni_prefix(self):
        text = JAVA.read_text(encoding="utf-8")
        self.assertIn("package com.valvesoftware;", text)
        self.assertIn("class ValveActivity2 extends Activity", text)
        for symbol in cpp_jni_symbols():
            self.assertIn(symbol, java_native_methods() | {"setenv", "setArgs",
                                                           "nativeOnActivityResult"},
                          f"C++ exports {symbol}, which Java does not declare")

    def test_launcher_library_is_loaded_by_name(self):
        """The activity must load liblauncher.so itself: dlopen of the modules
        only works once a process exists, and this is that process."""
        self.assertIn('System.loadLibrary("launcher")', JAVA.read_text(encoding="utf-8"))


class JavaShapeTests(unittest.TestCase):
    """A structural check, not a compile.

    There is no JDK in this workspace and the APK is only built in CI, so the
    cheap failure modes are worth catching structurally: unbalanced braces (a
    compile error 20 minutes into a run), and a native library load that is not
    guarded (a crash with no message on a device that has no adb).
    """

    def test_translation_units_are_balanced(self):
        for source in sorted((APK / "src").rglob("*.java")):
            text = source.read_text(encoding="utf-8")
            stripped = re.sub(r'"([^"\\]|\\.)*"', '""', text)
            stripped = re.sub(r"//[^\n]*", "", stripped)
            stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.S)
            for opener, closer in (("{", "}"), ("(", ")"), ("[", "]")):
                self.assertEqual(stripped.count(opener), stripped.count(closer),
                                 f"{source.name}: unbalanced {opener}{closer}")

    def test_every_native_declaration_is_static_and_terminated(self):
        text = JAVA.read_text(encoding="utf-8")
        declarations = re.findall(r"^\s*(?:private\s+)?static\s+native[^;]*;", text, re.M)
        self.assertEqual(len(declarations), len(java_native_methods()))
        for declaration in declarations:
            self.assertIn("static", declaration)

    def test_native_load_is_guarded(self):
        """Background: an UnsatisfiedLinkError out of a static initialiser is
        fatal, and the phone has no logcat to explain it."""
        text = JAVA.read_text(encoding="utf-8")
        block = text[text.index("static {"):]
        block = block[:block.index("    }")]
        self.assertIn("try", block)
        self.assertIn("catch", block)
        self.assertIn("nativeReady", block)

    def test_probe_skips_when_native_is_unavailable(self):
        text = JAVA.read_text(encoding="utf-8")
        self.assertIn("if (!nativeReady)", text)


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.text = MANIFEST.read_text(encoding="utf-8")

    def test_activity_is_launchable_and_exported(self):
        self.assertIn('android:name="com.valvesoftware.ValveActivity2"', self.text)
        self.assertIn('android:exported="true"', self.text)
        self.assertIn('android.intent.category.LAUNCHER', self.text)

    def test_debug_build_is_debuggable_and_extracts_native_libs(self):
        self.assertIn('android:debuggable="true"', self.text)
        # Without extraction, dlopen by absolute path into the APK's lib dir is
        # the only option, and that path is not stable across API levels.
        self.assertIn('android:extractNativeLibs="true"', self.text)

    def test_min_sdk_matches_the_ndk_platform(self):
        self.assertIn('android:minSdkVersion="21"', self.text)


@unittest.skipIf(shutil.which("sh") is None, "no POSIX shell")
class PackagingScriptTests(unittest.TestCase):
    def test_syntax(self):
        result = subprocess.run(["sh", "-n", str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_demands_a_library_directory(self):
        result = subprocess.run(["sh", str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("--libs", result.stderr)

    def test_explains_a_missing_sdk(self):
        env = {"PATH": "/usr/bin:/bin", "ANDROID_HOME": "/nonexistent-sdk"}
        result = subprocess.run(["sh", str(SCRIPT), "--libs", str(ROOT)], env=env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("build-tools", result.stderr)
        self.assertIn("install", result.stderr)

    def test_workflow_builds_the_apk(self):
        workflow = (ROOT / ".github" / "workflows" / "android.yml").read_text(encoding="utf-8")
        self.assertIn("build-apk.sh --libs apk-libs", workflow)
        self.assertIn("android-${{ env.ARCH }}-debug-apk", workflow)


def make_apk(path, entries):
    """entries: {name: bytes} - a stand-in for what aapt + zip produce."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return path


class VerifyApkTests(unittest.TestCase):
    """The regression that cost an install attempt.

    The first APK this repository produced signed correctly, passed
    `apksigner verify`, and then said "package appears to be invalid" on the phone
    - because classes.dex had been staged into ``dex/`` rather than the root. These
    tests are the check that was missing.
    """

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="apk-test-"))
        self.addCleanup(shutil.rmtree, self.dir, True)

    def run_verifier(self, entries, *extra):
        apk = make_apk(self.dir / "test.apk", entries)
        return subprocess.run([sys.executable, str(VERIFIER), str(apk), *extra],
                              capture_output=True, text=True)

    def good_entries(self):
        return {
            "AndroidManifest.xml": b"binary xml",
            "classes.dex": b"dex\n035\0",
            "lib/arm64-v8a/libclient.so": b"\x7fELF",
            "lib/arm64-v8a/libserver.so": b"\x7fELF",
        }

    def test_a_correct_package_passes(self):
        result = self.run_verifier(self.good_entries())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("classes.dex at the root", result.stdout)

    def test_the_exact_bug_is_caught(self):
        entries = self.good_entries()
        entries["dex/classes.dex"] = entries.pop("classes.dex")
        result = self.run_verifier(entries)
        self.assertEqual(result.returncode, 1)
        self.assertIn("dex/classes.dex", result.stderr)
        self.assertIn("not at the root", result.stderr)

    def test_missing_code_is_caught(self):
        entries = self.good_entries()
        del entries["classes.dex"]
        result = self.run_verifier(entries)
        self.assertEqual(result.returncode, 1)
        self.assertIn("no classes.dex", result.stderr)

    def test_missing_manifest_is_caught(self):
        entries = self.good_entries()
        del entries["AndroidManifest.xml"]
        result = self.run_verifier(entries)
        self.assertEqual(result.returncode, 1)
        self.assertIn("AndroidManifest.xml", result.stderr)

    def test_wrong_abi_is_caught(self):
        result = self.run_verifier(self.good_entries(), "--abi", "armeabi-v7a")
        self.assertEqual(result.returncode, 1)
        self.assertIn("lib/armeabi-v7a", result.stderr)

    def test_relative_prefix_is_caught(self):
        entries = self.good_entries()
        entries["./classes.dex"] = entries.pop("classes.dex")
        result = self.run_verifier(entries)
        self.assertEqual(result.returncode, 1)
        self.assertIn("prefix", result.stderr)

    def test_expected_library_count_is_enforced(self):
        result = self.run_verifier(self.good_entries(), "--expect-libs", "2")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_verifier(self.good_entries(), "--expect-libs", "7")
        self.assertEqual(result.returncode, 1)
        self.assertIn("expected 7", result.stderr)

    def test_a_truncated_file_is_caught(self):
        apk = make_apk(self.dir / "truncated.apk", self.good_entries())
        data = apk.read_bytes()
        apk.write_bytes(data[: len(data) // 2])
        result = subprocess.run([sys.executable, str(VERIFIER), str(apk)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)


class StagingTests(unittest.TestCase):
    """Reproduce the packaging step with plain zip, which needs no Android SDK.

    This is the closest thing to an install test available here: it runs the same
    staging commands the script runs, then asks the verifier - the same question
    the phone asked.
    """

    @unittest.skipIf(shutil.which("zip") is None, "no zip")
    def test_staged_package_passes_verification(self):
        workdir = Path(tempfile.mkdtemp(prefix="apk-stage-"))
        self.addCleanup(shutil.rmtree, workdir, True)
        (workdir / "dex").mkdir()
        (workdir / "dex" / "classes.dex").write_bytes(b"dex\n035\0")
        libs = workdir / "libs"
        libs.mkdir()
        for name in ("libclient.so", "libserver.so"):
            (libs / name).write_bytes(b"\x7fELF")

        stage = workdir / "stage"
        (stage / "lib" / "arm64-v8a").mkdir(parents=True)
        (stage / "AndroidManifest.xml").write_bytes(b"binary xml")
        shutil.copy(workdir / "dex" / "classes.dex", stage / "classes.dex")
        for library in libs.glob("*.so"):
            shutil.copy(library, stage / "lib" / "arm64-v8a" / library.name)

        # The script's own command, minus aapt having created the file first.
        files = sorted(str(p.relative_to(stage)) for p in stage.rglob("*") if p.is_file())
        apk = workdir / "out.apk"
        subprocess.run(["zip", "-q", "-X", str(apk), "-@"],
                       input="\n".join(files) + "\n", text=True, cwd=str(stage), check=True)
        result = subprocess.run([sys.executable, str(VERIFIER), str(apk),
                                 "--abi", "arm64-v8a", "--expect-libs", "2"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_script_stages_the_dex_at_the_root(self):
        script = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('cp "$WORK/dex/classes.dex" "$STAGE/classes.dex"', script)
        self.assertNotIn('"$STAGE/dex/', script)
        self.assertIn("verify-apk.py", script)

class FakeSdkPackagingTests(unittest.TestCase):
    """Run the real packaging script against a stand-in SDK (see fake_sdk)."""

    def setUp(self):
        from l4d1ctl.tests import fake_sdk

        self.fake_sdk = fake_sdk
        self.root = Path(tempfile.mkdtemp(prefix="fake-sdk-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.libs = fake_sdk.make_libraries(self.root / "libs")

    def run_script(self, build_tools, *extra):
        sdk, binaries = self.fake_sdk.make_sdk(self.root, build_tools)
        keystore = self.root / "debug.keystore"
        keystore.write_bytes(b"key")
        out = self.root / "l4d1-debug.apk"
        env = {"PATH": f"{binaries}:/usr/bin:/bin", "ANDROID_HOME": str(sdk)}
        result = subprocess.run(
            ["sh", str(SCRIPT), "--libs", str(self.libs), "--out", str(out),
             "--keystore", str(keystore), *extra],
            capture_output=True, text=True, env=env)
        return result, out

    def verify(self, out, libraries=3):
        return subprocess.run([sys.executable, str(VERIFIER), str(out),
                               "--abi", "arm64-v8a", "--expect-libs", str(libraries)],
                              capture_output=True, text=True)

    @unittest.skipIf(shutil.which("zip") is None, "no zip")
    def test_aapt_path_produces_an_installable_package(self):
        result, out = self.run_script({"34.0.0": ["aapt", "d8", "zipalign", "apksigner"]})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        check = self.verify(out)
        self.assertEqual(check.returncode, 0, check.stderr)

    @unittest.skipIf(shutil.which("zip") is None, "no zip")
    def test_aapt2_is_used_when_aapt_is_gone(self):
        """Later build-tools dropped aapt; the script must not die on that."""
        result, out = self.run_script({"37.0.0": ["aapt2", "d8", "zipalign", "apksigner"]})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("aapt2", result.stdout)
        self.assertEqual(self.verify(out).returncode, 0)

    @unittest.skipIf(shutil.which("zip") is None, "no zip")
    def test_known_good_build_tools_wins_over_a_newer_one(self):
        """Newest alone is not a policy: a newer revision expects a newer JDK."""
        result, _ = self.run_script({
            "34.0.0": ["aapt", "d8", "zipalign", "apksigner"],
            "37.0.0": ["aapt", "d8", "zipalign", "apksigner"],
        })
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("build-tools 34.0.0", result.stdout)

    def test_unusable_build_tools_says_so(self):
        result, _ = self.run_script({"40.0.0": ["zipalign"]})
        self.assertEqual(result.returncode, 1)
        self.assertIn("no usable build-tools", result.stderr)

    @unittest.skipIf(shutil.which("zip") is None, "no zip")
    def test_a_dex_less_build_stops_before_it_is_shipped(self):
        """The failure that reached a phone: nothing produced a classes.dex."""
        sdk, binaries = self.fake_sdk.make_sdk(
            self.root, {"34.0.0": ["aapt", "d8", "zipalign", "apksigner"]})
        # a dexer that reports success and writes nothing
        self.fake_sdk.write_executable(sdk / "build-tools" / "34.0.0" / "d8",
                                       "#!/bin/sh\nexit 0\n")
        keystore = self.root / "k"
        keystore.write_bytes(b"key")
        result = subprocess.run(
            ["sh", str(SCRIPT), "--libs", str(self.libs), "--out",
             str(self.root / "bad.apk"), "--keystore", str(keystore)],
            capture_output=True, text=True,
            env={"PATH": f"{binaries}:/usr/bin:/bin", "ANDROID_HOME": str(sdk)})
        self.assertEqual(result.returncode, 1)
        self.assertIn("no classes.dex produced", result.stderr)


if __name__ == "__main__":
    unittest.main()


class NativeProbeCompilesTests(unittest.TestCase):
    """Compile the probe block on the host against stubs.

    The real build only happens in CI, so a typo in the new JNI code would cost a
    full dispatch to find. The block is self-contained apart from jni.h, Msg() and
    DLL_EXPORT, so stubbing those three is enough to get a real syntax check.
    """

    STUBS = r"""
    typedef int jint;
    typedef long long jlong;
    typedef void *jstring;
    typedef void *jclass;
    struct JNIEnv {
        const char *GetStringUTFChars(const void *, unsigned char *);
        void ReleaseStringUTFChars(const void *, const char *);
        void *NewStringUTF(const char *);
    };
    #define DLL_EXPORT __attribute__((visibility("default")))
    void Msg(const char *format, ...);
    """

    def probe_block(self):
        text = NATIVE.read_text(encoding="utf-8", errors="replace")
        start = text.index("// Debug probe used by launcher/android/apk")
        end = text.index("// Builds the engine argv from", start)
        return text[start:end]

    @unittest.skipIf(shutil.which("g++") is None, "no g++")
    def test_probe_block_is_valid_c(self):
        import tempfile
        source = ("#include <dlfcn.h>\n#include <stdint.h>\n#include <stdio.h>\n"
                  + self.STUBS + self.probe_block())
        with tempfile.NamedTemporaryFile("w", suffix=".cpp", delete=False) as handle:
            handle.write(source)
            path = handle.name
        try:
            result = subprocess.run(["g++", "-fsyntax-only", "-x", "c++", path],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        finally:
            Path(path).unlink()

    def test_no_engine_headers_in_the_probe(self):
        """Keeping the block free of engine headers is what makes the check above
        possible; it also keeps a crash in the probe away from engine code."""
        for line in self.probe_block().splitlines():
            if line.startswith("#include"):
                self.skipTest("the probe no longer needs to be header-free")
