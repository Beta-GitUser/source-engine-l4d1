# Bink video, and what it would take

The device log carries one line per boot:

```
Can't find module - video_bink.so
```

It is a warning, not a fault: the engine asks for a video subsystem, does not get
one, and carries on. This is what is behind it, and what "implementing Bink"
would actually mean here.

## What a `.bik` is

Bink Video is RAD Game Tools' proprietary codec, the one every Source game of
this era ships. L4D1 (2008) is Bink **1**, extension `.bik`; Bink **2** uses
`.bk2` and a different block/quantisation scheme, so nothing below describes it.

A Bink 1 file is a container and a codec at once:

* **Header**, 16 bytes: the magic `BIK` plus one letter. `i` is a plain file;
  `f`/`g`/`h` are the "frame index" variants that differ in where the index
  lives. Then the file size, the frame count, and the size of the largest frame.
* **Frame index**: for each frame, the offset and packet count (and which frames
  are keyframes). In the indexed variants it sits up front, so seeking is cheap;
  otherwise it is the last table in the file.
* **Video**: YUV 4:2:0 cut into 16×16 blocks. Each frame carries its own Huffman
  tables and a quantiser matrix; a block is either intra or motion-compensated
  (half-pixel precision), and its coefficients are an integer DCT.
* **Audio**: either RAD's IMA ADPCM or "Bink Audio", a per-band quantised DCT
  codec at 44.1 kHz. Some files carry no audio at all.

None of that is documented by RAD, and Bink's decoder is licensed per title — the
SDK is not redistributable, which is why no Source tree contains one.

## How the engine consumes it

`public/video/ivideoservices.h` defines `IVideoServices` / `IVideoSubSystem` /
`IVideoMaterial`. `video/videoservices.cpp` maps an extension to a module name
(`.bik` → `video_bink`), and `Sys_LoadModule`s it on first use. The module returns
an `IVideoMaterial`, and the engine hangs that material on a panel
(`game/client/vgui_video.h`). The movie therefore draws like any other textured
quad.

The tree's own implementation, `video/video_bink/`, has no Bink SDK types in it at
all: `bink_material.cpp` decodes through **libavformat/libavcodec**
(`avcodec_send_packet` / `avcodec_receive_frame`), converts YUV to RGB888 on the
CPU with `yuv_rgb.c`, and refreshes a procedural `IMAGE_FORMAT_RGB888` texture
through an `ITextureRegenerator`. The `bik_dx90` shader exists but this path does
not need it — the shader would only matter if frames stayed in YUV.

So the honest answer to "how do we implement .bik" is: **we don't write a
decoder; we build the one that is already here against a decoder library that
already has one.** FFmpeg's `bink.c` and `binkaudio.c` are the only open
implementations, are LGPL, and are what this module was written against.

## Why it is not built today

Three separate gaps, in order:

1. **No FFmpeg.** `external/` holds `v8` and `vpc` and nothing else. The module's
   `wscript` links `AVCODEC AVUTIL AVFORMAT`, so it cannot link on Android as
   things stand.
2. **Nothing recurses into it.** `video/wscript` builds `video_services` only;
   `video/video_bink/wscript` exists and is never called. The top-level `wscript`
   lists `'video'` twice and never the subdirectory.
3. **It is not in the artifact.** The module list is 27 `.so` files, and
   `libvideo_bink.so` is not one of them, so even a successful build would not
   reach the device.

## The three routes

| Route | Work | What it buys | Cost |
| --- | --- | --- | --- |
| Leave it (today) | none | nothing; one warning line per boot | the movie does not draw, everything else is unaffected |
| Build the module against FFmpeg | vendor FFmpeg for aarch64 configured with only the `bink`, `binkaudio` and PCM/IMA decoders plus the `bink` demuxer (`--disable-everything --enable-decoder=...`), recurse the subfolder in waf, add the module to the artifact | intro movie plays, with audio — `binkaudio.c` is the "Bink audio support" the fork's README still lists as open | roughly 2 MB of static libav linked into one `.so`, plus licence paperwork |
| Write a decoder | weeks of reverse engineering | the same as above, worse | not worth discussing |

Licence, because it decides the shape of the change: FFmpeg is LGPL-2.1+ by
default. Shipping it as a **separately loadable `libvideo_bink.so`** that links
`libavcodec.so` dynamically satisfies LGPL's relinking requirement; a
statically-linked-in, `--enable-gpl` build does not, and is not an option here.
Either way it needs a row in [THIRDPARTY.md](THIRDPARTY.md), the licence text, and
a written offer for the corresponding source.

## What to measure before doing any of it

Ask the content, not the README:

```sh
find /storage/emulated/0/srceng/left4dead -iname '*.bik' -printf '%10s %p\n'
python3 scripts/vpk-list.py /storage/emulated/0/srceng/left4dead/pak01_dir.vpk --filter .bik
```

`scripts/vpk-list.py` only reads the directory file's own entry table, so both
commands together cover loose files and packs. If the answer is one intro movie
under `media/`, the priority is clear: after the boot ladder, not before. A port
that cannot reach the main menu does not need a nicer intro.

## If it is spiked

The proof, in order, with no ambiguity in any step:

1. `libvideo_bink.so` appears in the build output and in the artifact, alongside
   the 27 existing modules.
2. `nm -D --undefined-only libvideo_bink.so | grep ' av'` resolves against the
   shipped `libavcodec.so` / `libavformat.so` — no silent static copy of a
   GPL-only decoder.
3. A boot **without** `-novid` draws the intro instead of logging
   `Can't find module - video_bink.so`.
4. Frame pacing: the module updates a texture from the main thread, so a slow
   decode shows up as input lag, not as a log line. Watch for it.
