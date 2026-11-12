# Game code: map hosting and a walker (bootstrap)

This is roadmap phase 2, first slice: get the engine to **host a map** and let
you **walk and jump on it**, using HL2 game code as the body until L4D's own
game code exists.

## What a "game module" is here

The engine is only half of Source. Everything gameplay lives in a shared object
loaded from the game directory:

| Module | Loaded by | Interface it must export |
| --- | --- | --- |
| `client` | `engine/cdll_engine_int.cpp:1727` - `LoadModule( "client", "GAMEBIN", false )` | `VClient017` (`public/cdll_int.h:796`) |
| `server` | the server side, same `GAMEBIN` search path | `ServerGameDLL010` (`public/eiface.h:457`) |

`GAMEBIN` resolves to `<gamedir>/bin`, so `left4dead/bin/client.so` and
`left4dead/bin/server.so` are what `-game left4dead` boots. On Android the files
are `libclient.so` / `libserver.so` and live beside the engine binary instead;
both spellings are accepted by the tooling (`l4d1ctl inspect`,
`module_name_for`, `check`).

## Bootstrap: build HL2 game code into `left4dead/bin`

Both game `wscript` files now register a `left4dead` target that compiles the
HL2 game sources (that is what "base off of hl2 codebase" means in practice -
no new C++ yet):

```sh
git submodule init && git submodule update     # thirdparty, ivp, lib
./waf configure -T release --build-games left4dead
./waf build
# -> game/left4dead/bin/{client,server}.so
```

On Android the same game code is built by CI, because there is no arm64 host
NDK (see [BUILDING.md](BUILDING.md)). The layout differs: the modules come out
as `libclient.so` / `libserver.so` next to the engine, not in `<gamedir>/bin`.

```sh
gh workflow run "Android build"
gh run download <run-id> -n android-aarch64-libraries
python3 -m l4d1ctl inspect out/libclient.so    # ELF 64-bit AArch64 ... VClient017
```

If you already have an HL2 build, install it instead of rebuilding:

```sh
python3 -m l4d1ctl module install --from game/hl2/bin --dry-run   # look first
python3 -m l4d1ctl module install --from game/hl2/bin --link      # hardlinks
python3 -m l4d1ctl module status
```

`module install` copies only game modules (client/server/gameui), refuses to
replace a different build without `--force`, is idempotent, and reports the
binary format of each file (`ELF 64-bit AArch64 shared object`) so a mismatched
architecture is visible before the engine fails to load it.

## What this already gives you

* **Map hosting.** `map c1m1_hotel` loads the BSP, spawns the world, physics,
  lights, props and brush entities; the server runs a listen server.
* **Walking and jumping.** HL2's `CHL2_Player` plus `CGameMovement`
  (`game/shared/gamemovement.cpp`) gives full Source movement: ladders, duck,
  sprint-style accel, step-up, air control - all of it, unchanged.
* **View, sound, particles, ragdolls, physics props** from the shared code.

## What it does not give you (yet)

* **No survivors, no Director, no horde.** L4D's game rules do not exist in the
  HL2 module: no `TerrorPlayer`, no flow distance, no panic events.
* **No weapons.** HL2's weapon scripts live under `hl2/`, which the L4D1
  `gameinfo.txt` does not mount. The player spawns unarmed.
* **HUD mismatch.** HL2 HUD elements will draw with L4D's UI scheme resources;
  expect layout weirdness, not crashes.
* **L4D-specific entities are skipped.** See below - this is by design, not a
  failure.

## Unknown and "obsolete" entities: nothing folds

The server only knows classnames registered with `LINK_ENTITY_TO_CLASS`. A map
entity with any other classname goes through `CreateEntityByName`
(`game/server/util.cpp:164`), which warns:

```
Attempted to create unknown entity type <classname>!
```

and returns `NULL`. `MapEntity_ParseAllEntities`
(`game/server/mapentities.cpp`) keeps a spawn list, skips entries whose entity
is `NULL`, and still spawns and activates everything else. Outputs that target a
missing entity name resolve to nothing and log, rather than aborting the map.

So an entity that Hammer's L4D FGD labels obsolete - or that simply does not
exist in HL2 - costs one warning line per instance. That is exactly why the
bootstrap approach is safe to use for testing map compatibility: the map loads,
the unknown parts are inert, and the list of skipped classnames tells us what to
implement next:

