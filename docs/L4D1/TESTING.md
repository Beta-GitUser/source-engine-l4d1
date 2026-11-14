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

## Is `scenes/scenes.image` even a scene image?

The engine's complaint names the file and nothing else, so the file itself has to
answer. `l4d1ctl scene` reads it straight out of the pack and prints what is
actually in the header: the four-byte id, the version, how many scenes and strings
it claims, whether those claims fit inside the bytes on disk, and the first
entries and strings. It takes a game directory, a `*_dir.vpk`, or an extracted
file, and prefers a loose copy over the pack exactly as the engine does.

```sh
python3 -m l4d1ctl scene /storage/emulated/0/srceng/left4dead
python3 -m l4d1ctl scene /storage/emulated/0/srceng/left4dead/pak01_dir.vpk --json
python3 -m l4d1ctl scene ~/scenes.image --write /tmp/copy.image
```

With no repository on the device, `scripts/scene-image.py` does the same thing
with nothing but `python3` — it carries its own small VPK reader:

```sh
python3 scene-image.py /storage/emulated/0/srceng/left4dead
```

Exit code is 0 when the image is one this build would accept, 1 when it is not
(a scene image from another version, a truncated entry table, zeros where the
header should be), 2 when there is nothing to read.

This build wants id `VSIF` — the bytes `56 53 49 46`, and note that `MAKEID`'s
parameters are reversed so `MAKEID('V','S','I','F')` is `0x46495356` — and version
`2`. **L4D1's own file is version 3** with an identical header, entry table and
summaries; the tool verifies that by walking all 5877 entries and their summaries
rather than assuming it, and the engine accepts version 3 on the same check
(`CSceneFileCache: … is version 3, reading it as version 2`).

The verdict distinguishes the failures that look identical in the engine log:

* **an older or newer version** — the file is a scene image, the engine's check is
  just stricter than the content;
* **a header that does not hold together** — the entry table or an entry points
  past the end of the file, i.e. the copy in the pack is truncated;
* **zeros, gzip, or LZMA** — not a scene image at all, so suspect the pack read
  rather than the format.

## What is actually inside a pack

When a name "is not in the pack", three different things could be true, and they
have different fixes: the tree has no such folder, the tree spells the entry
differently, or the entry's bytes are not there. `scripts/vpk-list.py` shows all
three without the engine.

```sh
python3 pyscripts/vpk-list.py left4dead/pak01_dir.vpk              # counts, folders
python3 pyscripts/vpk-list.py left4dead/pak01_dir.vpk --folders    # every folder
python3 pyscripts/vpk-list.py left4dead/pak01_dir.vpk --filter surfaceproperties
python3 pyscripts/vpk-list.py left4dead/pak01_dir.vpk --strings 60 # tree as stored
python3 pyscripts/vpk-list.py /storage/emulated/0/srceng           # every pack
```

That last form is the one that answers "where does this game keep its `scripts/`":
one line per `*_dir.vpk` under the root, with its entry count and whether it has a
`scripts/` folder at all, plus `--filter` applied across all of them.

```
3 pack(s) under /storage/emulated/0/srceng:

  hl2/hl2_misc_dir.vpk: vpk v1, 9134 entries, 412 under scripts/
    top folders: materials, sound, models, scripts
  left4dead/pak01_dir.vpk: vpk v1, 20869 entries, no scripts/ folder
    top folders: materials, models, sound, particles
  platform/platform_misc_dir.vpk: vpk v1, 2210 entries, no scripts/ folder
    top folders: resource, materials
```

A version 2 pack is a second trap: its header is 28 bytes instead of 12, but
`tree_size` is still at offset 8. Reading the size from after the extra fields reads
the first bytes of the tree and truncates the index — six real packs here went from
thousands of entries to a few hundred, and what looked like "this pack has no
`scripts/` folder" was the reader stopping early. Both the package reader and the two
standalone ones now share that arithmetic and are tested against `build_vpk_v2`.

`--strings` exists because a path is never one string in a VPK tree: it is stored
as extension, then folder, then name, and a search for `scripts/surfaceproperties.txt`
looks for bytes that are not there even when the file is. The parts of the name are.

