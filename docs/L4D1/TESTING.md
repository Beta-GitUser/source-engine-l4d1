# Testing the port: downloads, launches, and what "it blew up" means

Three tiers, in increasing cost. Everything in tier 0 and 1 runs with no Steam
account, no game content and no engine build, so it works in CI, on a laptop,
and in Termux on the phone.

| Tier | Command | Needs | Answers |
| --- | --- | --- | --- |
| 0 | `python3 -m l4d1ctl selftest` | Python only | Is the tooling itself broken? |
| 1 | `python3 -m l4d1ctl check` | a game folder | Will this launch fail, and why? |
| 2 | `python3 -m l4d1ctl launch --run` | engine + content | What happened when it ran? |

---

## "couldn't find materials/…" on every start

Four lines show up in the log of a healthy boot:

```
##### CTexture::LoadTextureBitsFromFile couldn't find materials/debug/debugluxelsnoalpha.vtf
##### CTexture::LoadTextureBitsFromFile couldn't find materials/dev/identitylightwarp.vtf
##### CTexture::LoadTextureBitsFromFile couldn't find materials/engine/normalizedrandomdirections2d.vtf
Can't find background image 'materials/console/background01_widescreen.vtf'
```

They are **not fatal** — the engine warns and continues — and they are not
peculiar to this port: nillerusr's tree produces the same four on a working install
(source-engine issue #292). What they do *not* tell you is which of two very
different things is true:

* the file is in the content and the engine is not looking there (mounting, search
  path, wrong base directory) — fixable here;
* the file is genuinely absent from what was downloaded — fixable only by fetching
  the right depot.

`l4d1ctl content` answers that instead of guessing. It indexes loose files and every
`*_dir.vpk` under the engine's base directory (so `left4dead/` and `platform/` both
count) and says, per asset, whether it was found and where:

```sh
python3 -m l4d1ctl content                      # scan the resolved base directory
python3 -m l4d1ctl content --root /storage/emulated/0/srceng
python3 -m l4d1ctl content --json               # for the launcher UI
```

`[found] … in pak01_dir.vpk` means the content has it: if the engine still says it
cannot find it, the problem is mounting, not content. `[absent]` means the download
lacks it, and nothing in this repository can conjure it up.

The `Requested non-existent write path MOD!` spam that accompanies these lines is
also upstream's: nothing registers a path under the ID `MOD` (or its `mod_write`
twin), so every write goes through `GetWritePath()`, misses, warns, and lands on
the default write path. Savegames, `sound.cache` and download lists all use that
ID, which is why it fires ~32 times before anything loads.

The engine now registers the gameinfo directory under path ID `MOD` while loading
search paths — the same place the fallback already used — so those calls resolve
instead of warning. If the warnings persist, the `libfilesystem_stdio.so` on the
device predates that change; the gameinfo is not the thing to edit.

## When it stops: the `##### stack` block

A fatal `Error()` ends in `Sys_Error`, and Breakpad takes the `SIGTRAP` it raises:
one sentence in the log and nothing else, on a device where nobody can attach a
debugger. So `Sys_Error` now unwinds and prints the call stack first, with every
address relative to the module it came from:

```
##### stack (14 frames):
#####   0: libengine.so+0x2f1c40
#####   1: libengine.so+0x2f1d08
#####   2: libclient.so+0x1a3f28
...
```

The modules are built with debug info, so that relative address names the
function, file and line:

```sh
addr2line -e libclient.so 0x1a3f28
c++filt <symbol>            # the tree is C++; names come out mangled
```

`Engine error: ...` lines come from the same place, so a log that stops on a
message is a log with a stack under it — read the stack, not the sentence.

Two startup failures that used to be fatal are not any more:

* **`scenes/scenes.image` from another Source game** — the scene image is only a
  cache that makes scene playback cheaper; every caller already copes with "not
  in the image". A different id or version is now a warning that prints what the
  file's header actually says, and the engine loads scenes individually.
* **A scene image too short to hold a header** — same treatment, same reason.

## Reading the boot log: what was mounted

