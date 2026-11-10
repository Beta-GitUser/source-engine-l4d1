"""Tests for the shell scripts in scripts/.

They are part of the tooling surface - CI calls build-android.sh, and a human
calls make-keystore.sh exactly once and has to get it right - but nothing
exercises them. These tests cover the parts that can be checked without an NDK
or a JDK: syntax, refusal to clobber, and the instructions they print.

make-keystore.sh is driven with a fake ``keytool`` on PATH, so the real
credential path is never touched and no key material exists after the test.
"""

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"

SH = shutil.which("sh")
KEYTOOL = shutil.which("keytool")


def shell(script: str, *args, env=None, cwd=None, stdin=""):
    return subprocess.run(
        [SH, str(SCRIPTS / script), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd or ROOT),
        input=stdin,
    )


def fake_keytool(directory: Path) -> Path:
    binary = directory / "keytool"
    binary.write_text(
        "#!/bin/sh\n"
        'echo "fake keytool $*"\n'
        'case "$*" in\n'
        '  *-list*) echo "Alias name: fake"; echo "SHA256: 00:11";;\n'
        '  *-genkeypair*)\n'
        '    while [ $# -gt 0 ]; do\n'
        '      if [ "$1" = "-keystore" ]; then shift; echo "JKS" > "$1"; fi\n'
        "      shift\n"
        "    done;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    return binary


@unittest.skipIf(SH is None, "no POSIX shell")
class ScriptSyntaxTests(unittest.TestCase):
    def test_every_script_parses(self):
        scripts = sorted(SCRIPTS.glob("*.sh"))
        self.assertTrue(scripts, "no scripts/*.sh found")
        for script in scripts:
            result = subprocess.run([SH, "-n", str(script)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0,
                             f"{script.name}: {result.stderr.strip()}")

    def test_scripts_are_executable(self):
        for script in sorted(SCRIPTS.glob("*.sh")):
            if script.name == "build-android-armv7a.sh":
                continue  # historical, superseded by build-android.sh
            self.assertTrue(os.access(script, os.X_OK), f"{script.name} is not executable")


@unittest.skipIf(SH is None, "no POSIX shell")
class MakeKeystoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.env = dict(os.environ)
        self.env["PATH"] = str(self.bin) + os.pathsep + self.env.get("PATH", "")
        self.env["KEYSTORE"] = str(self.tmp / "keys" / "l4d1-release.jks")

    def tearDown(self):
        self._tmp.cleanup()

    def test_refuses_to_overwrite_an_existing_key(self):
        (self.tmp / "keys").mkdir()
        (self.tmp / "keys" / "l4d1-release.jks").write_bytes(b"existing")
        fake_keytool(self.bin)
        result = shell("make-keystore.sh", env=self.env)
        self.assertEqual(result.returncode, 1)
        self.assertIn("refusing to overwrite", result.stderr)
        self.assertEqual((self.tmp / "keys" / "l4d1-release.jks").read_bytes(), b"existing")

    def test_creates_keystore_and_prints_the_secret_commands(self):
        fake_keytool(self.bin)
        result = shell("make-keystore.sh", env=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.tmp / "keys" / "l4d1-release.jks").is_file())
        self.assertTrue((self.tmp / "keys" / "l4d1-release.jks.base64").is_file())

        out = result.stdout
        for needle in (
            "gh secret set ANDROID_KEYSTORE_BASE64",
            "gh secret set ANDROID_KEYSTORE_PASSWORD",
            "gh secret set ANDROID_KEY_ALIAS",
            "gh secret set ANDROID_KEY_PASSWORD",
            "back it up",
        ):
            self.assertIn(needle, out)

    def test_passes_a_sane_key_specification_to_keytool(self):
        fake_keytool(self.bin)
        result = shell("make-keystore.sh", env=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("-keyalg RSA", result.stdout)
        self.assertIn("-validity", result.stdout)

    def test_honours_overrides(self):
        fake_keytool(self.bin)
        self.env["ALIAS"] = "custom"
        self.env["DAYS"] = "9125"
        self.env["KEYSIZE"] = "2048"
        result = shell("make-keystore.sh", env=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("alias custom", result.stdout)
        self.assertIn("RSA 2048", result.stdout)
        self.assertIn("9125", result.stdout)

    @unittest.skipIf(KEYTOOL is not None, "a real keytool is installed")
    def test_explains_a_missing_keytool(self):
        result = shell("make-keystore.sh", env=self.env)
        self.assertEqual(result.returncode, 1)
        self.assertIn("keytool not found", result.stderr)


@unittest.skipIf(SH is None, "no POSIX shell")
class BuildAndroidScriptTests(unittest.TestCase):
    def test_refuses_a_missing_ndk(self):
        env = dict(os.environ)
        env.update({"NDK_HOME": "/nonexistent/ndk", "USE_ENV_NDK": "1", "SKIP_SUBMODULES": "1"})
        result = shell("build-android.sh", env=env)
        self.assertEqual(result.returncode, 1)
        self.assertIn("NDK_HOME does not exist", result.stderr)

    def test_reports_an_unsupported_inherited_ndk_before_downloading(self):
        with tempfile.TemporaryDirectory() as tmp:
            ndk = Path(tmp) / "ndk"
            ndk.mkdir()
            (ndk / "source.properties").write_text("Pkg.Revision = 27.0.12077973\n")
            env = dict(os.environ)
            env.update({"NDK_HOME": str(ndk), "SKIP_SUBMODULES": "1"})
            result = shell("build-android.sh", env=env)
            self.assertIn("ignoring NDK 27", result.stderr)
            self.assertIn("r10e", result.stderr)


if __name__ == "__main__":
    unittest.main()
