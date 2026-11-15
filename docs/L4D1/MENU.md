# The menu you see, and what L4D1's menu actually is

Status: **the menu on screen is L4D1's layout drawn by a newer GameUI.** The frame
and the layout are right; the drift is version, not authorship — this tree's GameUI
is TF2-2018 and L4D1's content is 2008, so the framework asks for panels that never
existed in the game it is dressing. Getting to an L4D1-looking menu is therefore
mostly making the newer framework tolerate the older layout, plus fonts, plus the
background movie. It is not a rewrite.

## What `resource/gamemenu.res` actually contains

Not a panel layout. L4D1's main menu is a list of entries - label, command, and
whether it only applies in game:

```
"GameMenu"
{
	"1" { "label" "#GameUI_GameMenu_ResumeGame" "command" "ResumeGame" "OnlyInGame" "1" }
	...
	"6" { "label" "#GameUI_GameMenu_FindServers" "command" "OpenServerBrowser" }
	"7" { "label" "#GameUI_GameMenu_CreateServer" "command" "OpenCreateMultiplayerGameDialog" }
	"8" { "label" "#GameUI_GameMenu_Achievements" "command" "OpenAchievementsDialog" }
	"10" { "label" "#GameUI_GameMenu_Options" "command" "OpenOptionsDialog" }
	"11" { "label" "#GameUI_GameMenu_Quit" "command" "Quit" }
}
```

Nine entries, two of them (Resume / Disconnect) in-game only, one disabled spacer, and
a commented-out `OpenSoloGame`. There is no `ControlName`, no `xpos`, no panel nesting:
the menu builds its items itself and the file only decides which ones and in what
order.

**This tree already reads exactly that format.** `CBasePanel::RecursiveLoadGameMenu`
takes `label`, `command` and an optional `name` per entry, passes the whole KeyValues
to `CGameMenu::AddMenuItem`, and `CGameMenu::UpdateMenuItemState` honours
`OnlyInGame` / `OnlyInReplay` / the VR keys. So no recreation is needed for the main
menu: the entries, their order and their commands are L4D1's own.

Two things this tree adds on top, both worth knowing:

* a **`Console` item calling `OpenConsole`**, prepended to whatever the file lists -
  the console entry point asked about earlier already exists in the main menu;
* `OpenServerBrowser` if the file does not list it and the mod is not single player.

Settings pages are the classic shape and match as well: `optionssubvideo.res` is
`ControlName` / `fieldName` / `xpos` / `ypos` / `wide` / `tall` / `labelText` blocks,
which is what this tree's control loader has always consumed.

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

## Is any of it drawn? (`vgui: draw stats`)

The panel tree proves the menu *exists*. It says nothing about whether a pixel of it
reached the screen, and the two ways to get a black screen — "vgui never painted" and
"vgui painted, but every label was skipped" — need opposite fixes. So the tap handler
prints what the surface was actually asked to draw, for the first six taps:

```
vgui: draw stats (after tap 1): 3 painted frame(s), 12 filled rect(s), 0 textured rect(s), 4 text call(s) in 26 char(s), 4 font bind(s)
vgui:   drawn text #1 in 'Trade Gothic Bold': "#GameUI_GameMenu_Quit"
vgui:   drawn text #2 in 'Trade Gothic Bold': "#GameUI_GameMenu_Options"
```

Three verdicts, three different bugs:

| The line says | It means |
| --- | --- |
| `0 painted frame(s)` | `CMatSystemSurface::PaintTraverseEx` never ran at the top level: the engine is not drawing vgui at all (present/activation order). No font, colour or material can fix this one |
| frames > 0, `0 text call(s)` | panels paint, but no string reaches the surface: layout, visibility or an early-out — still not the font |
| text calls > 0 | labels are drawn, and the samples name the font they are drawn in. A blank screen is then colour, material or compositing, and the sample says which font to suspect first |

The counters only move when a draw survives its alpha test and a string survives the
"no current font" early-out, so `12 filled rect(s)` means twelve quads were emitted,
not twelve were attempted. The line is printed from the touch path on purpose: taps
are known to arrive, while "does paint happen" is exactly the open question.

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

0. **Work out which stage of the font chain is broken before touching it** —
   [FONTS.md](FONTS.md) lists the seven stages, what each looks like in a log, and
   the two shell commands that identify the one that is failing here.
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