On a phone there is no console to type `path` into, and "the file is missing"
looks identical to "the file was never mounted" — both print `couldn't find`. So
filesystem init now reports both sides itself, right after the search paths are
loaded:

```
[fs] mounted search paths:
---------------
Paths:
"/storage/emulated/0/srceng/left4dead/" "GAME"
"/storage/emulated/0/srceng/platform/" "PLATFORM"
[fs] boot asset materials/dev/identitylightwarp.vtf: found
[fs] boot asset materials/debug/debugluxelsnoalpha.vtf: MISSING
[fs] boot asset materials/engine/normalizedrandomdirections2d.vtf: MISSING
[fs] boot asset materials/console/background01_widescreen.vtf: MISSING
[fs] pak01_dir.vpk in the game directory: absent
```

Three things to read off it:

* **the list of `GAME` paths** — if the game directory is not there, nothing else
  matters; the search path, not the content, is the problem.
* **`(VPK)` next to a path** — every directory search path mounts the `*_dir.vpk`
  packs sitting in it, so after `<game dir>/` there should be a
  `(VPK)<game dir>/pak01_dir.vpk` line. `pak01_dir.vpk: present` together with no
  matching `(VPK)` line means content the engine cannot see, which is exactly what
  this port did before filesystem init mounted packs itself.
* **`found` vs `MISSING`** — the verdict for the four fallback assets, resolved
  through the engine's own search paths rather than a host-side scan.

## Tier 0 — `selftest`: rehearse the whole toolchain

```sh
python3 -m l4d1ctl selftest
python3 -m l4d1ctl selftest --keep /tmp/l4d1-drill   # keep the fake install
python3 -m l4d1ctl selftest --stage vpk              # one stage, repeatable
L4D1_SELFTEST_TRACEBACK=1 python3 -m l4d1ctl selftest
```

It builds a *real-shaped* game directory in a temp folder — a genuine VPK
(built by the same writer the tests use, read back by the production parser), a
generated `gameinfo.txt`, installed modules with correct ELF headers, a staged
mod, an identity file, a fake engine — and then runs the production pipelines
against it:

```
selftest passed (11/11 stages)  [/tmp/l4d1ctl-selftest-35k743io]
  [PASS] paths              left4dead/ with l4d1_port/{logs,avatars}, cfg/, bin/ GAMEBIN
  [PASS] gameinfo           3 search path(s): game, platform
  [PASS] vpk                3 entries read + CRC ok, 3 extracted, split pack ok
  [PASS] mods               add/enable/stage/order/disable/remove ok (100_selftest_mod)
  [PASS] modules            installed 3, second run unchanged, ELF x86-64
  [PASS] preflight          12 checks, verdict ready, negative case detected
  [PASS] triage             3 findings, 2 unknown entities
  [PASS] identity           identity.json round-trip + cfg generated
  [PASS] launch             14 argv entries, +exec l4d1_identity.cfg
  [PASS] archive            export + import round-trip, 6 copied, 0 linked, 3 skipped
  [PASS] depot              9 files, 0.0 MiB, 0 failures, 4 warnings
```

Two stages are **negative tests on purpose**: `preflight` deletes a module and
checks that the checker notices, and `triage` feeds a synthetic crash log to the
rules and checks that they fire. A checker that never says "no" is worthless.

If a stage fails, the tooling is broken in a way that would also bite on the
device, and the message names the assertion. `[FAIL] vpk  CRC mismatch for
materials/selftest/test.vmt` means the VPK writer/reader disagree — not that
your download is bad.

---

## Tier 1 — `check`: predict the launch

```sh
python3 -m l4d1ctl check                       # human readable, exit 1 if it would fail
python3 -m l4d1ctl check --json                # same, for the launcher UI
python3 -m l4d1ctl check --engine ./hl2_linux --map c1m1_hotel
```

Checks run in the order the engine hits them, because the first thing that is
wrong is the thing you will see:

