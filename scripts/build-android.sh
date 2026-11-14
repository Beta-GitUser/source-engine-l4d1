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
USE_ENV_NDK=${USE_ENV_NDK:-0}
EXTRA_FLAGS=${EXTRA_FLAGS:-}
JOBS=${JOBS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)}

log() { printf '==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# Breadcrumbs. The failing step is not obvious from a log nobody can download,
# so each section records that it started and finished; on failure those lines
# travel as one annotation and say where the run stopped.
STEPS=$ROOT/.ci-steps.log
: > "$STEPS"
step() { printf '%s\n' "$*" >> "$STEPS"; }

# A failing `waf configure` prints one line and writes the reason into
# build/config.log, which is exactly the file nobody finds afterwards. Dump the
# interesting part of it on the way out.
diagnose() {
	code=$?
	[ "$code" -eq 0 ] && return 0
	printf '\n--- build failed (exit %s) ---\n' "$code"
	# The job log is an artifact, and an artifact lives on storage that restricted
	# networks cannot reach; the annotations are the channel that always arrives,
	# and they are one line each. So the tail travels as a single line, cut down
	# to what fits, with '|' where the newlines were.
	if [ -f build-android.log ]; then
		printf '::error::log lines: %s\n' "$(grep -ac '' build-android.log)"
		printf '::error::log errors: %s\n' \
			"$(grep -aE 'error:|Error [0-9]+|Traceback|fatal|No such file' build-android.log \
				| grep -av 'sysroot=' | tail -n 6 | cut -c1-300 | tr '\n' '|')"
		printf '::error::log tail: %s\n' \
			"$(tail -n 40 build-android.log | cut -c1-200 | tr '\n' '|')"
	fi
	if [ -f build/config.log ]; then
		printf '::error::config tail: %s\n' \
			"$(tail -n 8 build/config.log | cut -c1-240 | tr '\n' '|')"
	fi
	printf '::error::steps: %s\n' "$(tr '\n' '|' < "$STEPS")"
	for f in "$ROOT"/.ffmpeg/configure-*.log "$ROOT"/.ffmpeg/build-*.log; do
		[ -f "$f" ] || continue
		printf '::error::%s: %s\n' "$(basename "$f")" \
			"$(tail -n 6 "$f" | cut -c1-200 | tr '\n' '|')"
	done
	if [ -f build/config.log ]; then
		printf -- '--- configure errors (build/config.log) ---\n'
		grep -iE 'error|fatal|cannot find|no such file|undefined reference|not found|unsupported|unknown' \
			build/config.log | tail -n 20 || tail -n 20 build/config.log || true
		# The interesting failure is often a Python traceback, which matches none
		# of those patterns. Its last lines are what name the line of wscript.
		printf -- '--- tail of build/config.log ---\n'
		tail -n 25 build/config.log || true
	fi
	if [ -f build-android.log ]; then
		printf -- '--- compiler/linker errors ---\n'
		# waf dumps the failing command line as a Python list, which buries the
		# one line that matters. Filter to error lines and drop the dumps.
		grep -aE 'error:|fatal error|undefined reference|Error [0-9]+|No such file|cannot find|not found' \
			build-android.log | grep -av 'sysroot=' | grep -av "^\['" | tail -n 15 || true
		printf -- '--- last lines ---\n'
		tail -n 5 build-android.log || true
	fi
	exit "$code"
}
trap diagnose EXIT

# ---------------------------------------------------------------------------
# 1. toolchain
# ---------------------------------------------------------------------------
# xcompile.py only understands NDK r10e, r19c and r20 (it still injects
# gnu-libstdc++ 4.9 paths). Build images ship a much newer NDK and export
# ANDROID_NDK_HOME, so an inherited NDK must not silently win: it fails
# configure with "Unknown NDK revision: 27".
ndk_major() {
	if [ -f "$1/source.properties" ]; then
		sed -n 's/^Pkg.Revision *= *//p' "$1/source.properties" | head -n1 | cut -d. -f1
	elif [ -f "$1/RELEASE.TXT" ]; then
		echo 10
	else
		echo unknown
	fi
}

if [ -n "$NDK_HOME" ] && [ "$USE_ENV_NDK" != "1" ]; then
	inherited_major=$(ndk_major "$NDK_HOME")
	case "$inherited_major" in
		10|19|20) ;;
		*)
			printf 'note: ignoring NDK %s at %s - xcompile.py supports r10e, r19c, r20\n' \
				"$inherited_major" "$NDK_HOME" >&2
			printf 'note: fetching NDK %s instead (USE_ENV_NDK=1 keeps the inherited one)\n' \
				"$NDK_VERSION" >&2
			NDK_HOME=""
			ANDROID_NDK_HOME=""
			;;
	esac
fi

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

ndk_rev=$(ndk_major "$NDK_HOME")
case "$ndk_rev" in
	10|19|20) ;;
	*) printf 'warning: NDK revision %s is outside what xcompile.py knows (r10e, r19c, r20)\n' \
		"$ndk_rev" >&2 ;;
esac

case "$ARCH" in
	aarch64) abi=arm64-v8a ;;
	armeabi-v7a|armeabi-v7a-hard) abi=armeabi-v7a ;;
	*) abi=$ARCH ;;
esac

log "ndk $ndk_rev at $NDK_HOME"
log "target $ARCH ($abi), api $API, toolchain $TOOLCHAIN, $BUILD_TYPE"

# ---------------------------------------------------------------------------
# 1b. ffmpeg - the Bink decoder video_bink links against
# ---------------------------------------------------------------------------
# Third-party, so it is a separate script with its own cache: a finished
# .ffmpeg/<arch> makes this a no-op, and FFMPEG=0 builds the engine without the
# video module at all (which is what the tree did before this existed).
FFMPEG_VERSION=${FFMPEG_VERSION:-n4.4.4}
export FFMPEG_VERSION
step "ffmpeg start"
if [ "${FFMPEG:-1}" = "1" ]; then
	log "ffmpeg $FFMPEG_VERSION for $ARCH"
	FFMPEG_ROOT=${FFMPEG_ROOT:-$ROOT/.ffmpeg/$ARCH}
	export FFMPEG_ROOT
	sh "$ROOT/scripts/build-ffmpeg-android.sh"
else
	log "ffmpeg disabled (FFMPEG=0) - libvideo_bink.so will not be built"
	FFMPEG_ROOT=""
	export FFMPEG_ROOT
fi

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
step "ffmpeg ok"
log "configure"
step "configure start"
./waf configure -T "$BUILD_TYPE" \
	--android="$ARCH,$TOOLCHAIN,$API" \
	--build-games "$GAMES" \
	--togles $EXTRA_FLAGS

step "configure ok"
log "build (-j$JOBS)"
step "build start"
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
