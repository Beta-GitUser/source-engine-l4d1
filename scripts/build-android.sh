#!/bin/sh
# Cross-compile the engine and the game modules for Android.
#
# Google ships the NDK for x86_64 hosts only, so this cannot run on the phone:
# it runs on any x86_64 Linux box, or in CI (.github/workflows/android.yml),
# and the resulting libraries are pushed to the device. See
# docs/L4D1/BUILDING.md.
#
# Usage:
#   sh scripts/build-android.sh                    # aarch64, NDK r10e, release
#   ARCH=armeabi-v7a API=19 sh scripts/build-android.sh
#   NDK_HOME=/opt/android-ndk-r10e sh scripts/build-android.sh   # no download
#   GAMES=hl2 sh scripts/build-android.sh          # different game code
#   PACK=1 sh scripts/build-android.sh             # also tar the libraries
#
# Environment:
#   NDK_HOME      root of an unpacked NDK (else NDK_VERSION is downloaded)
#   NDK_VERSION   default r10e - the revision scripts/waifulib/xcompile.py and
#                 scripts/build-android-armv7a.sh actually support
#   ARCH          aarch64 | armeabi-v7a | armeabi-v7a-hard | x86 | x86_64
#   TOOLCHAIN     NDK toolchain version, default 4.9 (r10e's gcc)
#   API           android API level, default 21 (required for 64-bit)
#   GAMES         game code to build, default left4dead
#   BUILD_TYPE    release | debug, default release
#   JOBS          parallel build jobs, default nproc
#   EXTRA_FLAGS   extra `waf configure` flags, e.g. "--disable-warns"
#   PACK=1        tar the produced libraries into dist/

set -e

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

ARCH=${ARCH:-aarch64}
TOOLCHAIN=${TOOLCHAIN:-4.9}
API=${API:-21}
GAMES=${GAMES:-left4dead}
BUILD_TYPE=${BUILD_TYPE:-release}
NDK_VERSION=${NDK_VERSION:-r10e}
NDK_HOME=${NDK_HOME:-${ANDROID_NDK_HOME:-}}
EXTRA_FLAGS=${EXTRA_FLAGS:-}
JOBS=${JOBS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)}

log() { printf '==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# A failing `waf configure` prints one line and writes the reason into
# build/config.log, which is exactly the file nobody finds afterwards. Dump the
# interesting part of it on the way out.
diagnose() {
	code=$?
	[ "$code" -eq 0 ] && return 0
	printf '\n--- build failed (exit %s) ---\n' "$code"
	if [ -f build/config.log ]; then
		printf -- '--- configure errors (build/config.log) ---\n'
		grep -iE 'error|fatal|cannot find|no such file|undefined reference|not found|unsupported' \
			build/config.log | tail -n 20 || tail -n 20 build/config.log || true
	fi
	if [ -f build-android.log ]; then
		printf -- '--- last lines ---\n'
		tail -n 10 build-android.log || true
	fi
	exit "$code"
}
trap diagnose EXIT

# ---------------------------------------------------------------------------
# 1. toolchain
# ---------------------------------------------------------------------------
if [ -z "$NDK_HOME" ]; then
	cache="$ROOT/.ndk"
	NDK_HOME="$cache/android-ndk-$NDK_VERSION"
	if [ ! -d "$NDK_HOME" ]; then
		url="https://dl.google.com/android/repository/android-ndk-$NDK_VERSION-linux-x86_64.zip"
		zip="$cache/android-ndk-$NDK_VERSION.zip"
		mkdir -p "$cache"
		log "downloading NDK $NDK_VERSION (x86_64 host package)"
		if command -v curl >/dev/null 2>&1; then
			curl -fL --retry 3 --connect-timeout 30 -o "$zip" "$url" \
				|| die "could not download $url - unpack an NDK and set NDK_HOME instead"
		elif command -v wget >/dev/null 2>&1; then
			wget -O "$zip" "$url" \
				|| die "could not download $url - unpack an NDK and set NDK_HOME instead"
		else
			die "need curl or wget to download the NDK, or set NDK_HOME"
		fi
		log "unpacking $zip"
		unzip -q -o "$zip" -d "$cache"
		rm -f "$zip"
	fi
fi
[ -d "$NDK_HOME" ] || die "NDK_HOME does not exist: $NDK_HOME"
NDK_HOME=$(cd "$NDK_HOME" && pwd)
export NDK_HOME ANDROID_NDK_HOME="$NDK_HOME"

ndk_rev="unknown"
if [ -f "$NDK_HOME/source.properties" ]; then
	ndk_rev=$(sed -n 's/^Pkg.Revision *= *//p' "$NDK_HOME/source.properties" | head -n1)
fi

case "$ARCH" in
	aarch64) abi=arm64-v8a ;;
	armeabi-v7a|armeabi-v7a-hard) abi=armeabi-v7a ;;
	*) abi=$ARCH ;;
