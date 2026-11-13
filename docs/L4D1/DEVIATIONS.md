# The deviation ledger

This port reuses Valve's framework, writes the L4D1 delta from scratch, and runs
both on a toolchain nobody at Valve ever used. Some of it is therefore improvised.
That is fine; improvising *silently* is not. Every place where this port knowingly
differs from L4D1 lives here, with what we assumed and what would prove us wrong.

Two rules:

1. **Do not modernise behaviour.** Engine plumbing gets modernised — 64-bit,
   aarch64, bionic, case-sensitive filesystems. Gameplay numbers do not. Any
   gameplay change is a deviation and belongs in this table, not in a commit
   message.
2. **Anything guessed is listed, with the evidence for it.** "It felt right" is
   not evidence; "the convar's default string appears in the binary" is.

## Three tiers of code, and where improvisation is allowed

| Tier | What | Source | Improvisation |
| --- | --- | --- | --- |
| **A. Engine** | `engine/`, `tier0/`, `tier1/`, `mathlib/`, `vphysics/` | this tree | none — it must build; correctness is mechanical |
| **B. Framework game code** | `game/client`, `game/server`, `game/shared` | HL2 SDK base in this tree | adaptation only: same class hierarchy, 2013-era API |
| **C. L4D1 delta** | Director, survivors, special infected, weapons, game modes, L4D-only interfaces | written here | yes — against a known contract, see below |

Tier C is where the writing happens. Its contract is what mining gave us: entity
classnames, netprop names, convar names, interface names, HUD `.res` paths. Those
names are not decoration — they are what L4D1's data files and configs refer to,
and what a future swap to a more faithful implementation would have to match.

There is no legal, public L4D1-era SDK to steer by. Valve published Source SDK
2006/2007 (HL2-era) and 2013; neither contains L4D1's interfaces. So "use the
era-correct headers" is not an option that exists — the improvisation is forced.

## Known deviations

| Area | What this port does | Why | Confidence | Revisit when |
| --- | --- | --- | --- | --- |
| 128-bit atomics | LDXP/STXP CAS in `tier0/threadtools.cpp` | GCC 4.9 aarch64 has no 16-byte CAS, libatomic lacks `__sync_*_16` | high — instructions encode, semantics are LL/SC | engine links and runs a TSLIST stress |
| NEON float64 reinterprets | `memcpy` bit casts in `common/sse2neon.h` | GCC 4.9's `arm_neon.h` predates them | high — bit-exact by construction, tested | never; modern toolchains just use real intrinsics |
| Interface versions | our modules request **this SDK's** versions, not L4D1's | both sides are ours, so they agree by construction; see [INTERFACES.md](INTERFACES.md) | high for local play | retail interop, or importing 2007-era game code |
| L4D-only interfaces (`GMATCHFRAMEWORK`, `IMATCHEXT_L4D`, `INETSUPPORT`, `VScriptManager`, `VSERVERFOUNDRY`, `VResourceAccessControl`, `NetworkSystemVersion`) | stub or implement minimally, keeping the retail names | no source exists; offline play needs little of it | low — contracts inferred | any code path that turns out to need one |
| `GameUI011` | launcher replaces Valve's main menu | the launcher already owns connect/load/mod UI | high — deliberate design | pause menu, options, achievements screens |
| Netprop wire layout | names taken from the binary, bit layout defined by us | the layout is not recoverable from strings, and both sides are ours | medium — names right, layout ours | demo playback, or retail interop |
| Steam interfaces (`SteamUser013`, `SteamUtils004`, `SteamFriends005`, …) | shim exposes the versions our code asks for, with the user's real identity | this is the port's own Steam layer | high — our code, our contract | anything that consults a version we do not implement |
| Entity/archival behaviour | HL2 SDK base, extended toward L4D1 | no L4D1 source exists | medium — observable behaviour only | a diff that shows drift from retail |
| Server player seam | `L4D_Server_PlayerSpawned()` called from `CHL2_Player::Spawn` under `L4D_OWN_PLAYER`; the player class is still HL2's | a server-only player subclass would need a client netclass for its send table, which the ladder builds later | high for what it does (nothing but log) | any behaviour that differs from HL2 before step 2 lands |
| Holdout / Last Stand mode | inferred from HUD resources (`HoldoutModeScoreboard`, `FinaleMeter`) | mode postdates L4D1's launch, data only | low — mostly guesswork | a config or map that exercises it |
| VPK mounting | a directory search path brings its own `*_dir.vpk` packs with it (`FileSystem_AddLoadedSearchPath`), so a `pak01_dir.vpk` sitting in the game directory is mounted without the gameinfo naming it | in this tree a pack is mounted only when a search path spells it out (`AddSearchPathInternal` dispatches on `.vpk`; `AddPackFiles` scans for `zip%d.zip`). L4D1's gameinfo lists only directories, yet its content is inside `pak01_dir.vpk` — measured on device: pack present, search paths clean, all four boot assets `MISSING`. The retail build must mount it somewhere else; this is the equivalent | high — the on-device log showed the pack unmounted and the assets missing from the same boot | a retail gameinfo that names the pack explicitly, or content that is not packed |
| Write path ID `MOD` | `FileSystem_LoadSearchPaths` registers `m_ModPath` (the directory holding gameinfo.txt) under path ID `MOD` | the engine reads and writes savegames (`host_saverestore.cpp` `MOD_DIR`), `sound.cache` and download lists through that ID, and Valve's later gameinfo stopped registering it — so every call logged `Requested non-existent write path MOD!` (32 per boot) and fell back to that same directory anyway. Older Source builds added it automatically, as their own gameinfo comments still claim | high — the fallback already resolved to the same path | a retail gameinfo that names a dedicated write directory |
| Path case | lookups must work with Valve's mixed case (`Resource/UI/…`) on a case-sensitive filesystem | Android is case-sensitive; Windows was not | unknown yet | any "missing resource" on device — see below |

## The one that will bite first: case sensitivity

Valve's data uses mixed-case paths (`Resource/UI/HUD/BoomerHealth.res`), and it
was authored on Windows where case does not matter. Android filesystems are case
sensitive, VPK lookups are exact-match, and half the paths inside the packs may
not match the case the code asks for. The engine's file search already tries a
few variants; if resources go missing on device, this is the first suspect and
the fix is a case-insensitive fallback in the VPK/filesystem layer, not a mass
rename of the content (which we would have to redo on every content update).

## How to add an entry

If you guess a number, invent an interface, or pick a behaviour because it seems
reasonable, add a row: what you did, why, how sure you are, and what evidence
would settle it. A row with "confidence: low" is honest; a missing row is a bug
waiting to be attributed to the engine.