`--extract PATH --out FILE` writes one entry out byte for byte, following it into
its part file if that is where the data lives — useful when the file the engine
wants exists in some other game's pack:

```sh
python3 pyscripts/vpk-list.py garrysmod/garrysmod_dir.vpk \
    --extract scripts/surfaceproperties.txt --out /tmp/surfaceproperties.txt
```

A `pak01_000.vpk` data part is refused by name: parts have no header and no index,
their first bytes are whatever file starts there.

## Is the surface property manifest there, and does the chain behind it work?

`l4d1ctl surface` answers the three questions separately, because they fail
separately: is the file in the content at all, would the engine's reader get
anything out of it, and does each file it names exist and parse.

```sh
python3 -m l4d1ctl surface /storage/emulated/0/srceng        # or --root, --json
```

```
content root: /storage/emulated/0/srceng

manifest:  scripts/surfaceproperties_manifest.txt
           pak01_dir.vpk, 551 bytes, exact spelling

as loaded: openable, non-empty, 551 bytes
           root name 'surfaceproperties_manifest' (the engine never compares it)

it names 1 file(s):
  [  ok  ]  scripts/surfaceproperties_left4dead.txt
            pak01_dir.vpk, 31817 bytes
            44 surfaces: default, solidmetal, wood, ...

verdict: the manifest is there and the chain behind it resolves (44 surfaces)
```

With no repository on the device, `scripts/surface-manifest.py` does the same thing
with nothing but `python3`, carrying its own VPK reader:

```sh
python3 pyscripts/surface-manifest.py /storage/emulated/0/srceng
```

Exit code 0 when the chain resolves, 1 when the manifest exists but something
behind it is missing or broken, 2 when there is no manifest in the content.

A loose file is only visible to the engine if its folder is on a search path —
`left4dead/`, `hl2/`, `platform/` and the DLC folders are, the directory above them
is not. So the report prints where the file really is, and says so when that is
somewhere the engine never looks:

```
manifest:  scripts/surfaceproperties_manifest.txt
           a loose file at garrysmod/scripts/surfaceproperties_manifest.txt, 595 bytes
           note: 'garrysmod/' is not one of the folders the engine searches
           (left4dead/, hl2/, platform/), so it cannot see this file there

verdict: the manifest is there but does not work as intended
  - it sits in 'garrysmod/', which is not a folder the engine searches, so it is
    invisible to the load

The engine looks in the game directory, and 'garrysmod/' is the game's sibling
rather than part of it. Either install the game's own scripts/ folder, or copy
what this needs into it:
  mkdir -p left4dead/scripts && cp garrysmod/scripts/surfaceproperties*.txt left4dead/scripts/
```

A file that resolves but cannot be seen is a failure, not a success: the exit code
is 1, because the engine's load will still fail on it.

Both print what they actually read before saying anything about the manifest — one
line per `*_dir.vpk` with its version, entry count and how many entries are under
`scripts/`, and the loose file count:

```
pack:      left4dead/pak01_dir.vpk — vpk v1, 30124 entries, 96 under scripts/
loose:     1421 files
```

A `0 entries` there means the reader could not get into the index, not that the pack
is empty. And when no `scripts/surfaceproperties*` shows up at all, they search the
dir file's raw bytes for the name, which does not trust the index: names live in the
tree, so bytes without an index entry means the reader is wrong, while no bytes at
all means the content really has none. Passing a `pak01_000.vpk` data part is refused
by name — only the `_dir.vpk` has an index.

The report separates the failures that the engine log renders as one line each:

| what it prints | what it means |
| --- | --- |
| `not in any loose directory or *_dir.vpk` | the content has no manifest; it lists the `scripts/surfaceproperties*` files that *are* there, and which fallback this branch will load |
| `0 bytes` | it exists and the engine cannot read it — the second and last way `LoadFromFile` fails |
| `missing {` | a bare list of names instead of `NAME { … }`: loads, contributes nothing |
| `subkeys not named 'file'` | the loop warns `bogus file type` and skips that entry |
| `[absent]` under a named file | the manifest points at a file this content does not carry |
| `spelled …` on a loose file | only the case-folded second look finds it; inside a pack, case never matters |

