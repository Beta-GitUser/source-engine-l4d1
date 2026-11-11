# What L4D1 asks the engine for

Extracted from the shipped modules with `l4d1ctl mine`, then diffed against the
versions this tree's own headers declare (`scripts/gen-sdk-interfaces.sh`, 182
entries). Reproduce with:

```sh
python3 -m l4d1ctl mine client.dll --bucket interfaces --top 0 > client.txt
python3 -m l4d1ctl mine server.dll --bucket interfaces --top 0 > server.txt
python3 scripts/interface-report.py client.txt server.txt
```

The raw lists are in `l4d1ctl/tests/fixtures/retail_*.txt`, and
`scripts/interface-report.py --check-doc docs/L4D1/INTERFACES.md <lists>` fails if
this page stops agreeing with them.

Totals: client 77 interfaces (50 match, 19 mismatch, 8 unknown);
server 78 (48 match, 20 mismatch, 10 unknown).

## What a version mismatch means — and does not

A mismatch means "if the retail module were loaded into this engine, it would ask
for an interface this engine does not answer". It does **not** mean the port
cannot be built.

Our engine and our game modules are compiled from the same headers, so they agree
by construction. Retail version numbers matter in three cases only:

1. **Loading retail modules** — not the plan; the port builds its own.
2. **Networking with unmodified copies** — matters only for retail multiplayer.
3. **Reusing era-appropriate source**: any 2007/2008-era game code expects the
   versions below rather than our 2013 SDK's.

So the mismatch tables are a map of an era. The *to-do list* is the third
category: interfaces this tree has never had.

## Version mismatches

### client.dll

| Interface the build asks for | This SDK declares |
| --- | --- |
| `MatSystemSurface006` | `MatSystemSurface005`, `MatSystemSurface008` |
| `MaterialSystemHardwareConfig013` | `MaterialSystemHardwareConfig012` |
| `QueuedLoaderVersion001` | `QueuedLoaderVersion004` |
| `STEAMAPPS_INTERFACE_VERSION003` | `STEAMAPPS_INTERFACE_VERSION007` |
| `STEAMUSERSTATS_INTERFACE_VERSION007` | `STEAMUSERSTATS_INTERFACE_VERSION011` |
| `StaticPropMgrClient005` | `StaticPropMgrClient004` |
| `SteamFriends005` | `SteamFriends015` |
| `SteamMatchMaking007` | `SteamMatchMaking009` |
| `SteamNetworking003` | `SteamNetworking005` |
| `SteamUser013` | `SteamUser018` |
| `SteamUtils004` | `SteamUtils007` |
| `VClient016` | `VClient017` |
| `VEngineCvar007` | `VEngineCvar004` |
| `VEngineRenderView013` | `VEngineRenderView014` |
| `VFileSystem018` | `VFileSystem022` |
| `VMaterialSystem080` | `VMaterialSystem081` |
| `VModelInfoClient004` | `VModelInfoClient006` |
| `VSoundEmitter003` | `VSoundEmitter002` |
| `VStudioRender026` | `VStudioRender025` |

### server.dll

| Interface the build asks for | This SDK declares |
| --- | --- |
| `MatSystemSurface006` | `MatSystemSurface005`, `MatSystemSurface008` |
| `MaterialSystemHardwareConfig013` | `MaterialSystemHardwareConfig012` |
| `PlayerInfoManager001` | `PlayerInfoManager002` |
| `QueuedLoaderVersion001` | `QueuedLoaderVersion004` |
| `STEAMAPPS_INTERFACE_VERSION003` | `STEAMAPPS_INTERFACE_VERSION007` |
| `STEAMREMOTESTORAGE_INTERFACE_VERSION002` | `STEAMREMOTESTORAGE_INTERFACE_VERSION013` |
| `STEAMUSERSTATS_INTERFACE_VERSION007` | `STEAMUSERSTATS_INTERFACE_VERSION011` |
| `ServerGameDLL005` | `ServerGameDLL003`, `ServerGameDLL008`, `ServerGameDLL009`, `ServerGameDLL010` |
| `SteamFriends005` | `SteamFriends015` |
| `SteamGameServer009` | `SteamGameServer012` |
| `SteamMatchMaking007` | `SteamMatchMaking009` |
| `SteamNetworking003` | `SteamNetworking005` |
| `SteamUser013` | `SteamUser018` |
| `SteamUtils004` | `SteamUtils007` |
| `VEngineCvar007` | `VEngineCvar004` |
| `VFileSystem018` | `VFileSystem022` |
| `VMaterialSystem080` | `VMaterialSystem081` |
| `VModelInfoServer002` | `VModelInfoServer003`, `VModelInfoServer004` |
| `VSoundEmitter003` | `VSoundEmitter002` |
| `VStudioRender026` | `VStudioRender025` |

## Not in this SDK at all

Client: `AVProcessUtils001`, `BlackBoxVersion001`, `ClientAlphaPropertyMgrV001`, `GMATCHFRAMEWORK_001`, `GameUI011`, `IMATCHEXT_L4D_INTERFACE_001`, `NetworkSystemVersion001`, `VResourceAccessControl001`

Server: `BlackBoxVersion001`, `GMATCHFRAMEWORK_001`, `IMATCHEXT_L4D_INTERFACE_001`, `INETSUPPORT_001`, `NetworkSystemVersion001`, `SteamMasterServerUpdater001`, `VResourceAccessControl001`, `VSERVERFOUNDRY001`, `VScriptManager001`, `Y0c021`

