# Handoff: the srceng-android fork

A starting prompt for the session that maintains the **carrier** — the Android app
around this engine. Copy the block below into that session; everything in it is a
fact established the hard way here, not a guess.

The split is deliberate: this repository is the engine and produces libraries. It
does not produce an app, and the attempt to make it do so was reverted (commit
`054e692`). The app is a fork of `nillerusr/srceng-android`.

---

```
You maintain a fork of nillerusr/srceng-android: the Android application that
carries a Source-engine port for Left 4 Dead 1. The engine itself is a different
repository (Beta-GitUser/source-engine-l4d1, branch arena/410ac3e6-source-engine-l4d1,
workflow "Android build"), which produces the native libraries; this repo only
produces the app that loads them.

SCOPE
- L4D1 only. Do not aim at L4D2.
- No piracy. Content comes from the user's own Steam account via an external depot
  downloader; never bundle or fetch game assets.
- The user has no build machine: builds happen in GitHub Actions, never on the
  phone. The phone is an unrooted Tecno with no adb. Assume the user cannot read
  logcat and cannot reach /data/data or Android/data.
- The user does not manage git; commit and push for them.

WHAT THE CARRIER IS (verified in the upstream repo, default branch android-fixes)
- An Ant project, not gradle. build.sh is "export ANDROID_HOME=...; ant debug".
  There is a committed debug.keystore, deliberately, so builds update in place.
- AndroidManifest.xml: package com.valvesoftware.source, minSdk 17, targetSdk 24.
- src/com/valvesoftware/ValveActivity2.java is NOT an activity. It is a static
  holder for setenv/setArgs/nativeOnActivityResult, called before launching.
- src/me/nillerusr/LauncherActivity.java is the GUI: game picker. DirchActivity
  picks the game directory. UpdateService is an updater.
- src/org/libsdl/app/*.java is SDL2's Java glue. org.libsdl.app.SDLActivity loads
  the engine: SDL.loadLibrary("liblauncher.so") then nativeRunMain(...). The engine
  therefore runs inside the SDL activity process, with SDL providing window,
  surface, input and audio. Do not replace this with a standalone executable or a
  NativeActivity; SDL2's Android backend requires its Java side.
- Environment the engine reads, set before the SDL activity starts:
  APP_DATA_PATH, APP_LIB_PATH (nativeLibraryDir), APP_MOD_LIB, VALVE_GAME_PATH,
  EXTRAS_VPK_PATH, LANG.

KNOWN INCOMPATIBILITY, fix it first
- ValveActivity2 declares nativeOnActivityResult(Activity, int, int, Intent) —
  four arguments. The engine's launcher/android/main.cpp implements
  Java_com_valvesoftware_ValveActivity2_nativeOnActivityResult with none. Same
  symbol name, different signature: compiles, then throws UnsatisfiedLinkError the
  first time it is called. Reconcile both sides deliberately.

MODULE NAMING (case-sensitive; this is where the engine repo wasted runs)
- The engine's loader is foundLibraryWithPrefix in tier1/interface.cpp. For a
  module name N it tries, under APP_LIB_PATH: lib<N>.so, <N>.so, and the same two
  under an "lib/" subdirectory.
- waf writes lib + target + .so, so the modules are libclient.so, libserver.so,
  liblauncher.so and libGameUI.so — note the capitals in GameUI.
- APP_LIB_PATH must be set, or the Android branch of Sys_LoadModule has no
  directory to search.

WHERE THE LIBRARIES COME FROM
- Download the artifact android-aarch64-libraries from the latest green run of the
  engine repo's "Android build" workflow. It contains libclient.so, libserver.so,
  liblauncher.so and libGameUI.so, all ELF AArch64.
- Put them where the app's nativeLibraryDir will be (the APK's lib/arm64-v8a/),
  because that is what APP_LIB_PATH is set to.

DIAGNOSTICS THAT WORK WITHOUT ADB
- Anything worth keeping must be visible on the device: on screen, through a share
  sheet, or written to Documents/ via a MediaStore insert (no storage permission
  needed on Android 10+; a plain path there would need MANAGE_EXTERNAL_STORAGE,
  which this project does not ask for).
- Assume a crash on first launch is normal and design so the report survives it.

MODERNITY, in order of likely breakage on a current device
- targetSdk 24 predates API 31's requirement for android:exported on the launcher
  activity, scoped storage, and per-app language handling. Raising it is work, not
  a one-line change; do it after the app runs once.
- Adding a second <application>/process is not the way to isolate a crash; note
  that the engine repo solved an equivalent problem by forking a child around each
  risky load.

FIRST MILESTONES, in order
1. Fork builds in CI: ant debug produces an APK, artifact uploaded.
2. That APK installs on the device and reaches the SDL window with the engine
   libraries present.
3. The JNI arity mismatch above is fixed, so nothing throws UnsatisfiedLinkError.
4. The app can be pointed at an L4D1 content directory prepared by the user.
5. Report what the engine prints when it tries to load a map. That output is the
   next real information either project has about the port.
```

---

## Why the split, in one paragraph

The carrier is a working, complete Android app: launcher UI, directory chooser,
updater, SDL glue and input, all of it battle-tested across nillerusr's HL2/Portal
builds. Re-deriving that in the engine repository produced a diagnostic probe app
that never ran a frame of a game. The engine repository's job is the part nobody
else has: aarch64 libraries for L4D1, and the game code behind them.