esac

log "ndk $ndk_rev at $NDK_HOME"
log "target $ARCH ($abi), api $API, toolchain $TOOLCHAIN, $BUILD_TYPE"

# ---------------------------------------------------------------------------
# 2. sources
# ---------------------------------------------------------------------------
if [ ! -f waf ]; then
	die "no ./waf here - run this from a checkout of the repository"
fi
if [ -f .gitmodules ] && [ "${SKIP_SUBMODULES:-0}" != "1" ]; then
	log "submodules"
	git submodule update --init --recursive
fi
for dep in thirdparty ivp lib; do
	if [ -d "$dep" ] && [ -z "$(ls -A "$dep" 2>/dev/null)" ]; then
		die "$dep/ is empty - submodules did not populate (git submodule update --init --recursive)"
	fi
done

# ---------------------------------------------------------------------------
# 3. configure + build
# ---------------------------------------------------------------------------
log "configure"
./waf configure -T "$BUILD_TYPE" \
	--android="$ARCH,$TOOLCHAIN,$API" \
	--build-games "$GAMES" \
	--togles $EXTRA_FLAGS

log "build (-j$JOBS)"
./waf build -j"$JOBS"

# Modules are installed next to the engine on Android (PREFIX/lib/<abi>),
# unlike the desktop layout where they land in <gamedir>/bin.
if [ -n "$PREFIX_DIR" ]; then
	./waf install --destdir="$PREFIX_DIR"
fi

# ---------------------------------------------------------------------------
# 4. report
# ---------------------------------------------------------------------------
log "artefacts"
found=0
for name in libclient.so libserver.so liblauncher.so libgameui.so; do
	# One path per name; the build tree can contain stale copies.
	path=$(find . -name "$name" -type f -newermt '-6 hours' 2>/dev/null | head -n1)
	if [ -z "$path" ]; then
		path=$(find . -name "$name" -type f 2>/dev/null | head -n1)
	fi
	if [ -n "$path" ]; then
		printf '  %-16s %s  (%s bytes)\n' "$name" "$path" "$(wc -c <"$path" | tr -d ' ')"
		found=$((found + 1))
	fi
done
if [ "$found" -eq 0 ]; then
	printf '  no libraries found - the build may have used a different output layout\n'
	printf '  look under: %s/build/\n' "$ROOT"
else
	printf '\n  push these to the device and check them:\n'
	printf '    python3 -m l4d1ctl module install --from <dir> --force\n'
	printf '    L4D1_TARGET_MACHINE=%s python3 -m l4d1ctl check --engine <engine>\n' "$ARCH"
fi

if [ "${PACK:-0}" = "1" ]; then
	dist="$ROOT/dist/android-$abi"
	mkdir -p "$dist"
	for name in libclient.so libserver.so liblauncher.so libgameui.so; do
		path=$(find . -name "$name" -type f 2>/dev/null | head -n1)
		[ -n "$path" ] && cp "$path" "$dist/"
	done
	tar -czf "$ROOT/dist/l4d1-android-$abi.tar.gz" -C "$dist" .
	log "packed dist/l4d1-android-$abi.tar.gz"
fi