```sh
# in the engine console, after loading a map
find_ent ...            # or just read the warning lines in the log

# or let the tooling collect and dedupe them for you
python3 -m l4d1ctl launch --run --map c1m1_hotel
```

`launch --run` writes the log to `l4d1_port/logs/` and prints the deduped list of
skipped classnames. That warning list is the real specification for phase 2b.

## Map intro cutscenes - how they actually work

Short answer: **they are not videos and not a special entity type**. What L4D
does at the start of a chapter is an ordinary in-engine camera sequence:

* `point_viewcontrol` - registered in this tree as `CTriggerCamera`
  (`game/server/triggers.cpp:2839`). It takes over the player's view with
  `Enable()`, moves along its `path_*`/`info_target` stack, then hands the view
  back with `Disable()`.
* `logic_auto` / `logic_relay` / `math_counter` / `scripted_sequence` /
  `instanced_scripted_scene` / `scene` / `scripted_sentence` drive the timing,
  the survivors' animations and the VO.
* The *server game DLL* (the Director) decides when this runs. In our port that
  logic is `l4d_gamerules`/Director code we have not written yet, which is why
  intros simply never start today - the entities are present in the map, but
  nothing triggers them.

What is *not* is also worth knowing:

* The main-menu intro is a **Bink video** (`video/video_bink/`). This fork lists
  "Bink audio support" as an open task in the root README, so treat the movie
  as audio-less/broken until that is addressed. The file itself comes from the
  user's own depot under `left4dead/media/`.
* Anything L4D-specific that *is* custom (survivor spawn logic, weapon/item
  spawners, infected spawn points, commentary hooks) will hit the unknown-entity
  path above. They are the checklist for phase 2b/3, not blockers.

## Ladder from here

1. **This commit.** HL2 module in `left4dead/bin`, map hosting, walker.
2. **Own player.** *Started as a seam, on purpose.* `game/server/left4dead/`
   now exists and builds into the server module (`server_left4dead.vpc` adds it to
   the `left4dead` target only; `L4D_OWN_PLAYER` is defined there and nowhere
   else), and `CHL2_Player::Spawn` calls `L4D_Server_PlayerSpawned()` at the end,
   which logs `L4D bootstrap: server seam active (map …)`. That line is how a log
   answers "is this the bootstrap HL2 module or something of ours?".

   The subclass can wait, and here is why: a **server-only** `CL4DPlayer` would
   declare a new send table, the send table name travels to the client, and the
   client can only build an entity for a netclass it registered. `hl2mp` gets away
   with it because it ships both sides (`DT_HL2MP_Player` plus
   `IMPLEMENT_CLIENTCLASS_DT`). So the class arrives with its client counterpart,
   as written below, not before; the hook changes no netcode.

   The full step remains:
   `class CL4DPlayer : public CBasePlayer` (or `CBaseMultiplayerPlayer` for
   team/4-slot support), only movement + view angles + health, no weapons.
   Client side: `game/client/left4dead/clientmode_l4d.{h,cpp}` +
   `c_l4d_player.{h,cpp}`. New VPCs `server_left4dead.vpc` /
   `client_left4dead.vpc` replace the HL2 includes, then drop the bootstrap
   entries from the wscripts.
3. **Map contract.** Spawn from `info_player_start`, verify the unknown-entity
   warning list against a chapter, and decide what to stub first
   (`weapon_spawn`, `infected_spawn`, `info_survivor_position`).
4. **Game rules.** `l4d_gamerules` with 4 slots, team entity, and a stub
   Director that fires the intro `point_viewcontrol` so cutscenes play.

## Expected friction

* `--build-games left4dead` needs the three submodules initialised (`thirdparty`,
  `ivp`, `lib`); without them nothing in `game/` compiles.
* L4D nav meshes carry flow attributes this tree's nav parser does not know.
  Bots/Director will need that data later; walking does not.
* HL2's client expects `resource/ClientScheme.res` keys that L4D's scheme does
  not define - cosmetic errors in the log are expected.
* If the module is built for another architecture, the engine fails at
  `LoadModule`; `l4d1ctl module status` shows the ELF machine so you can catch
  that before launching.
