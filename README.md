# Source Engine
[![GitHub Actions Status](https://github.com/nillerusr/source-engine/actions/workflows/build.yml/badge.svg)](https://github.com/nillerusr/source-engine/actions/workflows/build.yml) [![GitHub Actions Status](https://github.com/nillerusr/source-engine/actions/workflows/tests.yml/badge.svg)](https://github.com/nillerusr/source-engine/actions/workflows/tests.yml)
 Discord: [![Discord Server](https://img.shields.io/discord/672055862608658432.svg)](https://discord.gg/hZRB7WMgGw)
 

Information from [wikipedia](https://wikipedia.org/wiki/Source_(game_engine)):

Source is a 3D game engine developed by Valve.
It debuted as the successor to GoldSrc with Half-Life: Source in June 2004,
followed by Counter-Strike: Source and Half-Life 2 later that year.
Source does not have a concise version numbering scheme; instead, it was released in incremental versions

Source code is based on TF2 2018 leak. Don't use it for commercial purposes.

This project is using waf buildsystem. If you have waf-related questions look https://waf.io/book

# Features:
- Android, OSX, FreeBSD, Windows, Linux( glibc, musl ) support
- Arm support( except windows )
- 64bit support
- Modern toolchains support
- Fixed many undefined behaviours
- Touch support( even on windows/linux/osx )
- VTF 7.5 support
- PBR support
- bsp v19-v21 support( bsp v21 support is partial, portal 2 and csgo maps works fine )
- mdl v46-49 support
- Removed useless/unnecessary dependencies
- Achivement system working without steam
- Fixed many bugs
- Serverbrowser works without steam

# Current tasks
- Rewrite materialsystem for OpenGL render
- dxvk-native support
- Elbrus port
- Bink audio support( for video_bink )

# How to Build?
- [Building instructions(EN)](https://github.com/nillerusr/source-engine/wiki/Source-Engine-(EN))
- [Building instructions(RU)](https://github.com/nillerusr/source-engine/wiki/Source-Engine-(RU))

# Support nillerusr
BTC: `bc1qnjq92jj9uqjtafcx2zvnwd48q89hgtd6w8a6na`

ETH: `0x5d0D561146Ed758D266E59B56e85Af0b03ABAF46`

XMR: `48iXvX61MU24m5VGc77rXQYKmoww3dZh6hn7mEwDaLVTfGhyBKq2teoPpeBq6xvqj4itsGh6EzNTzBty6ZDDevApCFNpsJ`

## Continuous integration

Actions minutes are opt-in on this fork, because private-repo minutes are not
free and the engine matrix is 11 build jobs:

| Workflow | Manual | On push / pull request |
| --- | --- | --- |
| Tooling tests (`.github/workflows/tooling.yml`) - launcher tooling, ~40s | always | only if the repo variable `L4D1_TOOLING_CI` is `true`, and only when tooling paths changed |
| Build / Tests - full engine matrix, expensive | always | only if the repo variable `L4D1_CI_ON_PUSH` is `true` |

Variables live in **Settings -> Secrets and variables -> Actions -> Variables**.
Leave them unset and pushes trigger nothing that costs runner time.

```sh
gh workflow run "Tooling tests"                 # manual, current branch
gh workflow run Build                           # full matrix, when you mean it
scripts/test-tooling.sh                         # or just run it locally
scripts/test-tooling.sh cpp                     # one suite only
```

# Left 4 Dead 1 port

This fork targets Left 4 Dead 1 (appid 500). Status first, promises second:

- **Engine can read L4D1 assets** (BSP 19-21, VPK v1/v2, MDL 46-49) - yes.
- **Retail L4D1 game code can run here** - no. It is Windows x86 code for a
  different engine branch, and it is not redistributable.
- **Playing L4D1** therefore means reimplementing the game on this engine.
  That is a large project; the roadmap says what it costs.

Start here:

- [`docs/L4D1/FEASIBILITY.md`](docs/L4D1/FEASIBILITY.md) - what works, what
  blocks, and the legal boundaries.
- [`docs/L4D1/ROADMAP.md`](docs/L4D1/ROADMAP.md) - phases and exit criteria.
- [`docs/L4D1/GAMECODE.md`](docs/L4D1/GAMECODE.md) - map hosting and walking
  today (HL2 bootstrap module), and how L4D map intros actually work.
- [`docs/L4D1/LAUNCHER.md`](docs/L4D1/LAUNCHER.md) - launcher reference.
- [`docs/L4D1/BUILDING.md`](docs/L4D1/BUILDING.md) - where the build actually
  happens (CI: the NDK has no arm64 host package, so the phone cannot build it).
- [`docs/L4D1/TESTING.md`](docs/L4D1/TESTING.md) - testing a download and a
  launch without Steam, content or a compiler.

Launcher quick start (needs your own Steam copy, no content is bundled):

```sh
python3 -m l4d1ctl doctor            # what is missing
python3 -m l4d1ctl import --from-steam
python3 -m l4d1ctl init              # gameinfo.txt + mods/
python3 -m l4d1ctl identity --avatar  # name / SteamID64 / avatar (offline display identity)
python3 -m l4d1ctl mod add ./mymod    # mod manager, load-ordered
python3 -m l4d1ctl launch --print     # engine command line
```

Building for Android needs an x86_64 host, so it runs in CI:

```sh
gh workflow run "Android build"                        # arm64 by default
gh run download <run-id> -n android-aarch64-libraries  # libclient.so, libserver.so, ...
python3 -m l4d1ctl inspect out/libclient.so            # ELF 64-bit AArch64 ... VClient017
sh scripts/make-keystore.sh                            # do this once, and back it up
```

See [`docs/L4D1/BUILDING.md`](docs/L4D1/BUILDING.md) - including why the signing
key is the one file here that cannot be recreated.

On device, `launcher/android/main.cpp` reads `$APP_DATA_PATH/port.cfg`
(see [`launcher/port.cfg.example`](launcher/port.cfg.example)).

Tooling tests do not need the engine build:

```sh
scripts/test-tooling.sh   # 253 python tests + 67 C++ checks
```

# Fork note
Yes, this is vibecoded. Congrats, you have eyes. Anyways, actual note as i don't want to come off rude with that line, but i can't exactly get my laptop with integrated graphics up and running, so all i have left to do is to try and vibecode a native port of L4D1 for Android. Why not L4D2, which has practically all the L4D1 content built-in? Just cause, AI won't manage this. Or i won't manage testing every campaign. If this will turn out in a working state, i'll focus on improving it, and only then if stars will align i'll start working on L4D2.
