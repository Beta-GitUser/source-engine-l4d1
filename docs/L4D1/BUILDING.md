# Building it: where, how, and why not on the phone

The short version: **the build cannot happen on the phone, and GitHub Actions
is the build machine.** Everything else in this document is the detail.

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

Every failure so far is one of two things, not a pile of unrelated breakage:

1. **glibc names vs bionic.** The vpc/wscript files name their system libraries
   the Linux way (`rt`, `log`, `dl`, `unwind`). Android has no `librt`; the log
   and dl entry points live in `liblog.so` / `libdl.so`. waf turns an *unknown*
   uselib name into nothing and a *known* one into `-l<name>`, so the fix is a
   single translation table in `wscript`'s android branch, not a patch per
   target.
2. **32-bit assumptions in 64-bit code.** The armv7a build never exercises the
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
| `undefined reference to '__sync_bool_compare_and_swap_16'` | 16-byte CAS on AArch64; provided by `tier0/threadtools.cpp` via LDXP/STXP |
| `ld: cannot find -lrt` | `check_cc(lib='rt')` succeeds against the sysroot stub but the real link has no librt; `wscript` now sets `LIB_RT = []` for Android |
| `undefined reference to 'fprintf@LIBC'` / `read@LIBC` | the prebuilt SDL2 was linked against a differently versioned libc stub; `xcompile.py` now passes `-Wl,--allow-shlib-undefined` (the device's libc resolves them at runtime). The real fix is rebuilding SDL2 for this NDK |
| `-lSDL2` / `libSDL2.so` missing | SDL2 in `lib/android/<arch>/` is not populated for this tree |
| `Exec format error` on device | binary and module came from different arches — `l4d1ctl inspect` both |
| `Unable to load client DLL` on device | modules not beside the engine, or `lib` prefix missing |

The first run of the workflow is expected to need iteration: the toolchain
combination is old (NDK r10e era) and this tree has never been built for arm64
by anyone. That is exactly why the log is uploaded even when the build fails —
`build/config.log` names the missing header or library, and `games=hl2` cuts
the iteration time down to the game code alone.
