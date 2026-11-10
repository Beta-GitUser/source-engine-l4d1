# Left 4 Dead 1 on this fork: status, launcher tooling, plan

This folder is the working documentation for running **Left 4 Dead 1** (appid
500) on the nillerusr source-engine fork. It is split into three documents:

| Document | What it answers |
| --- | --- |
| [FEASIBILITY.md](FEASIBILITY.md) | What already works in this engine, what blocks L4D1, and which parts of the retail game cannot legally be reused. Read this before promising anyone a port. |
| [ROADMAP.md](ROADMAP.md) | The order of work: launcher → asset loading → walkable maps → co-op skeleton → Director. Each phase has an exit criterion. |
| [LAUNCHER.md](LAUNCHER.md) | The launcher that exists today: `l4d1ctl` (content fetching, mods, identity) and `launcher/portcore` (the C++ side that runs on the phone). |
| [BUILDING.md](BUILDING.md) | Where the build happens and why the phone cannot do it: the NDK is an x86_64 host toolchain, so arm64 builds come from GitHub Actions as artifacts. |
| [TESTING.md](TESTING.md) | How to test a download and a launch without Steam or content: `selftest`, `check`, `launch --run` and what each engine error means. |
| [GAMECODE.md](GAMECODE.md) | The game module: how map hosting and walking work today via the HL2 bootstrap, what L4D entities get skipped and why that is safe, and how map intro cutscenes are built. |

## Short version

* **Assets:** feasible. L4D1's VPK/BSP/MDL formats are inside what this engine
  already reads, so the engine can *mount and render* L4D1 content.
* **Gameplay:** not feasible by "running L4D1". The retail `client.dll` /
  `server.dll` are Windows x86 builds for a different engine branch, and no L4D1
  game code was ever released. Co-op has to be reimplemented against this
  engine's interfaces.
* **Launcher/co-op plumbing:** that is the part we can build now, and this
  repository now has it: content fetching from *your own* Steam copy, a mod
  manager that uses the engine's own search-path wildcard mounting, an offline
  identity (name / SteamID64 / avatar) and a config-driven launch path.

Nothing here redistributes game content. Every route to content requires a
Steam account that owns Left 4 Dead.

## Quick start

```sh
# from the repository root
python3 -m l4d1ctl doctor                       # what is missing?
python3 -m l4d1ctl import --from-steam          # copy your own install
python3 -m l4d1ctl init                         # write gameinfo.txt + mods/
python3 -m l4d1ctl identity --avatar             # name / SteamID64 / avatar
python3 -m l4d1ctl mod add ~/Downloads/some_mod  # enable a mod, staged + ordered
python3 -m l4d1ctl launch --print                # show the engine command line
python3 -m l4d1ctl check                         # would that launch work?
python3 -m l4d1ctl selftest                      # rehearse everything, no content needed

# game module: host a map and walk on it with the HL2 bootstrap module
./waf configure -T release --build-games left4dead && ./waf build
python3 -m l4d1ctl module status                 # what is in left4dead/bin?
```

On Android the shell wrapper is not used; the Java activity passes its extra
arguments to `launcher/android/main.cpp`, which now reads `$APP_DATA_PATH/port.cfg`
(see [LAUNCHER.md](LAUNCHER.md#portcfg-reference)).
