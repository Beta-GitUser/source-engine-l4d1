# Roadmap

Phases are ordered so that each one produces something testable on a phone, and
so that the expensive phase (game code) starts only after the cheap phases prove
the asset path actually works.

Status legend: **done** in this branch · **next** · **later**

## Phase 0 — Launcher and content pipeline · done

Deliverables:

* `l4d1ctl` (Python, stdlib only): Steam library discovery, content import from
  your own install, `steamcmd`/`DepotDownloader` driver, archive import for
  phones, generated `gameinfo.txt`, mod manager, offline identity, launch plan,
  `doctor`/`status`.
* `launcher/portcore` (C++, no engine dependencies): `port.cfg` parser and argv
  builder, wired into `launcher/android/main.cpp`.
* Tests: 128 Python tests + 67 C++ checks, runnable without the engine build
  (`scripts/test-tooling.sh`) and in CI.

Exit criterion: `l4d1ctl doctor` reports a verified game directory, `l4d1ctl
launch --print` emits a sane engine command line, mods stage and un-stage
deterministically. **Met.**

## Phase 1 — Engine boots with L4D1 assets · next

Goal: get `-game left4dead` to open the engine's own menus/console with L4D1
content mounted, and load a map far enough to see the world.

Work:

1. Build the engine for the target (Android arm64, or desktop x86_64 first — it
   is much easier to iterate on).
2. Run with the generated `gameinfo.txt` and collect the first 20 failures.
   Expected candidates: missing game DLL target, `resource/*.res` UI scheme
   differences, MDL version, particle (PCF) version, missing shaders.
3. Decide the game-code story (see Phase 2) — the engine will not start a map
   without a `client`/`server` module exporting `VClient017`/`ServerGameDLL010`.

Exit criterion: engine reaches the main menu loop and `map <name>` fails inside
map/game-code init rather than in the file system.

## Phase 2 — A walkable L4D1 map (sandbox game code) · next

Build `game/client` + `game/server` targets (`left4dead_client`,
`left4dead_server`) from the existing shared/HL2 sources, i.e. the smallest game
that can drive a player around a map:

* reuse `C_BasePlayer`, prediction, view, physics props from `game/shared`;
* spawn a survivor-shaped player (L4D1 survivor models via `C_BaseAnimating`);
* no weapons beyond a placeholder, no AI, no Director.

Exit criterion: on a phone, walk around a L4D1 campaign map with correct
geometry, lighting and props. This is the "it is real" milestone.

## Phase 3 — Co-op skeleton · later

* Listen server, 4 survivor slots, bot survivors that follow and shoot
  (NextBot player locomotion already exists in `game/server/NextBot/Player`).
* Common infected as `CAI_BaseNPC`-derived NextBots using the map's nav mesh;
  horde spawner driven by simple distance-from-start pacing.
* Our own protocol only: LAN discovery plus direct connect. Never retail servers.

Exit criterion: two devices finish one campaign chapter with bots.

## Phase 4 — Systems that make it Left 4 Dead · later

* Director: flow distance field over the nav mesh, item/weapon placement,
  panic events, special-infected budget, tension/relax pacing.
* Survivors: health/incap/revive/pills/adrenaline, weapon set (pistols, SMG,
  shotgun, rifle, melee), throwables, mounted guns, safe-room doors.
* Special infected: Smoker, Boomer, Hunter, Tank, Witch — each is a NextBot with
  its own ability state machine.
* HUD: survivor status, item counts, damage direction, incap vignette, versus
  and coop variants.
* Audio: L4D1's dynamic music needs an event-driven music layer, not a playlist.

## Phase 5 — Polish and packaging · later

* Save/restore of campaign progress, chapter transitions.
* Achievements/stat counters locally (engine already runs without Steam).
* Launcher UI: content check, Steam login screen (only if a real Steam client
  integration is added), mod manager screen, touch layout editor.
* Optional, desktop only: link against Valve's redistributable `steam_api` and
  run inside Steam with appid 500 — this is the only legitimate path to real
  tickets, Workshop and multiplayer with Steam friends.

## Known risks

| Risk | Consequence | Mitigation |
| --- | --- | --- |
| MDL/PCF versions differ from what this branch writes | models/particles fail to load | test early (Phase 1), add converters if needed |
| Renderer is DX9/GL-only in this fork | shader gaps on GLES | the fork already targets `--togles`; fix shaders on demand |
| Game code scope | years of work | keep Phases 2–3 narrow; treat Phase 4 as a separate project |
| Reimplementing the Director well | L4D feels generic without it | prototype flow-distance pacing early, iterate on one chapter |
| Content licensing | cannot ship anything | keep launcher fetching from the user's own account |