Read that as the shape of what L4D1 added to the Source it forked: matchmaking
(`GMATCHFRAMEWORK`, `IMATCHEXT_L4D`), a networking support layer (`INETSUPPORT`),
the server foundry, a scripting manager, resource access control. For an offline
port most are stubbable; `NetworkSystemVersion001` and `VScriptManager001` are
the two to understand early, since level scripts and netchannel setup run through
them. `GameUI011` matters only if the port keeps Valve's main menu instead of the
launcher's own.

## Already matching: the engine services this tree can answer today

Client: `ClientLeafSystem002`, `ClientRenderTargets001`, `DebugTextureInfo001`, `EngineTraceClient003`, `G15_INTERFACE_VERSION001`, `GAMEEVENTSMANAGER002`, `GameClientExports001`, `GameMovement001`, `HapticsInterface001`, `IEffects001`, `IEngineSoundClient003`, `InputSystemVersion001`, `MDLCache004`, `SceneFileCache002`, `ServerUploadGameStats001`, `SpatialPartition001`, `SteamMatchMakingServers002`, `VAvi001`, `VBAllocTracker001`, `VBik001`, `VCLIENTENGINETOOLS001`, `VCLIENTTOOLS001`, `VClientDllSharedAppSystems001`, `VClientEntityList003`, `VClientPrediction001`, `VDataCache003`, `VDebugOverlay003`, `VENGINE_GAMEUIFUNCS_VERSION005`, `VEngineClient013`, `VEngineClientStringTable001`, `VEngineEffects001`, `VEngineModel016`, `VEngineRandom001`, `VEngineShadowMgr002`, `VEngineVGui001`, `VGUI_Input005`, `VGUI_InputInternal001`, `VGUI_Localize005`, `VGUI_Panel009`, `VGUI_Scheme010`, `VGUI_Surface030`, `VGUI_System010`, `VGUI_ivgui008`, `VMDLLIB001`, `VMaterialSystemStub001`, `VParticleSystemQuery001`, `VPhysics031`, `VPhysicsCollision007`, `VPhysicsSurfaceProps001`, `XboxSystemInterface001`

Server: `BotManager001`, `DebugTextureInfo001`, `EngineTraceServer003`, `GAMEEVENTSMANAGER002`, `GameMovement001`, `HLTVDirector001`, `IEffects001`, `IEngineSoundServer003`, `ISERVERPLUGINHELPERS001`, `InputSystemVersion001`, `MDLCache004`, `PlayerInfoManager002`, `PluginHelpersCheck001`, `SceneFileCache002`, `ServerGameClients003`, `ServerGameEnts001`, `ServerGameTags001`, `ServerUploadGameStats001`, `SpatialPartition001`, `StaticPropMgrServer002`, `SteamMatchMakingServers002`, `VAvi001`, `VBAllocTracker001`, `VBik001`, `VDataCache003`, `VDebugOverlay003`, `VENGINE_GAMEUIFUNCS_VERSION005`, `VEngineRandom001`, `VEngineServer022`, `VEngineServerStringTable001`, `VEngineVGui001`, `VGUI_Input005`, `VGUI_Localize005`, `VGUI_Panel009`, `VGUI_Scheme010`, `VGUI_Surface030`, `VGUI_System010`, `VGUI_ivgui008`, `VMDLLIB001`, `VPhysics031`, `VPhysicsCollision007`, `VPhysicsSurfaceProps001`, `VProcessUtils001`, `VSERVERENGINETOOLS001`, `VSERVERTOOLS001`, `VServerDllSharedAppSystems001`, `VoiceServer002`, `XboxSystemInterface001`

These are the interfaces a port does *not* have to invent. The overlap between
the two lists - MDLCache, material system, VGUI, physics, sound, datacache,
debug overlay - is the framework the game code sits on, which is why this tree's
HL2 game code is a usable starting point and why the delta is a bounded list
rather than a rewrite.

## Names that look like interfaces and are not

The first mining run produced plausible nonsense, now rejected by the classifier:
`ABGR8888`, `ARGB8888`, `BGR565`, `BGR888`, `BGRA4444`, `BGRA5551`, `BGRA8888`,
`BGRX5551`, `BGRX8888`, `RGB565`, `RGB888`, `RGBA8888`, `RGBA16161616`, `UVWQ8888`
and `IMAGE_FORMAT_UVLX8888` are image formats; `ACT_PRIMARYATTACK_XM1014` and
`EjectBrass_556` are animation activities; `VP4001` and `Y0c021` are noise. They
stay in the fixtures as regression tests.

## Where the two builds sit relative to this SDK

The client asks for `VClient016` and `VEngineClient013`; the server asks for
`ServerGameDLL005` and `ServerGameClients003`, where this SDK declares
`ServerGameDLL008/009/010` and `ServerGameClients003/004`.

The numbers do not order by date across branches. L4D1 is *older* than the 2013
SDK for the filesystem and materials (`VFileSystem018` vs `022`,
`VMaterialSystem080` vs `081`, `MatSystemSurface006` vs `005`/`008`) but *newer* for
cvars and sound (`VEngineCvar007` vs `004`, `VSoundEmitter003` vs `002`). It is a
separate branch off the same trunk, not a checkpoint on a timeline - which is
also why "just use a newer SDK" is not a solution to an interface mismatch.
