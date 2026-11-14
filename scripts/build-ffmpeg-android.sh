#!/bin/sh
# Build the FFmpeg libraries that video/video_bink links against.
#
# Bink is RAD Game Tools' codec; its decoder is licensed per title and its SDK is
# not redistributable, so no Source tree contains one. FFmpeg's bink demuxer and
# its bink/binkaudio decoders are the only open implementation, which is what the
# module in video/video_bink was written against (docs/L4D1/VIDEO.md).
#
# This script is deliberately separate from build-android.sh: it downloads and
# cross-compiles a third-party library, which is a different kind of event from
# compiling our own code, and CI caches its output under .ffmpeg/<arch>.
#
# Usage:
#   sh scripts/build-ffmpeg-android.sh                     # aarch64, API 21
#   ARCH=armeabi-v7a sh scripts/build-ffmpeg-android.sh
#   FFMPEG_ROOT=/opt/ffmpeg-aarch64 sh scripts/build-ffmpeg-android.sh  # already built
#   FFMPEG_VERSION=n4.4.4 sh scripts/build-ffmpeg-android.sh
#
# Environment:
#   NDK_HOME        root of an unpacked NDK (required; build-android.sh exports it)
#   ARCH            aarch64 | armeabi-v7a, default aarch64
#   API             android API level, default 21
#   FFMPEG_VERSION  git tag to build, default n4.4.4
#   FFMPEG_ROOT     install prefix, default .ffmpeg/<arch>
#   JOBS            parallel build jobs, default nproc
#
# A finished prefix is left alone unless FORCE=1, so a cached .ffmpeg/<arch>
# makes this a no-op on the second run.

set -e

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

ARCH=${ARCH:-aarch64}
API=${API:-21}
FFMPEG_VERSION=${FFMPEG_VERSION:-n4.4.4}
NDK_HOME=${NDK_HOME:-${ANDROID_NDK_HOME:-}}
FFMPEG_ROOT=${FFMPEG_ROOT:-$ROOT/.ffmpeg/$ARCH}
FORCE=${FORCE:-0}
JOBS=${JOBS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)}

log() { printf '==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

[ -n "$NDK_HOME" ] && [ -d "$NDK_HOME" ] || die "NDK_HOME is not set (build-android.sh exports it)"
NDK_HOME=$(cd "$NDK_HOME" && pwd)

case "$ARCH" in
	aarch64)
		ff_arch=aarch64
		ff_cpu=armv8-a
		toolchain=aarch64-linux-android-4.9
		prefix=aarch64-linux-android-
		sysroot_arch=arch-arm64
		;;
	armeabi-v7a|armeabi-v7a-hard)
		ff_arch=arm
		ff_cpu=armv7-a
		toolchain=arm-linux-androideabi-4.9
		prefix=arm-linux-androideabi-
		sysroot_arch=arch-arm
		;;
	*) die "no FFmpeg cross-compile rules for ARCH=$ARCH" ;;
esac

bindir="$NDK_HOME/toolchains/$toolchain/prebuilt/linux-x86_64/bin"
sysroot="$NDK_HOME/platforms/android-$API/$sysroot_arch"
[ -d "$bindir" ] || die "no NDK gcc toolchain at $bindir (checked for $toolchain)"
[ -d "$sysroot" ] || die "no sysroot at $sysroot (is API=$API installed in this NDK?)"

# A cached prefix wins, and is checked by content rather than by a timestamp:
# a half-finished make install leaves the stamp and no libraries.
stamp="$FFMPEG_ROOT/.version"
if [ "$FORCE" != "1" ] && [ -f "$stamp" ] && [ "$(cat "$stamp")" = "$FFMPEG_VERSION" ] \
	&& [ -f "$FFMPEG_ROOT/lib/libavcodec.a" ] && [ -f "$FFMPEG_ROOT/lib/libavformat.a" ]; then
	log "ffmpeg $FFMPEG_VERSION already built at $FFMPEG_ROOT"
	exit 0
fi

