# Bink video, and what it took to play it

Status: **built**. `libvideo_bink.so` is part of the artifact when FFmpeg is
available at configure time, decodes `.bik` through libavformat/libavcodec, and
draws into a material. The movie is **silent** — audio is the remaining half of
the work, and it is the last section of this page.

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
(`.bik` → `video_bink`) and `Sys_LoadModule`s it on first use. The module returns
an `IVideoMaterial`, and the engine hangs that material on a panel
(`game/client/vgui_video.h`), so the movie draws like any other textured quad.

The decoder has no Bink SDK types in it: `bink_material.cpp` decodes through
libavformat/libavcodec (`avcodec_send_packet` / `avcodec_receive_frame`),
converts YUV to RGB888 on the CPU with `yuv_rgb.c`, and refreshes a procedural
`IMAGE_FORMAT_RGB888` texture through an `ITextureRegenerator`. The `bik_dx90`
shader exists but this path does not use it — that shader is for keeping frames
in YUV on the GPU.

## What was missing, and what was added

The module source has been in the tree for years (it is nillerusr's), and was
built by nothing. Three separate gaps, in the order they had to be closed:

1. **No FFmpeg.** `external/` holds `v8` and `vpc` and nothing else, and the
   module links `AVCODEC AVUTIL AVFORMAT`. Added:
   `scripts/build-ffmpeg-android.sh`, which downloads a pinned tag
   (`FFMPEG_VERSION`, default `n4.4.4`) and cross-compiles three static
   libraries with only the bink demuxer, the bink/binkaudio/IMA decoders and the
   `file` protocol enabled.
2. **Nothing recursed into it.** `video/wscript` builds `video_services` only,
   and the top-level list names `'video'` twice without ever naming the
   subdirectory. Added: `projects['game'] += ['video/video_bink']` in the
   top-level `wscript`, gated on `HAVE_FFMPEG` so a tree without FFmpeg builds
   exactly as it did before.
3. **It was not in the artifact.** The CI step copies every `build/**/*.so`, so
   the module appears in the artifact by itself once it builds.

Also fixed while wiring it up: `avformat_open_input` was handed the game's own
relative path (`media/…`), which is not the process working directory on a phone.
`OpenMovie` now asks the filesystem for the real path first (`GetLocalPath`) and
says both names if the open still fails.

## Building it

```sh
# in CI, or any x86_64 Linux box with the NDK
sh scripts/build-android.sh                  # builds FFmpeg first, then the engine
FFMPEG=0 sh scripts/build-android.sh         # old behaviour: no video module
FFMPEG_ROOT=/opt/ffmpeg-aarch64 sh scripts/build-android.sh   # reuse a prefix
```

FFmpeg lives in `.ffmpeg/<arch>` (git-ignored, cached in CI by
`FFMPEG_VERSION`). A finished prefix is reused until the version changes; the
build log prints the prefix, and configure prints
`FFmpeg (Bink video): /path` when it found it, or
`FFmpeg (Bink video): no - video_bink will not be built`.

## Limits that are deliberate

* **`--disable-asm`.** NDK r10e's assembler predates parts of FFmpeg's aarch64
  `.S` set. A 35-second intro decoding slower is a cheaper failure than a build
  that will not assemble; turn asm on when the rest is proven.
* **The file must be loose.** libavformat opens a path, so a `.bik` inside
  `pak01` has nothing to open. L4D1 ships its movies loose under
  `left4dead/media/`, so this costs nothing today. Supporting packed video means
  an AVIOContext over `IFileSystem`, not a bigger decoder.
* **Silent.** See below.

## The remaining half: audio

`CBinkMaterial` sets `m_bHasAudio = false` and its audio paths are QuickTime
leftovers behind `#if defined(WIN32) || defined(OSX)`; on Android
`SoundDeviceCommand` returns `OPERATION_NOT_SUPPORTED`, so the movie plays
without sound. Closing that needs three things that are all outside the decoder:

1. decode the audio stream (avcodec already has `binkaudio_dct`,
   `binkaudio_rdft` and `adpcm_ima_qt`, all enabled in the build),
2. resample to the engine's mixer format — `--enable-swresample` and
   `-lswresample` in the module's `use`, which is deliberately not linked yet
   because nothing calls it,
3. push it into the sound system the way the engine's own movie audio does
   (`IVideoCommonServices` in `public/video/ivideoservices.h` is the seam;
   check what `engine/` passes for `SET_SOUND_MANAGER_DEVICE` before writing it).

That is a separate change with its own measurement, not a follow-up commit to
this one.
