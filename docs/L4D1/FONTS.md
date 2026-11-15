# Why the UI has no text: the failure chain, stage by stage

Status: **a checklist, not a fix report.** One line of a boot log —
`Found font: …` followed by `Failed to load custom font file …` — is the *last*
stage of a chain that can break in seven places, and fixing the wrong one produces
a build that changes nothing and a day that feels wasted. This page says what each
stage looks like when it fails, so the next log narrows it instead of restarting it.

## The chain

| # | Stage | Code | Fails how | What the log shows |
| --- | --- | --- | --- | --- |
| 1 | Scheme names a font | `resource/SourceScheme.res`, `Fonts` and `CustomFontFiles` | scheme missing, or names a font this vgui2 does not know | nothing at all: `Scheme::GetFont` silently returns the default |
| 2 | Font file is resolved | `CMatSystemSurface::AddCustomFontFile` | path is an absolute host path, or not in any search path | `Warning: Couldn't find custom font file '<path>'` |
| 3 | Bytes are read | `FontDataHelper` → `g_pFullFileSystem->ReadFile` | the filesystem refuses a path outside its search paths | `Failed to load custom font file '<path>'` |
| 4 | A FreeType face is built | `FT_New_Memory_Face` | not actually a TrueType/OpenType file — `.vfont` lands here | `ERROR <n>: UNABLE TO LOAD FONT FILE <path>` |
| 5 | A glyph atlas is built | `CFontManager` | face loads, but size/DPI makes zero-height glyphs | no message; text is invisible or one pixel tall |
| 6 | The label is laid out | vgui2 `Label`/`Button` | font has no glyphs → zero `GetContentSize` | controls collapse to nothing to click |
| 7 | The tap lands on it | `InputHandleInputEvent`, `IE_FingerDown` | window coords vs backbuffer coords, letterboxing | `vgui: first touch at (x,y) of WxH` says where it thinks you tapped |

Stages 1–4 are one-time, at scheme load; 5–6 are per control; 7 is per tap.

## What the stages say about this port specifically

* **Stage 3 is broken on Android, and that is what the log is complaining about.**
  The fallback font is a *host* file (`/data/user/0/com.valvesoftware.source/files/dejavusans.ttf`),
  found by fontconfig. `g_pFullFileSystem` resolves through search paths, so an
  absolute host path is outside all of them and `ReadFile` fails. The file was
  found and then not read — that is why the same path appears in both lines.
* **Stage 4 will always fail for `.vfont`.** L4D1 ships `resource/HALFLIFE2.vfont`,
  `TG.vfont`, `TGB.vfont` and friends; `ERROR 2` is FreeType's
  `FT_Err_Unknown_File_Format`. FreeType cannot read Valve's bitmap font format, no
  matter how the bytes arrive. If L4D1's scheme lists *only* `.vfont` custom fonts,
  stage 1 then hands every label the default font, and stages 5–6 decide the rest.
* **Stage 7 has a quiet trap**: `GetScreenSize` is the *backbuffer* size, while an
  SDL finger event is normalised to the *window*. With letterboxing those are not
  the same rectangle, so a tap can land tens of pixels off — enough to miss a
  button while the cursor is visibly somewhere else.

## What this device's log actually says

Line counts from `engine-2.log` (one `-nosound -novid` boot), which settle the order
of the stages:

```
[7.59]  LoadLibrary: libGameUI.so
[7.69]  ERROR 2: UNABLE TO LOAD FONT FILE resource/HALFLIFE2.vfont
[7.74]  ERROR 2: UNABLE TO LOAD FONT FILE resource/HL2EP2.vfont
[7.84]  ERROR 2: UNABLE TO LOAD FONT FILE resource/TG.vfont
[7.84]  ERROR 2: UNABLE TO LOAD FONT FILE resource/TGB.vfont
[7.845] Found font: /data/user/0/com.valvesoftware.source/files/LiberationMono-Regular.ttf
[7.846] Failed to load custom font file '…/LiberationMono-Regular.ttf'
        … 1190 times in total, 1074 of them dejavusans-bold.ttf / dejavusans.ttf
```

* 1190 `Found font:` and **1190** `Failed to load custom font file`: every single
  font the UI asks for dies at stage 3. There is no font in the process at all, so
  every label is empty and every control sized by its text is zero-sized.
* 9 `UNABLE TO LOAD FONT FILE`, all `.vfont`: those four files in `CustomFontFiles`
  were **read successfully** and rejected by FreeType at stage 4. Stage 3 is not
  broken for files inside the search paths — only for absolute host paths.
* `resource/marlett.ttf`, the fifth `CustomFontFiles` entry, logs nothing: it is a
  real TTF inside the search paths, so it loaded. Another proof that the read path
  works and only the host path fails.
* The scheme's fonts are **system names** — Tahoma, Courier New, Lucida Console,
  Tahoma Bold. `l4d1ctl fonts` reports 11 of them with no `"custom"` path, so the
  `.vfont` files are auxiliary (crosshair and tool glyph sets), not the UI's text.

So: stage 4 is real but irrelevant to text, and stage 3 is the one that matters.
That is what the direct read in `FontDataHelper` fixes.

## The reading bug that made this look simpler than it is

