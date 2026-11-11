# Mining the retail modules

`l4d1ctl mine` reads a shipped `client.dll` / `server.dll` and lists what the
binary *declares*: interface versions, network properties, data tables, convar
names, entity classnames, asset paths.

It exists because the SDK cannot answer those questions. The SDK tells you how
to write a `CBasePlayer`; it does not tell you that L4D1's client asks for
`VClient017`, nor what the infected network properties are called. The shipped
module is the only authority on both, and getting them wrong is not a subtle
bug — the engine refuses to load the module.

## What this is, and what it is not

There is a line, and it is worth being precise about it:

| | |
| --- | --- |
| ✅ **Interoperability information** | names, version strings, table names, defaults, exported symbols. You need these to talk to the engine. This tool extracts only this. |
| ❌ **Expression** | turning decompiled pseudocode back into source and shipping that. |

EU Software Directive 2009/24/EC art. 6 permits decompilation to obtain the
information needed for interoperability, and explicitly excludes using it to
develop a program "substantially similar in its expression". Dutch
implementation follows the directive. Steam's subscriber agreement independently
prohibits reverse engineering, subject to those statutory rights.

So: mining declarations is the permitted use and is how the whole Source modding
toolchain has always worked (SourceMod's gamedata files are exactly this idea).
Reconstructing Valve's code from a decompiler is the excluded use — and, for the
reasons in the next section, also the slower way to get a working port.

## Why not decompile the logic instead

Because decompiled MSVC C++ is not source:

- no type information — `*(int *)(param_1 + 0x1c)` where the SDK says `m_iHealth`;
- virtual calls are offset arithmetic, inlining erased the function boundaries;
- tens of thousands of functions per module, and every type you re-derive is an
  unverifiable guess until it misbehaves three subsystems later.

This repository already contains 319 client and 343 server source files of
compiling, licensed game code with the same class hierarchy. The productive
question is what L4D1 *adds*, and that is exactly what `mine` answers.

## Running it

```sh
python3 -m l4d1ctl mine client.dll                  # readable report
python3 -m l4d1ctl mine client.dll --json           # machine readable
python3 -m l4d1ctl mine client.dll --bucket netprops --top 0
python3 -m l4d1ctl mine client.dll --compare server.dll
```

Exit codes: `0` read at least one file, `1` a file was missing or not a PE,
`2` bad arguments.

### The buckets

| Bucket | What it is | Why it matters |
| --- | --- | --- |
| `exports` | export directory | must include `CreateInterface`, or nothing loads |
| `interfaces` | `VClient017`-style strings | the versions your modules must advertise |
| `datatables` | `DT_*` | send/recv table names; the network layout |
| `entities` | classnames | the shopping list of entities a map can ask for |
| `netprops` | `m_*` | property names that must match on both sides |
| `convars` | prefix families | behaviour switches players and configs expect |
| `assets` | paths | which data files the port must find or convert |
| `identifiers` | everything else | commands, keys, error strings |

The classifiers are filters, not proofs: a name can be misfiled, and a few
thousand candidates is the intended output. Read them, don't parse them.

## Reading the first real run

A first pass against retail L4D1 `client.dll` (5.8 MB, 32-bit) is instructive
about what the filters do and do not promise:

| Looked like | Was really | Fix |
| --- | --- | --- |
| `ABGR8888`, `BGR565`, `IMAGE_FORMAT_UVLX8888` | image-format enums | interface versions must end in `0xx`, not any three digits |
| `ACT_PRIMARYATTACK_XM1014`, `EjectBrass_556` | animation activity names | the three digits must not be the tail of a longer number |
| `Boomer`, `TANK`, `ITEM_FLAG_EXHAUSTIBLE` | display names, enum flags | entity classnames are lowercase snake_case |
| `Mp_3`, `R_SHOULDER`, `VGui_DrawHud` | `Mp_`/`R_` prefix collisions | convars are lowercase |
| `Invalid surface property. …surfaceproperties_manifest.txt` | a sentence | asset paths contain no spaces |
| `ADVAPI32.dll`, `KERNEL32.DLL` | import-table noise | not assets; dropped |

Each of those is now a row in `l4d1ctl/tests/fixtures/mine_l4d1_sample.txt`, so
the next regex change cannot quietly reintroduce them.

## The diff that matters: this SDK vs the retail build

Every interface string is compared against the 162 versions this tree's own
headers declare (`scripts/gen-sdk-interfaces.sh` regenerates the list; a test
fails if it drifts). Both naming conventions are read — `VCLIENT_INTERFACE_VERSION`
and `INTERFACEVERSION_SERVERGAMEDLL` — because the server side uses the second,
and missing it would hide exactly the interfaces the engine checks before it
will run a server module.

A family may declare several versions at once (`ServerGameDLL008`, `009`, `010`),
so a name is a *mismatch* only if it is outside the whole set, not merely "not
the last one parsed". The report splits them three ways:

- **matching** — same family, same version: nothing to do;
- **version mismatch** — the module will not load until our headers request the
  version the retail build asks for. On the first real run, `MatSystemSurface006`
  against this SDK's `008` and `MaterialSystemHardwareConfig013` against `012`;
- **unknown** — no such family here, i.e. L4D-only interfaces such as
  `IMATCHEXT_L4D_INTERFACE_001`. These are the work the SDK has never had.

The middle category is the one that decides whether the port runs at all — it is
cheap to mine and expensive to discover at launch ("Your client is out of date").

## Using the output

Do not commit the inventory wholesale. Commit the *conclusions* — the interface
version your client must expose, the property names your `DT_` tables must
carry — and keep the raw JSON outside the repository. The port needs to agree
with those declarations; it does not need to republish Valve's binaries'
contents.

A useful first pass:

```sh
python3 -m l4d1ctl mine bin/client.dll --bucket interfaces --top 0 > interfaces.txt
python3 -m l4d1ctl mine bin/server.dll --bucket netprops  --top 0 > netprops.txt
```

then, in the code, work down `interfaces.txt` until the engine stops rejecting
the module, and use `netprops.txt` as the checklist for `DT_` definitions.

## Which depot

`client.dll` and `server.dll` live in **depot 503** (the Windows content depot),
which is a few megabytes. Appid 500's general depot is the large one and is not
what you want when the question is "what does the client declare?".

```sh
# depotdownloader can restrict to one depot and to a path within it
python3 -m l4d1ctl fetch --provider depotdownloader --user <you> \
    --depot 503 --file 'bin/(client|server)\.dll$' --run
```

`steamcmd` has no depot filter and will pull everything; the dry run says so
rather than letting you discover it at 90%.

## Bringing the download across from proot

If the depot download runs inside a proot/distro Debian, the files land in the
proot filesystem, which the Termux side can see but the *Android* side cannot.
Bridge it once, then work from shared storage:

```sh
# inside the proot distro, after the download finished
cp <out>/bin/client.dll <out>/bin/server.dll /sdcard/Download/

# back in Termux
python3 mine.py /sdcard/Download/client.dll
```

`l4d1ctl import` understands the shape a depot download produces - the app
folder with `bin/` next to `left4dead/` - and detects the content level itself,
so pointing it at `Left 4 Dead/` works as well as `Left 4 Dead/left4dead/`.

Two things it will tell you rather than do silently:

- a **partial** download (only `bin/`) imports nothing, prints that the depot
  mixup is usually depot 503 vs the content depots, and exits 1;
- `platform/` is a *sibling* of the content folder, so it is not imported. If
  the UI later shows missing fonts or platform resources on device, that is the
  first place to look.

## On a phone

The tool is standard library only — no `pefile`, no `pip` — so it runs in
[Termux](https://termux.dev). **You do not need the repository.** Pick whichever
fits:

```sh
pkg install python

# 1. One file. mine.py has no package imports and its own entry point.
curl -O https://raw.githubusercontent.com/Beta-GitUser/source-engine-l4d1/\
arena/410ac3e6-source-engine-l4d1/l4d1ctl/mine.py
python3 mine.py /sdcard/Download/client.dll

# 2. Just the tooling directory: 2.8 MB instead of a 357 MB working tree.
git clone --depth 1 --filter=blob:none --sparse \
    https://github.com/Beta-GitUser/source-engine-l4d1
cd source-engine-l4d1
git sparse-checkout set l4d1ctl
python3 -m l4d1ctl mine /sdcard/Download/client.dll
```

A full clone is unnecessary for any of the analysis commands; the engine sources
are what make it large. (A plain `git clone --depth 1` is ~90 MB of pack — still
a lot on mobile data.)

The Android engine build still cannot happen on the device (the NDK is an
x86_64 host toolchain, and the phone has no x86_64 to run it on) — that stays in
GitHub Actions. Analysis, fetching and inspection are all fine on-device.

## Limitations

- Packed or SteamStub-protected images yield few strings; the report says so
  rather than pretending to be empty.
- 64-bit images are called out: retail L4D1 is 32-bit, so a 64-bit file is
  probably the wrong file.
- It reads declarations only. Nothing here tells you what a function *does*.
