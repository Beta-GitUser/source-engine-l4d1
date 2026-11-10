# Launcher and tooling

Two pieces, deliberately separate:

| Piece | Runs where | What it does |
| --- | --- | --- |
| `l4d1ctl/` (Python 3.8+, stdlib only) | desktop / Termux / CI | content fetching, mods, identity, `gameinfo.txt`, launch plan, diagnostics |
| `launcher/portcore/` (C++, no engine deps) | inside `launcher.so` on the phone | parses `port.cfg` and builds the engine argv |

The Python side is the reference implementation; the C++ side is what actually
runs at startup. `launch.py` and `portcfg.cpp` implement the same rules — if you
change one, change the other (their tests are in the same PR for that reason).

---

## `l4d1ctl` reference

Run from the repository root (`python3 -m l4d1ctl ...`). Global flags:

* `--gamedir <path>` — game directory; default `$L4D1_GAMEDIR`, else
  `<repo>/game/left4dead`.
* `--steam-root <path>` — Steam install to read; default auto-detect
  (`L4D1_STEAM_ROOT`, `~/.steam/steam`, `~/.local/share/Steam`,
  `~/Library/Application Support/Steam`, `%PROGRAMFILES(X86)%\Steam`,
  `/sdcard/srceng`, …).

### Content

```sh
# Copy your own Steam install (hardlinks make a second copy almost free)
python3 -m l4d1ctl import --from-steam --hardlink

# Copy from any folder that has left4dead/gameinfo.txt
python3 -m l4d1ctl import --source "/media/usb/Left 4 Dead"

# Print (never run) the steamcmd invocation, then run it
python3 -m l4d1ctl fetch --provider steamcmd --user <account>
python3 -m l4d1ctl fetch --provider steamcmd --user <account> --run

# DepotDownloader instead
python3 -m l4d1ctl fetch --provider depotdownloader --tool ~/DepotDownloader --user <account> --run

# The phone route: pack on the PC, unpack on the device
python3 -m l4d1ctl pack --source "/steam/steamapps/common/Left 4 Dead" --output l4d1.tar.gz
python3 -m l4d1ctl unpack l4d1.tar.gz
```

Rules that the fetcher follows:

* credentials are typed into `steamcmd`/`DepotDownloader`, never stored here;
* `bin/` and all `*.dll`/`*.exe`/`*.so` are skipped — this port rebuilds game
  code, and PC binaries are dead weight on a phone;
* anonymous access is not attempted: appid 500 depots are not publicly
  downloadable, and pretending otherwise produces confusing failures.

### `gameinfo.txt`

```sh
python3 -m l4d1ctl init            # refuses to touch a hand-written gameinfo
python3 -m l4d1ctl init --force    # replace it
```

The generated file mounts:

```
Game |gameinfo_path|.
Game |gameinfo_path|mods/enabled/*
Platform |all_source_engine_paths|platform
```

The `*` is expanded by the engine (`public/filesystem_init.cpp`,
`FileSystem_AddLoadedSearchPath`), which mounts every directory and every
`*_dir.vpk` below it, alphabetically, and skips `_nnn.vpk` parts when the
matching `_dir.vpk` is present. That single line is the whole mod system's
integration point.

### Mods

```
left4dead/mods/
  mymod/mod.vdf            manifest (source of truth)
  enabled/                 generated staging area — do not edit by hand
l4d1_port/mods.staged.json stamp of entries this tool created
```

```sh
python3 -m l4d1ctl mod add ~/Downloads/survivor_skins --order 10
python3 -m l4d1ctl mod list
python3 -m l4d1ctl mod disable survivor_skins
python3 -m l4d1ctl mod order survivor_skins 50
python3 -m l4d1ctl mod validate
python3 -m l4d1ctl mod sync --dry-run

# inspect a mod's pack before installing it
python3 -m l4d1ctl vpk info  ~/Downloads/mod_dir.vpk
python3 -m l4d1ctl vpk list  ~/Downloads/mod_dir.vpk --filter materials
python3 -m l4d1ctl vpk check ~/Downloads/mod_dir.vpk
python3 -m l4d1ctl vpk extract ~/Downloads/mod_dir.vpk --output /tmp/unpacked
```

Behaviour worth knowing:

* **Load order** is the numeric prefix: order `10` → `010_<id>`. Alphabetical
  mount order then equals the configured order. Later entries win conflicts,
  which matches how Source search paths layer.
* **VPK mods** are staged as `<NNN>_<id>_dir.vpk` plus consistently renamed
  parts, so the engine's `_dir.vpk`/part pairing still works.
* **Folder mods** are staged as a directory tree.
* Entries the tool did not create are never deleted (`mod validate` flags them).
* VPK name collisions between mods are a hard failure, not a warning — two mods
  both shipping `pak01_dir.vpk` cannot coexist.
* VPK mods are parsed with `l4d1ctl/vpk.py` (read-only implementation of the v1
  and v2 formats) on `mod add` and in `mod validate`: an unreadable pack, a
  missing `_nnn.vpk` part, or a zero-entry pack is a hard failure instead of a
  silent "why is my mod not loading".