## `Unable to load manifest file` has exactly two causes

The loader is `KeyValues::LoadFromFile` (`tier1/KeyValues.cpp`). It returns false in
two places and nowhere else:

- `filesystem->Open( name, "rb", "GAME" )` returned no handle — the name does not
  resolve through the GAME search path;
- it opened, but `ReadEx` returned 0 — a zero-byte file, or a read that gave nothing.

Parsing is not a third cause: `KeyValues::LoadFromBuffer` reports syntax errors
("missing {", "got EOF instead of keyname") and still returns `true`. A malformed
manifest therefore loads, produces warnings, and lists no files.

That splits the diagnosis in half, and the halves have opposite fixes:

| log says | meaning | next step |
| --- | --- | --- |
| `cannot be opened through the GAME search path` | name resolves nowhere — absent, or the search path does not include the pack | read the search path printed on the next line |
| `opens but is 0 bytes long` | the index lists it, the data is not there | check the entry's size in `vpk list` |

The search path is printed because it distinguishes the two sub-cases of the first:
`left4dead/pak01_dir.vpk` present in the list means the pack is mounted and the file
really is missing; absent means the mount came later than physics init.

## What the manifest is, and where it lives

`scripts/surfaceproperties_manifest.txt` is not generated by the engine. It is a
KeyValues index cooked into the game's content like any other `scripts/` file:

```
surfaceproperties_manifest
{
	"file"		"scripts/surfaceproperties_left4dead.txt"
}
```

Everything about it is optional except the layout: the root key name is never
checked, and each subkey must be named `file` (anything else draws
`bogus file type` and is skipped). Its only purpose is to let several
`surfaceproperties_*.txt` files stack — base game, DLC, mod — without anyone
editing Valve's copy, and the physics parser will not read the same file name
twice.

Where it sits follows the surfaces it describes: in HL2-era installs it is
`hl2/scripts/surfaceproperties_manifest.txt` (TF2 ships none of its own and loads
HL2's through the `hl2` search path), and in the L4D-era games it is in the game's
own pack — `left4dead/pak01_dir.vpk` under `scripts/`, next to
`scripts/surfaceproperties_left4dead.txt`.

## Is the file there, and is it spelled the way the engine asks?

`l4d1ctl find` answers both, without the engine: it searches the loose files and
every `*_dir.vpk` under a content root for a path, and reports the spelling it
found. Case matters once the content is on Android and inside a VPK; Valve's data
was authored on Windows, where it never did.

```sh
python3 -m l4d1ctl find scripts/surfaceproperties_manifest.txt
python3 -m l4d1ctl find scripts/surfaceproperties_left4dead.txt --root /storage/emulated/0/srceng
```

```
looking for: scripts/surfaceproperties_manifest.txt
under:       /storage/emulated/0/srceng

[exact]  scripts/surfaceproperties_manifest.txt
          in pak01_dir.vpk
```

`--cat N` prints the first N lines of whatever it found, which is the difference
between "the file is there" and "the file is there and parses".

`[exact]` exits 0, a `[case]` near miss exits 1, nothing at all exits 2. The
engine now does the same thing for the surface property manifest: if the name as
written does not load, it looks for it again with case folded away, logs the
spelling it found, and loads that. A manifest naming a file the content does not
carry is a warning, not a fatal error.

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
* **`scripts/surfaceproperties_manifest.txt` missing** — L4D1 predates the
  manifest. The game code falls back to the pre-manifest files
  (`scripts/surfaceproperties.txt`, `_ep2`), and only warns if none of them exist.
  Before that fallback, a complete L4D1 install died here with
  `Unable to load manifest file` and a `##### stack` under it.
* **A scene image too short to hold a header** — same treatment, same reason.

When a version 3 image is *rejected*, the log now says which check failed:

```
CSceneFileCache: scenes/scenes.image is version 3, but its layout does not check
out: entry 4211 has data 900224+118 outside 1400655 bytes
```

Only structural overlaps reject an image — offsets leaving the file, an
unterminated string. A sound id outside the string pool is reported as advisory
and costs those scenes their sound, nothing more.

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
