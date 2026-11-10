# Feasibility: Left 4 Dead 1 on the nillerusr source-engine fork

Written after auditing this tree (TF2 2018 leak lineage, see the root README).
Every claim below points at the file that decides it, so the conclusion can be
re-checked instead of trusted.

## 1. The honest verdict

There are three very different questions hiding behind "can we run L4D1".

| Question | Answer | Why |
| --- | --- | --- |
| Can the engine read L4D1's data files? | **Mostly yes.** | BSP 19–21, VPK 1 and 2, MDL 46–49, VMT/VTF, particles. L4D1 content is inside those ranges. |
| Can this engine run the retail L4D1 game code? | **No.** | Retail game code is Windows x86, built for the L4D1 engine branch, and is not redistributable. This fork is a different branch, different ABI, and targets ARM/64-bit. |
| Can the *game* be reproduced? | **Yes, but it is a rewrite, not a port.** | L4D1's gameplay (Director, survivors, infected, HUD, weapons) lives entirely in game DLL code that was never released. It has to be rebuilt on top of this engine. |

So: the launcher, content pipeline, mod system and asset loading are real,
tractable work — and that is what this repository now ships. "L4D1 running
exactly as retail" is a multi-year reimplementation project, not a
configuration problem. Saying otherwise is how ports die with 40 open "it
crashes on load" issues.

## 2. What the engine already supports (verified in-tree)

* **Map format.** `public/bspfile.h` accepts `MINBSPVERSION 19` … `BSPVERSION 21`;
  the loader enforces that range in `engine/modelloader.cpp:465` and
  `engine/checksum_engine.cpp:166`. L4D1-era maps are v20, i.e. inside it.
* **VPK packs.** `vpklib/packedstore_internal.h` defines
  `VPK_CURRENT_VERSION 2` and `VPK_PREVIOUS_VERSION 1`, and
  `vpklib/packedstore.cpp:315` accepts the older one. L4D1 ships v1 packs.
* **Models.** `public/studio.h:70` pins `STUDIO_VERSION 49` and carries
  conversion code for older headers (`public/studio.h:3099-3160`, e.g. v47).
  The root README advertises MDL 46–49; L4D1-era models need a smoke test, and
  that is exactly what "walk around a L4D1 map" (roadmap phase 2) is for.
* **Search-path wildcards.** `public/filesystem_init.cpp`
  (`FileSystem_AddLoadedSearchPath`, ~lines 600-740) expands `*` in a
  `gameinfo.txt` search path, mounts both directories and `*_dir.vpk`, and drops
  the `_nnn.vpk` parts when the matching `_dir.vpk` exists. This is the
  mechanism the new mod manager relies on; it is not a hack.
* **NextBot leftovers.** `game/server/NextBot/**` and
  `game/client/NextBot/C_NextBot.cpp` are present (TF2 lineage). They are not
  L4D1's Director, but they are a usable locomotion/behaviour base.
* **No-Steam operation.** The launcher already passes `-nosteam`
  (`launcher/android/main.cpp`), the README advertises working achievements and
  a working server browser without Steam, and `stub_steam/steam_api.cpp` exists
  as a stub library.

## 3. What is missing, and why it is the hard part

* **No L4D1 game code.** `game/client/` and `game/server/` contain `hl2`,
  `hl2mp`, `cstrike`, `dod`, `hl1`, `portal`, `episodic`, `sdk` and shared code.
  There is no `left4dead` target, and no `TerrorPlayer`, Director, survivor,
  infected or weapon-script implementation anywhere in the tree (a repo-wide
  search hits only comments and NextBot leftovers).
* **The retail binaries cannot be dropped in.** They are 32-bit Windows PE
  builds exporting the interface versions of the *L4D1 branch*. This engine
  exports `VClient017` (`public/cdll_int.h:796`), `VEngineClient014`
  (`public/cdll_int.h:188`), `ServerGameDLL010` (`public/eiface.h:457`) and
  `VEngineServer023` (`public/eiface.h:78`). Even on Windows the ABI would not
  match; on Android/ARM there is nothing to load at all. And shipping or
  reverse-engineering those DLLs for redistribution is not on the table.
* **Steam is not a real Steam.** `stub_steam/steam_api.cpp` returns `NULL` from
  every interface accessor, and `CSteamAPIContext::Init()`
  (`public/steam/steam_api.h:532-631`) fails unless *every* interface
  (`ISteamUser`, `ISteamFriends`, … `ISteamVideo`) is non-null. A "spoof your
  SteamID" shim therefore cannot be a 50-line stub: either the whole interface
  set is implemented, or the game code must not talk to Steam at all.
* **Retail multiplayer is out of reach.** Joining Valve's official L4D1 servers
  needs a genuine Steam session plus their engine branch and VAC; no amount of
  local identity spoofing produces a valid session ticket (`ISteamUser::GetAuthSessionTicket`
  in `engine/cl_steamauth.cpp:148-170` is what the server checks). Faking that
  is both technically futile and a fast route to a banned account.

## 4. On the Garry's Mod comparison

GDroid works because Garry's Mod *ships* its game code as Lua plus the retail
`client.dll`/`server.dll` for Windows, and it is the same Source 2013 lineage
that this engine descends from — so the Windows builds run and the Lua layer is
data. L4D1 differs in the two ways that matter: the game code is native C++
(no Lua layer to reuse) and it belongs to an engine branch that this fork is
not. The launcher patterns from GDroid (Steam download inside the app, mod
manager, per-user identity) do transfer, and that is what has been built here.

## 5. Legal boundaries this project keeps

* Content comes only from a Steam account that owns appid 500, fetched by
  Valve's own tooling or copied from the user's own install.
* No game binaries, VPKs, maps, models or sounds are redistributed here.
* No Steam authentication is spoofed or reimplemented: the local name/SteamID/avatar
  is a *display* identity, documented as such in `LAUNCHER.md`.
* The engine's own licence (`LICENSE`, Source SDK 1) still applies: free
  distribution, no commercial use.

## 6. What that means in practice

1. This fork can become **a Source engine that boots with L4D1 assets** — that
   is a realistic, testable milestone and the roadmap starts there.
2. Turning it into **Left 4 Dead** means writing the game: survivors, horde,
   special infected, Director pacing, weapons, HUD, save/restore, nav flow.
   Expect it to be the largest part of the project by an order of magnitude.
3. Co-op is achievable **on our own protocol** (LAN/self-hosted, our own client
   and server builds). It will never be compatible with retail L4D1 clients.