L4D1's scheme writes one resolution per platform, with the conditional *between* the
key and its block:

```
"UiBold"
{
	"1"	[$WIN32] { "name" "Tahoma" "tall" "12" ... }
	"1"	[$X360]  { "name" "Tahoma" "tall" "24" ... }
}
```

`l4d1ctl` read `[$WIN32]` as the key's *value*, so the block that followed was treated
as a stray `{` and its contents (`name`, `tall`, `weight`, `outline`) landed in the
parent - which is how a 36-font scheme was reported as 11 fonts plus five called
`name`. Fixed in the KeyValues reader, with the shape above as a test: conditionals
are consumed before deciding whether a key introduces a block, and `[$X360]` entries
are skipped because this build never asks for them. Everything that reads Valve
KeyValues - gameinfo, schemes, VPK manifests - benefits.

## Why it "works on HL2" and not here

`vgui2/vgui_surfacelib/linuxfont.cpp` is **byte-identical to upstream** (`diff` = 0
lines against `nillerusr/source-engine`). Nothing about fonts was touched by this
fork, so "we barely changed GameUI" is exactly right and still does not help: the
stock code is the problem.

It has no fontconfig. `HAVE_FC` is defined nowhere in the build, so
`TryFindFont()` takes its `#ifdef ANDROID` branch, which does this and nothing else:

```c
snprintf( fontFile, sizeof fontFile, "%s/files/dejavusans.ttf", getenv("APP_DATA_PATH") );
return fontFile;                       // no existence check, no fallback, no fontconfig
```

That is a *fabricated* path. The string `Found font:` in the log therefore means only
that `APP_DATA_PATH` is set — not that a font exists. On an install whose app package
deposits those TTFs in `$APP_DATA_PATH/files/`, the same code works and HL2 draws
text. On one where it does not, every one of the 1190 font requests resolves to a
file that is not there, and the UI has no glyphs at all. Same engine, same GameUI,
different packaging: that is the whole difference.

So the fix is not in GameUI and not in the scheme. It is to stop trusting the
fabricated path:

1. `TryFindFont()` now checks `access( path, R_OK )` before answering, and falls back
   to `/system/fonts` — Roboto, Noto, DroidSans, DroidSansMono — which every Android
   has, including when the app ships no fonts at all. Announced once per variant, not
   1190 times.
2. If a file exists but cannot be opened, `FontDataHelper`'s direct read covers it and
   prints `errno`, so "absent" and "unreadable" are distinguishable.

## The check that answers it without a build

```sh
python3 -m l4d1ctl fonts --root /storage/emulated/0/srceng/left4dead
```

```
scheme: /storage/emulated/0/srceng/left4dead/resource/sourcescheme.res
        11 font(s), 11 on a system font name

CustomFontFiles:
  resource/HALFLIFE2.vfont
  resource/HL2EP2.vfont
  resource/TG.vfont
  resource/TGB.vfont
  resource/marlett.ttf

Fonts:
  DebugFixed            Courier New  (tall 10, weight 500)
  Default               Tahoma  (tall 16, weight 500)
  DefaultBold           Tahoma  (tall 16, weight 1000)
  ...
  UiBold                Tahoma  (tall 12, weight 1000)
```

It exits non-zero when every font is a `.vfont`, because that verdict means no
amount of fixing the read path (stage 3) can put text on screen. It reads both
shapes of `CustomFontFiles` — L4D1 numbers its entries (`"1" "resource/TG.vfont"`),
other schemes write bare paths — and it resolves the other way a scheme names a
custom font, by calling a font `HALFLIFE2` rather than giving it a `"custom"` path.

Two outcomes, two projects:

* **vfont-only** → either a `.vfont` reader in `CMatSystemSurface`'s font manager, or
  a scheme override that names a TTF. The reader is fidelity; the override is
  legibility today, at the price of L4D1's typeface.
* **system names** (this port) → fontconfig answers with a host path, and the fix is
  the direct read that is already in build `43621383992`.

## The checks, cheapest first

On the device, before any rebuild:

```sh
# What does L4D1's scheme actually ask for? vfont, ttf, or system font names?
grep -in "customfontfiles\|\.vfont\|\.ttf\|\"name\"" \
     /storage/emulated/0/srceng/left4dead/resource/SourceScheme.res | head -40

# Is the fallback font a real TrueType file, and readable by us?
ls -l /data/user/0/com.valvesoftware.source/files/dejavusans.ttf
head -c 4 /data/user/0/com.valvesoftware.source/files/dejavusans.ttf | od -An -tx1
```

`00 01 00 00` (or `4f 54 54 4f` for OpenType CFF) means it is a real face and
stages 3–4 should succeed once the bytes get through. `74 74 63 66` is `ttcf`,
a collection — FreeType handles it, but only through the collection API, so it
would explain a stage-4 failure with a perfectly good file.

If the scheme lists no `.ttf` at all, the port needs either L4D1's own scheme
replaced with one that names a TTF, or a `.vfont` reader. Those are different
projects; do not start either until the scheme dump says which one it is.

## Rules this page exists to enforce

* one stage per build, and the log line that proves it moved;
* never patch stage 5–7 on the strength of a stage 3 message;
* a diagnostic line costs one build and answers a question; a "fix" without one
  costs a build and answers nothing.
