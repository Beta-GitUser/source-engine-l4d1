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

## The check that answers it without a build

```sh
python3 -m l4d1ctl fonts --root /storage/emulated/0/srceng/left4dead
```

```
scheme: /storage/emulated/0/srceng/left4dead/resource/SourceScheme.res
        3 font(s), 2 on .vfont (cannot load here), 1 on a system font name

CustomFontFiles:
  resource/HALFLIFE2.vfont
  resource/hl2crosshairs.vfont

Fonts:
  Default      HALFLIFE2 (resource/HALFLIFE2.vfont)  (tall 16, weight 500)
  DefaultBold  HALFLIFE2 (resource/HALFLIFE2.vfont)  (tall 16, weight 700)
  DefaultSmall Verdana  (tall 19)
```

It exits non-zero when every font is a `.vfont`, because that verdict means no
amount of fixing the read path (stage 3) can put text on screen. It also resolves
the second way a scheme points at a custom font: L4D1 names the font `HALFLIFE2`
rather than giving a `"custom"` path, and the file behind that name is
`resource/HALFLIFE2.vfont`.

Two outcomes, two projects:

* **vfont-only** → either a `.vfont` reader in `CMatSystemSurface`'s font manager, or
  a scheme override that names a TTF. The reader is fidelity; the override is
  legibility today, at the price of L4D1's typeface.
* **system names** → fontconfig answers, and the fix is the host-file read that is
  already in this build.

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
