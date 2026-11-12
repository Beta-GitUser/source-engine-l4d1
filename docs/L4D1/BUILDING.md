# Building it: where, how, and why not on the phone

The short version: **the build cannot happen on the phone, and GitHub Actions
is the build machine.** Everything else in this document is the detail.

## Status: it builds

Run [42999936541](https://github.com/Beta-GitUser/source-engine-l4d1/actions/runs/42999936541)
(commit `1966bfb`) is the first green Android build: `configure` and `build`
succeeded, `libclient.so` and `libserver.so` were collected, and the workflow's
own verification step confirmed both are ELF **AArch64** rather than host
binaries. Artifacts: `android-aarch64-libraries` (108 MB, the `.so` files) and
`android-aarch64-logs` (9 MB, `config.log` and the full build log), downloadable
from the run page.

What it took, in order, each found by a run rather than guessed:

| Failure | Cause |
| --- | --- |
| `fprintf@LIBC` undefined | prebuilt SDL2 linked against a different libc; tolerated with `-Wl,--allow-shlib-undefined` |
| `undefined reference to __sync_bool_compare_and_swap_16` | no 16-byte CAS on aarch64 with GCC 4.9; provided with LDXP/STXP |
| `cannot find -lrt` (in the *configure* log) | a probe that cannot succeed on bionic; now skipped |
| `vreinterpretq_*_f64 was not declared` | GCC 4.9's `arm_neon.h` predates those intrinsics; shimmed as `memcpy` bit casts |
| `'snprintf'` / `'stoi'` not in `std` | the NDK's libstdc++ lacks the C++11 string conversions; launcher code uses `strtol` and unqualified C calls, enforced by a lint |

Run [43007904508](https://github.com/Beta-GitUser/source-engine-l4d1/actions/runs/43007904508)
(commit `6141c84`) is that build plus the debug APK: the same job compiles the
engine, collects the libraries, and packages `l4d1-debug.apk` on the end. All
three artifacts are published — `android-aarch64-debug-apk` (181 MB),
`android-aarch64-libraries` (108 MB), `android-aarch64-logs` (9 MB).

Run [43031993188](https://github.com/Beta-GitUser/source-engine-l4d1/actions/runs/43031993188)
(commit `fa3f697`) is the first packaging run that succeeded *and* passed the
installability check, after two failures: the dex staged one directory too deep,
then the newest build-tools paired with the image's default JDK 11. Its artifacts
are `android-aarch64-debug-apk` (181 MB), `android-aarch64-libraries` (108 MB),
`android-aarch64-apk-log` and `android-aarch64-logs`.

The earlier APK step succeeded too, and meant less than it looked: `aapt`, `d8`,
`zipalign`, `apksigner sign` and `apksigner verify` all reported success. The last
one was not enough: the package signed perfectly and then refused to install —
*"package appears to be invalid"* — because `classes.dex` had been staged into
`dex/` instead of the archive root, and nothing in the chain asks where the dex
is. `scripts/verify-apk.py` now asks that, plus the manifest, the ABI directory,
entry names and CRC, and `build-apk.sh` fails on its answer. The *contents* were not inspected from here — GitHub's artifact storage answers this
development sandbox with `EOF`, so the file has to be downloaded from the run page
and installed to be looked at. Treat the transcript on the phone as the first
evidence of anything beyond "it packaged".

Known gap, reported as a warning rather than a failure: **`libgameui.so` is not
produced.** The port's launcher owns that surface (see
[DEVIATIONS.md](DEVIATIONS.md)), but the retail client asks for `GameUI011`, so
this needs a decision before a device run can be considered complete.

## Why the phone can't build this

Cross-compiling for Android needs the NDK, and the NDK is a **host** toolchain:
Google publishes it for x86_64 Linux, x86_64 macOS and x86_64 Windows only.
There is no `android-ndk-*-linux-aarch64.zip`, and no `sdkmanager` package for
an arm64 host. So Termux on the phone has the compiler for *itself*, not the
cross-compiler for Android, and `./waf configure --android=...` cannot find an
NDK to use.

Wine is not an answer either: the Windows NDK under Wine means a Windows
`clang` driving Windows `link.exe` against a Linux `waf`, and every failure in
that chain is a Wine bug report rather than a build error.

What that leaves:

| Option | Works when | Notes |
| --- | --- | --- |
| GitHub Actions | always, free for public repos | x86_64 Linux + Android toolchain, artifacts downloadable |
| any x86_64 Linux box | you have one | identical script, `scripts/build-android.sh` |
| x86_64 macOS | you have one | same script, NDK darwin package |
| on-device build | never, currently | needs an aarch64 NDK that does not exist |

## The CI build

`.github/workflows/android.yml` builds arm64 (or armv7a) and uploads the
libraries. It is manual-first, like the other workflows here:

```sh
gh workflow run "Android build" --ref main
gh run list --workflow "Android build"
gh run watch <run-id>
gh run download <run-id> -n android-aarch64-libraries   # -> out/
```

Inputs (Actions → "Android build" → *Run workflow*):

| Input | Default | Meaning |
| --- | --- | --- |
| `arch` | `aarch64` | `aarch64` or `armeabi-v7a` |
| `ndk_version` | `r10e` | revision to fetch; `xcompile.py` supports r10e, r19c, r20 |
| `games` | `left4dead` | game code to build; use `hl2` when iterating on toolchain problems |
| `api` | `21` | Android API level (21 is the floor for 64-bit) |

Every run uploads two artifacts even when it fails:

* `android-<arch>-libraries` — `libclient.so`, `libserver.so`, `liblauncher.so`, `libgameui.so` that were produced
* `android-<arch>-logs` — `build-android.log` and `build/config.log`

The job also runs `l4d1ctl inspect` on the modules and **fails if they are not
the requested architecture**, so a build that silently used the host toolchain
is caught rather than shipped.

Automatic runs on push stay off unless the repository variable
`L4D1_CI_ON_PUSH` is `true` (Settings → Secrets and variables → Actions →
Variables), the same policy as `build.yml` and `tests.yml`.

## The same build locally

Any x86_64 Linux machine, no Android SDK required:

```sh
sh scripts/build-android.sh                              # aarch64, NDK r10e, release
ARCH=armeabi-v7a API=19 sh scripts/build-android.sh
NDK_HOME=/opt/android-ndk-r10e sh scripts/build-android.sh   # reuse an NDK
GAMES=hl2 sh scripts/build-android.sh                    # faster loop
PACK=1 sh scripts/build-android.sh                       # tar the libraries
```

The script downloads the NDK into `.ndk/` when `NDK_HOME` is unset, initialises
submodules, configures with `--android=<arch>,<toolchain>,<api>`, builds, and
then prints every library it produced with its size.

The NDK revision is not a free choice: `scripts/waifulib/xcompile.py` hardcodes
the NDK layouts it understands (`ANDROID_NDK_SUPPORTED = [10, 19, 20]`) and
still adds gnu-libstdc++ 4.9 include paths and `-lgnustl_static` at configure
time. That is why `r10e` is the default, matching
`scripts/build-android-armv7a.sh`. If you move to r21+ you are porting
`xcompile.py` to libc++ first — do that as its own change, with the CI log in
front of you.

## What gets built, and where it goes

Two different layouts, and installing the wrong one is the classic mistake:

| Layout | Produced by | Modules land in | Engine looks in |
| --- | --- | --- | --- |
| desktop | `./waf configure --build-games left4dead && ./waf build` | `game/left4dead/bin/{client,server}.so` | `<gamedir>/bin` (`GAMEBIN`) |
| Android | `scripts/build-android.sh` | `lib/<abi>/lib{client,server}.so` next to the engine | the engine's own library directory |

On Android the modules are `libclient.so` / `libserver.so` and they ship beside
the engine binary (inside the APK's `lib/<abi>/`, or in the engine folder you
pushed to the device). `l4d1ctl` accepts either spelling:
`module_name_for("libclient.so") == "client"`.

Installing a downloaded artifact:

```sh
# unzip the artifact from the run into ./out
python3 -m l4d1ctl inspect out/libclient.so out/libserver.so     # what is it?
python3 -m l4d1ctl module install --from out --force             # for the desktop layout
python3 -m l4d1ctl module status
```

For the Android layout you copy the two `.so` files next to the engine on the
device instead:

```sh
adb push out/libclient.so out/libserver.so /data/local/tmp/srceng/
adb shell "cd /data/local/tmp/srceng && ./hl2_linux -game left4dead -nosteam -insecure"
```

`/data/local/tmp` is writable and executable through `adb`, so this needs no root
and no package — a USB cable is the whole requirement, which is the situation
this document assumes.

If the phone is the only machine you have, that paragraph is not available: with
no `adb` there is no shell that can start a binary, and a bare `.so` cannot be
started at all, because the dynamic linker only maps libraries into a process it
creates. That case is what [The debug APK](#the-debug-apk) is for.

## The debug APK

`scripts/build-apk.sh` packages the cross-compiled libraries into
`l4d1-debug.apk`, and the CI job does this in the same run as the build, so one
dispatch produces both the `.so` set and the APK. It is the only way to put this
code in front of a device that is not rooted: the package is the process.

What it does is narrow on purpose. It is **not** a game yet — it loads the
modules and reports, step by step, how far it gets:

```
ok    libtier0.so
ok    libclient.so
ok    libclient.so -> VClient017
none  libclient.so -> VClient016
```

That answers three questions nothing else can answer, all of them about bionic
rather than about our code:

1. does the linker accept our aarch64 modules (ABI, page size, ELF layout);
2. do their `DT_NEEDED` dependencies resolve on a real device;
3. does `CreateInterface` hand back the versions this SDK advertises.

### Crashes are the expected result, so the crash is contained

Loading the engine modules on a device where the engine has never run is likely to
segfault inside a static initialiser. That is the finding, not an accident, so it
must not be able to destroy the report. **Each probe runs in a forked child**
(`nativeProbeLibrary`), which does the `dlopen` and either reports or dies; the
parent waits and prints the verdict:

```
libtier0.so                        ok: loaded
libclient.so                       ok: loaded
libclient.so -> VClient017         ok: interface at 0x7f2c1a0040 (code 0)
libengine.so                       CRASHED in dlopen: SIGSEGV (11)
```

`alarm()` in the child turns a hang into `SIGALRM`, so a stuck constructor is also
an answer. Nothing the probe does can kill the process drawing the screen.

### Reading it without adb

This is the part the first version got wrong, and the phone said so. The transcript
was written only to the app's internal `files/probe.log` — which is unreachable
without root or adb, and since Android 11 `Android/data/` is closed to file
managers too. A log nobody can open is not a log.

So there are three ways out, in order of how little they ask of you:

| Where | How it gets there |
| --- | --- |
| the screen | always, with **Share log** and **Copy** buttons under it |
| `Documents/l4d1-probe.txt` | a `MediaStore` insert, which needs **no** storage permission on Android 10+ |
| `files/probe.log` (internal) | for the replay at the top of the next launch |

The path-based alternative — writing into `Documents/` with a `File` object — needs
`MANAGE_EXTERNAL_STORAGE` on Android 11+, so the manifest does not ask for it, and
no diagnostic is worth that permission. On Android 9 and older the manifest
declares `WRITE_EXTERNAL_STORAGE`, capped at API 28, purely for the fallback path.

The activity also prints the `packaged:` list of the APK's `lib/` directory, so a
library whose name differs from the guess in `ValveActivity2.LIBRARIES` is visible
in the transcript instead of silently skipped. Missing libraries are skipped, not
fatal.

If `liblauncher.so` itself fails to load, the failure is caught in the static
initialiser and printed with the linker's own message — the usual cause is a
dependency that was not built for aarch64, and the message names it.

### Which tools it uses, and why that is not "the newest"

The runner image has build-tools up to 37 and **JDK 11 as its default**. Taking
the newest of each is how a step dies with an exit code and no sentence: later
build-tools no longer ship `aapt`, and they expect a newer JDK than the default
one. So the script pins `JAVA_HOME` to JDK 17 when the image provides it, prefers
build-tools `34.0.0` when present, accepts `aapt2 link` when `aapt` is gone, and
labels every phase's failure with a sentence instead of a number.

### What has not been verified

The packaging script and the Java side have **not been run**: this workspace has
no Android SDK, so they cannot be built here. What is checked mechanically
(`l4d1ctl/tests/test_apk.py`, 17 tests) is the part that would otherwise fail late
and vaguely: every `native` method declared in Java has a C++ implementation with
the exact JNI symbol name, the manifest declares a launchable exported activity,
the probe block compiles, the script fails with a sentence rather than a stack of
`command not found`, and the workflow actually calls it.

## Checking a cross-compiled build on the host

`l4d1ctl check` compares binaries against the machine running it, which is the
wrong question when the target is a phone. Declare the target:

```sh
L4D1_TARGET_MACHINE=aarch64 python3 -m l4d1ctl check --engine out/hl2_linux
L4D1_TARGET_MACHINE=arm64-v8a python3 -m l4d1ctl check     # Android ABI names work too
```

`check --json` echoes the machine it compared against as `target_machine`, so a
CI job can assert it. Without the variable, an arm64 module on an x86 host is
reported as a blocking architecture mismatch — correct, and the `->` line tells
you to set the variable.

## Why the first Android builds fail, in two axes

Every failure so far is one of three things, not a pile of unrelated breakage:

1. **glibc names vs bionic.** The vpc/wscript files name their system libraries
   the Linux way (`rt`, `log`, `dl`, `unwind`). Android has no `librt`; the log
   and dl entry points live in `liblog.so` / `libdl.so`. The fix is a single
   translation table in `wscript`'s android branch rather than a patch per
   target. Do not read a `cannot find -lrt` line in a build log as a link
   failure without checking which step emitted it: `check_deps()` used to probe
   for librt during configure, and a failed probe looks identical to a broken
   link. It is skipped on Android now.
2. **The compiler is old, and the code assumes newer intrinsics.** NDK r10e
   ships GCC 4.9, whose `arm_neon.h` predates the `vreinterpretq_*_f64`
   intrinsics that `sse2neon.h` is built on, so every file that includes that
   header fails with "was not declared in this scope". The fork shims the whole
   family as `__builtin_memcpy` bit casts, which compile to the same register
   moves the intrinsics would have produced.
3. **32-bit assumptions in 64-bit code.** The armv7a build never exercises the
   `PLATFORM_64BITS` paths; aarch64 does. The first one hit is
   `public/tier0/tslist.h`'s 128-bit head, which needs a 16-byte
   compare-and-swap. GCC cannot inline that on AArch64 (no `cmpxchg16b`, and
   ARMv8.1's single-instruction CAS is newer than NDK r10e), so it emits a call
   to `__sync_bool_compare_and_swap_16`, which libatomic does not provide under
   that name. `tier0/threadtools.cpp` now defines it with LDXP/STXP - the ARMv8
   load/store exclusive pair, which is exactly a 128-bit LL/SC, so the list
   stays lock-free.

Both are real portability fixes rather than flag juggling: nothing is suppressed
and no target silently loses a library.

One wart remains: the prebuilt `lib/android/<abi>/libSDL2.so` was linked against
a differently versioned libc, so its `fprintf@LIBC`-style symbols need
`-Wl,--allow-shlib-undefined`. The correct long-term fix is rebuilding SDL2 with
the same NDK; until then, only *shared-library* undefined symbols are tolerated,
never undefined symbols in this tree's own objects.

## Signing: make the keystore before you need it

A release keystore is the one file in this project that **cannot be recreated**.
If you publish an APK signed with it and then lose it, you cannot ship an update
to that app again - the install will be rejected as a different signer. So create
it once, early, and back it up somewhere you will still have in five years.

```sh
sh scripts/make-keystore.sh          # prompts for a password, writes l4d1-release.jks
```

which is the same as this, minus the checks:

```sh
keytool -genkeypair -v -keystore l4d1-release.jks -alias l4d1 \
  -keyalg RSA -keysize 4096 -validity 10000 -storetype JKS \
  -dname "CN=Left 4 Dead 1 (Android port), OU=l4d1, O=l4d1, C=NL"
```

Choices worth knowing:

| Flag | Why |
| --- | --- |
| `-keysize 4096` | overkill by current standards (2048 is the norm) and cheap for one key |
| `-validity 10000` | ~27 years; Play requires a certificate valid past 2033 |
| `-storetype JKS` | PKCS12 is the JDK default since 9, but JKS is what every Android tool and every tutorial still expects |
| no `-storepass` | the password is typed at the prompt, so it never reaches shell history or `ps` |

Use the same password for the store and the key. The script prints the four
`gh secret set` commands when it is done; CI needs them as secrets, never as
files in the repository:

| Secret | Value |
| --- | --- |
| `ANDROID_KEYSTORE_BASE64` | `base64 < l4d1-release.jks \| tr -d '\n'` |
| `ANDROID_KEYSTORE_PASSWORD` | the store password |
| `ANDROID_KEY_ALIAS` | `l4d1` |
| `ANDROID_KEY_PASSWORD` | the key password |

A future APK job materialises it like this, and nothing ever writes the password
to the log:

```yaml
- name: Decode the signing key
  run: |
    echo "$ANDROID_KEYSTORE_BASE64" | base64 -d > "$RUNNER_TEMP/l4d1-release.jks"
- name: Sign
  run: |
    apksigner sign --ks "$RUNNER_TEMP/l4d1-release.jks" \
      --ks-key-alias "$ANDROID_KEY_ALIAS" \
      --ks-pass env:ANDROID_KEYSTORE_PASSWORD \
      --key-pass env:ANDROID_KEY_PASSWORD \
      --out app-release.apk app-unsigned.apk
```

`apksigner` produces v1+v2+v3 signatures by default, which is what Android 11+
expects; a debug build (`~/.android/debug.keystore`, generated automatically)
must never be distributed. `*.jks`, `*.keystore` and the generated `.base64`
copies are in `.gitignore` - do not "helpfully" commit one.

## Patching vendored code

`sse2neon.h` is not ours; it is MIT, which permits modification, and this fork
does modify it to survive GCC 4.9. The rules for that - keep the licence text,
record the change in the file, and leave a test that fails if the patch is
dropped - are in [THIRDPARTY.md](THIRDPARTY.md).

## When the build fails

| Symptom | Usually means |
| --- | --- |
| `Set ANDROID_NDK_HOME or ANDROID_NDK environment variable` | NDK_HOME not exported / download failed |
| `Unknown NDK revision: 26` | `xcompile.py` doesn't know that NDK; use r10e (or port it) |
| `arm64-v8a` rejected as an arch | `--android` takes `aarch64`; the ABI name is derived (`xcompile.apk_arch()`) |
| `thirdparty/` empty or missing headers | submodules not initialised |
| link error mentioning `gnustl_static` | NDK too new for the hardcoded gnu-libstdc++ paths |
| `undefined reference to 'glGenFramebuffers'` / `AAsset_seek64` | linking `libSDL2.so` without `-lGLESv2 -landroid`; `xcompile.py` now adds the Android platform libs |
| `Unknown NDK revision: 27` | an inherited NDK won; the script now rejects unsupported revisions and fetches `NDK_VERSION` |
| `'stoi' is not a member of 'std'` / `'snprintf' is not a member of 'std'` | the NDK's libstdc++ does not provide the C++11 string conversions, and bionic need not put `snprintf` in `std::`. Use `strtol`, include what you use, call C functions unqualified - `l4d1ctl/tests/test_cxx_hygiene.py` enforces all three |
| `undefined reference to '__sync_bool_compare_and_swap_16'` | 16-byte CAS on AArch64; provided by `tier0/threadtools.cpp` via LDXP/STXP |
| `ld: cannot find -lrt` in the **configure** log | not a link failure: `check_deps()` probes `lib='rt'`, which cannot exist on bionic. Now skipped for Android (`wscript`), and `LIB_RT` is emptied so no target can request it |
| `undefined reference to 'fprintf@LIBC'` / `read@LIBC` | the prebuilt SDL2 was linked against a differently versioned libc stub; `xcompile.py` now passes `-Wl,--allow-shlib-undefined` (the device's libc resolves them at runtime). The real fix is rebuilding SDL2 for this NDK |
| `-lSDL2` / `libSDL2.so` missing | SDL2 in `lib/android/<arch>/` is not populated for this tree |
| `Exec format error` on device | binary and module came from different arches — `l4d1ctl inspect` both |
| `Unable to load client DLL` on device | modules not beside the engine, or `lib` prefix missing |

The first run of the workflow is expected to need iteration: the toolchain
combination is old (NDK r10e era) and this tree has never been built for arm64
by anyone. That is exactly why the log is uploaded even when the build fails —
`build/config.log` names the missing header or library, and `games=hl2` cuts
the iteration time down to the game code alone.