| Check | The engine would have said | Typical cause |
| --- | --- | --- |
| `engine.binary` | (nothing, or the shell's `No such file`) | wrong `--engine` path |
| `engine.arch` | `Exec format error` | x86 build staged for an arm64 phone |
| `gamedir` | `Couldn't find gameinfo.txt` | wrong `--gamedir`, unimported content |
| `gamedir.writable` | crash on first cfg/save write | read-only mount, `/sdcard` perms |
| `gameinfo` | `Unable to find gameinfo.txt` | missing or hand-broken `gameinfo.txt` |
| `search.game` | `Unable to find <path>` per mount | content folder moved |
| `module.dir` / `module.client` | `Unable to load client DLL` **(fatal)** | module never installed |
| `module.*.interface` | `CreateInterface` returns NULL, then a NULL deref | module built from stale `public/` |
| `content` / `content.vpk` | `Host_Error: Couldn't spawn server` / VPK read error | truncated or placeholder VPK |
| `argv.exec` | `exec: couldn't exec l4d1_identity.cfg` | cfg not generated yet |

Every failure carries a `->` line with the command that fixes it. Exit code is
`1` when any check is blocking, so it can gate a launch script.

The architecture check compares the module against **the machine running
`check`**. When you are staging an arm64 build on an x86 laptop, tell it what
you are aiming at:

```sh
L4D1_TARGET_MACHINE=aarch64 python3 -m l4d1ctl check --json
```

---

## Testing a resource download

Nothing here bypasses ownership: every route needs a Steam account that owns
Left 4 Dead, and the credentials go to Valve's own tool, never to this one.

**1. Look before you leap.** All provider commands print exactly what they will
run until `--run` is passed:

```sh
python3 -m l4d1ctl fetch --provider steamcmd --user <you>            # prints argv
python3 -m l4d1ctl fetch --provider depotdownloader --user <you> --dry-run
python3 -m l4d1ctl import --from-steam --dry-run                     # local copy
```

**2. Run it.** Expect the provider itself to ask for the password and Steam
Guard; that is the normal path.

```sh
python3 -m l4d1ctl fetch --provider steamcmd --user <you> --run --yes
```

**3. Is the result usable?** Three independent answers:

```sh
python3 -m l4d1ctl status                  # files, bytes, VPK count, identity
python3 -m l4d1ctl check                   # will it launch?
python3 -m l4d1ctl vpk --list --limit 0     # can the packs be parsed at all?
python3 -m l4d1ctl vpk --verify-crc scripts/talker.txt   # spot-check a CRC
python3 -m l4d1ctl content                    # are the boot assets actually in the content?
```

A healthy L4D1 content folder is several GB with most files inside
`*_dir.vpk` + `*_000.vpk` parts; loose `maps/` is usually empty. `check` failing
on `content` with "no game content" almost always means the provider wrote into
a different folder than `--gamedir`.

**Failure modes worth recognising**

| Symptom | Meaning |
| --- | --- |
| provider exits 0, `check` says no content | it downloaded depots you did not ask for, or into another dir |
| `Invalid Password` / `Steam Guard` loop | provider login, not this tool; nothing is stored |
| provider dies part-way | rerun the same command: steamcmd re-validates, DepotDownloader resumes |
| `status` shows ~0 MiB but many files | placeholders from an interrupted download |
| `disk full` | the engine fails *late* on a full disk; `check` warns under 64 MiB free |

**Testing without Steam at all:** the archive route exercises the same
verification code paths.

```sh
python3 -m l4d1ctl pack --source ~/some/left4dead --output /tmp/xfer.tar.gz
python3 -m l4d1ctl unpack /tmp/xfer.tar.gz --gamedir /tmp/target/left4dead
```

`selftest --stage archive` does this round trip automatically, and
`--stage depot` verifies the imported folder.

---

## Tier 2 — `launch --run`: capture, then explain

Plain `launch` replaces this process with the engine (`exec`), so nothing can be
captured. `--run` starts it as a child instead:

```sh
python3 -m l4d1ctl launch --run --map c1m1_hotel
python3 -m l4d1ctl launch --run --timeout 120          # stop it after 2 min
python3 -m l4d1ctl launch --run --no-check             # skip tier 1
python3 -m l4d1ctl launch --run --force                # launch despite blocking checks
```

It prints the tail of the engine output (40 lines — the answer is always near
the end), writes the full log to `left4dead/l4d1_port/logs/launch-<stamp>.log`,
and prints a triage:

```
triage: launch failed: undefined symbol
  [FATAL] undefined symbol
        the module was built against different engine headers than the engine binary.
        > undefined symbol: _ZN11CBasePlayer...
        -> rebuild game code against this engine's public/ (`./waf clean && ./waf build`)
  log: left4dead/l4d1_port/logs/launch-20261110-102430.log
```

The rules cover what actually goes wrong, in practice:

| Log line | Verdict | Meaning / fix |
| --- | --- | --- |
| `Unable to load client DLL` | fatal | module missing, wrong arch, or not a shared object |
| `undefined symbol: …` | fatal | engine/module ABI mismatch — rebuild both from one tree |
| `wrong ELF class` / `Exec format error` | fatal | CPU architecture mismatch |
| `Host_Error: …` | fatal | engine aborted; the message names the subsystem |
| `signal 11` / `Segmentation fault` | fatal | crash; check the last subsystem printed |
| `Could not find gameinfo` | fatal | `-game` name does not match the folder |
| `Error parsing … .vpk` | fatal | truncated pack; re-download |
| `Memory is corrupt` | fatal | allocator damage, almost always ABI drift |
| `Attempted to create unknown entity type X` | info | **expected** with the HL2 bootstrap. Collected and deduped into one list — that list is the checklist for the L4D game code |
| `bink` / `.bik` | warn | intro video unsupported (open task) |
| `SteamAPI_Init` | warn | expected with `-nosteam` |
| `Cvar_Get: cvar already exists` | warn | duplicate registration, harmless for one launch |
| `Couldn't exec <cfg>` | warn | a `+exec` config was missing |

A non-zero exit with no recognised line is still reported, with the last log
line attached — silence is never treated as success.

---

## On the device (adb)

```sh
adb push hl2_linux /data/local/tmp/srceng/
adb push left4dead  /sdcard/srceng/left4dead          # or let the launcher fetch it
adb shell "cd /data/local/tmp/srceng && ./hl2_linux -game left4dead -nosteam -insecure 2>&1 | head -100"
adb pull /sdcard/srceng/left4dead/l4d1_port/logs/     # engine logs, crash dumps
```

If the phone has no Python, run `check` on the PC against the staged folder and
declare the target architecture (`L4D1_TARGET_MACHINE=aarch64`); everything it
inspects is on disk, so the verdict is the same. Android ABI names work too:
`L4D1_TARGET_MACHINE=arm64-v8a`.

That is the normal case here, not the exception: the NDK is an x86_64 host
toolchain, so the arm64 build comes from CI and is verified from a host. See
[BUILDING.md](BUILDING.md).

---

## What to run after a change

| You changed | Minimum |
| --- | --- |
| anything in `l4d1ctl/` | `scripts/test-tooling.sh` |
| `game/*/wscript`, game code | `gh workflow run "Android build"`, `inspect` the artifact, `check`, `launch --run --map c1m1_hotel` |
| `scripts/build-android.sh`, workflow YAML | a manual `Android build` run; read `build/config.log` from the logs artifact |
| `launcher/portcore`, `launcher.cpp` | `scripts/test-portcore.sh`, then a real launch |
| `gameinfo.txt` / mods | `check`, then `launch --run` with a mod enabled and disabled |
| VPK handling | `selftest --stage vpk`, `vpk --verify-crc`, then a real pack |

## What none of this can catch

The tiers verify files, formats, argv and the engine's own startup diagnostics.
They cannot see: whether the GL driver gives you a context, whether touch input
maps to movement, whether a specific map's BSP parses all the way through, or
whether Steam auth succeeds on your account. Those still need a real launch on
a real device — and when one of them fails, `launch --run` is the thing that
turns the failure into a sentence.