mkdir -p "$ROOT/.ffmpeg/dl"
tarball="$ROOT/.ffmpeg/dl/ffmpeg-$FFMPEG_VERSION.tar.gz"
if [ ! -f "$tarball" ]; then
	url="https://codeload.github.com/FFmpeg/FFmpeg/tar.gz/refs/tags/$FFMPEG_VERSION"
	log "downloading $FFMPEG_VERSION"
	if command -v curl >/dev/null 2>&1; then
		curl -fL --retry 3 --connect-timeout 30 -o "$tarball" "$url" \
			|| die "could not download $url"
	elif command -v wget >/dev/null 2>&1; then
		wget -O "$tarball" "$url" || die "could not download $url"
	else
		die "need curl or wget to download FFmpeg"
	fi
fi

src="$ROOT/.ffmpeg/src-$FFMPEG_VERSION"
rm -rf "$src"
mkdir -p "$src"
log "unpacking"
tar -xzf "$tarball" -C "$src" --strip-components=1

# Only what libvideo_bink calls: the bink demuxer, the bink/binkaudio decoders,
# file input, and the three libraries themselves. Everything else - every other
# codec, muxer, protocol, filter, device - is off, which is what keeps this at a
# couple of megabytes instead of thirty.
#
# --disable-asm for the first build: the NDK r10e assembler predates some of what
# FFmpeg's aarch64 .S files expect, and a decoding-speed regression on a 35-second
# intro movie is a much cheaper failure than a build that will not assemble.
log "configure ($ff_arch, api $API)"
cd "$src"
./configure \
	--prefix="$FFMPEG_ROOT" \
	--target-os=android \
	--arch="$ff_arch" \
	--cpu="$ff_cpu" \
	--enable-cross-compile \
	--sysroot="$sysroot" \
	--cross-prefix="$bindir/$prefix" \
	--cc="$bindir/${prefix}gcc" \
	--cxx="$bindir/${prefix}g++" \
	--ar="$bindir/${prefix}ar" \
	--ranlib="$bindir/${prefix}ranlib" \
	--strip="$bindir/${prefix}strip" \
	--enable-static \
	--disable-shared \
	--enable-pic \
	--disable-asm \
	--disable-programs \
	--disable-doc \
	--disable-network \
	--disable-autodetect \
	--disable-avdevice \
	--disable-avfilter \
	--disable-postproc \
	--disable-swscale \
	--disable-swresample \
	--disable-everything \
	--enable-avcodec \
	--enable-avformat \
	--enable-avutil \
	--enable-demuxer=bink \
	--enable-decoder=bink,binkaudio_dct,binkaudio_rdft,adpcm_ima_qt,pcm_s16le \
	--enable-protocol=file \
	--extra-cflags="-O2 -fPIC -D__ANDROID_API__=$API" \
	> "$ROOT/.ffmpeg/configure-$ARCH.log" 2>&1 \
	|| { tail -n 30 "$ROOT/.ffmpeg/configure-$ARCH.log"; die "ffmpeg configure failed"; }

log "build (-j$JOBS)"
make -j"$JOBS" > "$ROOT/.ffmpeg/build-$ARCH.log" 2>&1 \
	|| { grep -aE 'error|Error' "$ROOT/.ffmpeg/build-$ARCH.log" | tail -n 20; die "ffmpeg build failed"; }

log "install to $FFMPEG_ROOT"
rm -rf "$FFMPEG_ROOT"
mkdir -p "$FFMPEG_ROOT"
make install >> "$ROOT/.ffmpeg/build-$ARCH.log" 2>&1 \
	|| die "ffmpeg install failed"
echo "$FFMPEG_VERSION" > "$stamp"

for lib in libavformat.a libavcodec.a libavutil.a; do
	[ -f "$FFMPEG_ROOT/lib/$lib" ] || die "expected $FFMPEG_ROOT/lib/$lib after install"
	printf '  %-16s %s bytes\n' "$lib" "$(wc -c < "$FFMPEG_ROOT/lib/$lib" | tr -d ' ')"
done
log "ffmpeg $FFMPEG_VERSION ready at $FFMPEG_ROOT"