* Staging defaults to hardlinks (falls back to copy). On filesystems without
  links, use `--link-mode copy`; `--link-mode symlink` works on app-private
  storage but not on most shared/emulated storage.

### Identity

```sh
python3 -m l4d1ctl identity                     # from the most recent local account
python3 -m l4d1ctl identity --name "Zoey Fan"
python3 -m l4d1ctl identity --steamid STEAM_0:0:1
python3 -m l4d1ctl identity --avatar            # needs a Steam Web API key
STEAM_WEBAPI_KEY=... python3 -m l4d1ctl identity --avatar
```

Writes:

* `left4dead/l4d1_port/identity.json` — SteamID64, persona name, avatar path;
* `left4dead/cfg/l4d1_identity.cfg` — `name "<persona>"`, executed by the engine
  on launch (`+exec l4d1_identity.cfg`);
* `left4dead/l4d1_port/avatars/<steamid64>.png` — cached avatar for the
  launcher UI.

**What this is:** a display identity read from your own Steam configuration plus
your own Steam Web API key. It makes the launcher and the in-game name look like
you.

**What this is not:** authentication. No logon, no session ticket, no
`ISteamUser` implementation, nothing that could join a retail server or unlock
entitlements. Attempting to fake tickets is technically useless against Valve's
backend and would put the account at risk; see FEASIBILITY.md §3–§5.

### Game module

```sh
python3 -m l4d1ctl module status                 # what is in <gamedir>/bin
python3 -m l4d1ctl module install --from game/hl2/bin --dry-run
python3 -m l4d1ctl module install --from game/hl2/bin --link
python3 -m l4d1ctl module remove                 # engine will not start after this
```

`status --json` reports each module's ELF format (bits/machine/kind) so an
architecture mismatch is visible before the engine fails at `LoadModule`.
Details and the gameplay side: [GAMECODE.md](GAMECODE.md).

### Launch, status, diagnostics

```sh
python3 -m l4d1ctl launch --print --map c1m1_hotel
python3 -m l4d1ctl launch --windowed
python3 -m l4d1ctl status --json
python3 -m l4d1ctl doctor
```

`launch` runs the preflight checks first and refuses to start on a blocking
issue (`--force` overrides, `--no-check` skips). `--run` starts the engine as a
child process instead of `exec`ing it, captures the log to
`l4d1_port/logs/launch-<stamp>.log` and triages it:

```sh
python3 -m l4d1ctl check --json                  # would this launch work? (UI contract)
python3 -m l4d1ctl check --engine ./hl2_linux
python3 -m l4d1ctl launch --run --map c1m1_hotel
python3 -m l4d1ctl selftest                      # rehearse all pipelines, no content
```

What each verdict means, and what to run for a download versus a launch:
[TESTING.md](TESTING.md).

`status --json` is the contract for a launcher UI:

```json
{
  "gamedir": "/sdcard/srceng/left4dead",
  "content_ok": true,
  "files": 4213,
  "bytes": 4781506560,
  "gameinfo": true,
  "gameinfo_generated": true,
  "mods": [{"id": "survivor_skins", "name": "Survivor skins", "enabled": true,
            "order": 10, "kind": "folder"}],
  "identity": {"steamid64": 7656119..., "name": "Zoey Fan", "avatar": "..."}
}
```

---

## `port.cfg` reference

`launcher/android/main.cpp` reads `$APP_DATA_PATH/port.cfg` at startup. Missing
file ⇒ historical defaults (`-fullscreen -nosteam -insecure -touch`, game
`left4dead`), so existing installs keep working. Example file:
[`launcher/port.cfg.example`](../../launcher/port.cfg.example).

```kv
// Port configuration; read by launcher.so at startup.
"port"
{
	game          "left4dead"
	extra_args    "-novid -console -nouserclip"
	identity_cfg  "l4d1_identity.cfg"   // appended as +exec; empty disables
	player_name   "Zoey Fan"            // logging only; the cfg sets `name`
	fullscreen    "1"
	touch         "1"
	nosteam       "1"
	insecure      "1"
	console       "0"
}
```

Semantics, matching `l4d1ctl/launch.py`:

1. `argv[0]` = `$APP_DATA_PATH/hl2_linux`;
2. `extra_args`, split quote-aware;
3. `-game <game>`;
4. arguments handed over by the Java activity (e.g. `+map c1m1_hotel`);
5. `-fullscreen`/`-windowed`, `-touch`, `-console`, `-nosteam`, `-insecure`;
6. `+exec <identity_cfg>` when set.

If the argv would exceed 512 entries the launcher logs how many were dropped and
still starts with what fits (`Warning` in the engine log).

Notes:

* `-insecure` only disables VAC on servers *you* host. Keep it while there is no
  Steam session; it is not a cheat toggle.
* The launcher logs the resolved command line (`launcher: ...`) on every start —
  the first thing to check in a bug report.
