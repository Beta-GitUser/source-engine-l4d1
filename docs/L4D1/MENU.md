# The menu you see, and what L4D1's menu actually is

Status: **the menu on screen is L4D1's layout drawn by a newer GameUI.** The frame
and the layout are right; the drift is version, not authorship — this tree's GameUI
is TF2-2018 and L4D1's content is 2008, so the framework asks for panels that never
existed in the game it is dressing. Getting to an L4D1-looking menu is therefore
mostly making the newer framework tolerate the older layout, plus fonts, plus the
background movie. It is not a rewrite.

## What is on screen

Three pieces, none of them L4D1:

| Piece | Where it comes from | What it does |
| --- | --- | --- |
| `libGameUI.so` | this tree (TF2 2018 branch), built by our CI | the panel framework, main menu, options dialogs, `Resource/GameMenu.res` |
| `libServerBrowser.so` | this tree | the server browser dialog |
| `resource/*.res` | **L4D1's content** | the layouts and `Scheme` that decide how the above looks |

So the framework is ours, the layout is Valve's, and the panels the framework
expects in between were never in L4D1. The boot log says so in order:

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

## What L4D1's own menu is (and why this is cheaper than it looks)

L4D1 keeps the **classic GameUI main menu** — the same code family as retail HL2's,
built from `resource/GameMenu.res` and skinned by `resource/SourceScheme.res`, with a
background movie behind it and a few things removed for the L4D look (no logo block on
the right, a slimmer button column, the background bink instead of the HL2 panorama).
It is not a `CBaseModPanel`/client.dll screen; that arrives with L4D2.

Which means the piece we already have — GameUI rendering `resource/GameMenu.res` — is
the right piece, and it is already reading L4D1's own layout: there is no
`Failed to load Resource\GameMenu.res` line in the boot log, so the layout in use is
L4D1's, not a fallback.

What is out of place is **version drift in the other direction**: this tree's GameUI is
the TF2 2018 branch, five years newer than L4D1's, so it looks for panels and layout
entries that L4D1's content has never contained (`FogUIPanel.res`, `customchat.res`,
`AchievementNotificationPanel` in `HudLayout.res`, `yard_line_panel`). Those lines are
the newer framework asking for newer content, not L4D1's menu being missing.

The gaps that are real:

- **fonts that load** — `UNABLE TO LOAD FONT FILE resource/*.vfont` and the failing
  `dejavusans.ttf` fallback mean the text you see is not the text L4D1 draws;
- **the background movie and menu music** — a `.bik` behind the panel, which is both a
  video question (Bink) and an audio one;
- **game-code panels** — achievements, campaign select, and anything else whose button
  commands only exist in L4D1's game code, which is the part being rewritten anyway.

## How the UI is put together, briefly

1. `GameUI` registers **panel classes** by name (`CGameMenu`, `COptionsDialog`, …).
2. A `.res` file is KeyValues: `Frame`/`Button`/`Label` blocks each name a panel class
   and give it geometry, text and a `command` or `customstring`.
3. `resource/SourceScheme.res` (or L4D1's scheme) supplies colours, borders and the
   font table (`CustomFontFiles`, per-font `tall`/`weight`/`blur`).
4. The main menu instantiates `GameMenu.res`, which instantiates nothing else — the
   buttons fire commands, and *those* panels (options, campaign select) are separate
   `.res` files loaded on demand.

So a missing `.res` is one dead button; a missing panel *class* is a `MetaClass
missing` line and a control that silently does nothing; a missing font is every label
in the file rendering as fallback glyphs.

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
