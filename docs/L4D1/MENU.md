# The menu you see, and what L4D1's menu actually is

Status: **the menu on screen is not an L4D1 menu.** It is this engine's GameUI
rendering whatever `.res` layouts it is handed, which happens to be L4D1's. It
works, it is stable, and every detail in it is wrong for L4D1 — which is what
"the menu needs recreating" means, and what this page is about.

## What is on screen

Three pieces, none of them L4D1:

| Piece | Where it comes from | What it does |
| --- | --- | --- |
| `libGameUI.so` | this tree (TF2 2018 branch), built by our CI | the panel framework, main menu, options dialogs, `Resource/GameMenu.res` |
| `libServerBrowser.so` | this tree | the server browser dialog |
| `resource/*.res` | **L4D1's content** | the layouts and `Scheme` that decide how the above looks |

So the framework is ours, the layout is Valve's, and the panels the framework
expects in between are absent. The boot log says so in order:

```
Failed to load Resource\FogUIPanel.res
Type yard_line_panel of meta class yard_line_panel undefined!
MetaClass missing for yard_line_panel
Hud element 'CAchievementNotificationPanel' doesn't have an entry
    'AchievementNotificationPanel' in scripts/HudLayout.res
Failed to load resource/UI/customchat.res
--- Missing Vgui material vgui/servers/icon_replay
```

Each line is the same shape: a TF2/HL2-era name that L4D1 never shipped, or a
panel class that L4D1's *game code* registers and we do not.

## What L4D1's own menu is

L4D1's main menu lives in `client.dll`, not in `GameUI.dll`. It is a
`CBaseModPanel`-era VGUI screen (`CMainMenu`) with L4D-specific pages — campaign
select, single-player credits, achievements, mutations — plus the background
movie and the menu theme, and it drives commands that only exist in L4D's game
code (`OpenCampaignSelect`, `OpenMatchmaking`, `StartSinglePlayer`…). None of
that is here, and none of it can be shipped here: it is Valve's code.

So the menu has to be **written**, exactly like the rest of the game, and it
belongs to the same phase as the game code because that is where the commands
it calls live.

## What to do, cheapest first

1. **Make the text legible.** `UNABLE TO LOAD FONT FILE resource/HALFLIFE2.vfont`
   (and HL2EP2/TG/TGB/Futurot/Toolbox) plus `Failed to load custom font file
   …/dejavusans.ttf` means the UI is drawing with fallbacks it also failed to
   load. No menu recreation matters until a string renders as characters you can
   read, so this comes before any panel work.
2. **A console entry point.** `toggleconsole` and `showconsole` already exist
   (`engine/console.cpp`), `-console` is already in your argv, and the engine
   activates the console on that flag (`engine/vgui_baseui_interface.cpp`) — so a
   console that does not appear is an activation-order problem, not a missing
   feature. On a touch device the gap is input: there is no backquote key to bind.
   A finger-sized button that runs `showconsole` is the whole fix.
3. **Stub the panels the framework expects.** `yard_line_panel`, `customchat`,
   `FogUIPanel` and friends are referenced by layouts we do not own; registering
   do-nothing panel classes for them stops `MetaClass missing` from cascading and
   makes the current menu predictable.
4. **Build the L4D1 menu for real** — in the game module, using L4D1's own
   `resource/` layouts as the visual reference and wiring the buttons to commands
   our game code actually implements. That is Phase 2b/3 work, not a detour.

Numbers 1 and 2 are what make the port *usable*; 3 is what keeps the log quiet;
4 is what makes it L4D1.
