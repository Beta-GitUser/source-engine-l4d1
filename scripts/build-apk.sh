#!/bin/sh
# Package a debug APK around the cross-compiled libraries.
#
# Why an APK and not a zip of .so files: an unrooted Android device will not run
# a library on its own. The dynamic linker only maps libraries into a process it
# creates, so the entry point has to be an installed package. This builds the
# package that launcher/android/apk describes - a plain Activity that loads the
# modules and reports how far it got.
#
# Usage:
#   sh scripts/build-apk.sh --libs <dir with .so> --out l4d1-debug.apk \
#                           [--arch aarch64] [--api 21] [--keystore file]
#
# Needs an Android SDK (not the NDK): build-tools for aapt/zipalign/apksigner/d8
# and one platform for android.jar. ANDROID_HOME or ANDROID_SDK_ROOT must point
# at it. With no --keystore a throwaway debug key is generated, which is right
# for a debug build and useless for publishing.

set -e

ROOT=$(cd "$(dirname "$0")/.." && pwd)
APK_SRC="$ROOT/launcher/android/apk"

ARCH=aarch64
API=21
LIBS=""
OUT="$ROOT/out/l4d1-debug.apk"
KEYSTORE=""

log() { printf '==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
	case "$1" in
		--libs) LIBS=$2; shift 2 ;;
		--out) OUT=$2; shift 2 ;;
		--arch) ARCH=$2; shift 2 ;;
		--api) API=$2; shift 2 ;;
		--keystore) KEYSTORE=$2; shift 2 ;;
		-h|--help) sed -n '2,20p' "$0"; exit 0 ;;
		*) die "unknown argument: $1" ;;
	esac
done

[ -n "$LIBS" ] || die "pass --libs <directory of .so files>"
[ -d "$LIBS" ] || die "no such directory: $LIBS"

# ---------------------------------------------------------------------------
# Locate the SDK pieces. Failures here are configuration, not build, problems.
# ---------------------------------------------------------------------------

SDK=${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}
if [ -z "$SDK" ]; then
	for candidate in "$HOME/Android/Sdk" /usr/local/lib/android/sdk /opt/android-sdk; do
		[ -d "$candidate" ] && SDK=$candidate && break
	done
fi
[ -n "$SDK" ] || die "ANDROID_HOME is not set and no SDK was found in the usual places"

# Java first: the SDK tools are Java programs, and "newest build-tools" plus
# "whatever JDK happens to be default" is how a step dies with an exit code and
# no sentence. The runner image defaults to JDK 11 while build-tools 35+ want 17,
# so 17 is preferred when it exists. javac 17 still accepts -source 8.
if [ -z "$L4D1_KEEP_JAVA" ]; then
	for candidate in "$JAVA_HOME_17_X64" "$JAVA_HOME_21_X64" "$JAVA_HOME_11_X64" "$JAVA_HOME"; do
		if [ -n "$candidate" ] && [ -x "$candidate/bin/javac" ]; then
			JAVA_HOME=$candidate
			break
		fi
	done
fi
if [ -n "$JAVA_HOME" ] && [ -d "$JAVA_HOME/bin" ]; then
	PATH="$JAVA_HOME/bin:$PATH"
	export JAVA_HOME PATH
	log "java        $JAVA_HOME"
fi

# build-tools: prefer the versions these tools are known to work in, then fall
# back to whatever exists, newest first. "Newest" alone is not a policy: aapt was
# dropped from later revisions, and the JDK a revision expects moves too.
PREFERRED="34.0.0 35.0.1 35.0.0 33.0.2 33.0.1 33.0.0 30.0.3"
CANDIDATES=""
for version in $PREFERRED; do
	[ -d "$SDK/build-tools/$version" ] && CANDIDATES="$CANDIDATES $SDK/build-tools/$version"
done
CANDIDATES="$CANDIDATES $(ls -1d "$SDK"/build-tools/* 2>/dev/null | sort -Vr)"

BUILD_TOOLS=""
for candidate in $CANDIDATES; do
	[ -x "$candidate/zipalign" ] || continue
	[ -x "$candidate/apksigner" ] || continue
	if [ -x "$candidate/d8" ] || [ -x "$candidate/dx" ]; then :; else continue; fi
	if [ -x "$candidate/aapt" ] || [ -x "$candidate/aapt2" ]; then :; else continue; fi
	BUILD_TOOLS=$candidate
	break
done
[ -n "$BUILD_TOOLS" ] || die "no usable build-tools under $SDK/build-tools - install build-tools;34.0.0"

AAPT="$BUILD_TOOLS/aapt"
AAPT2="$BUILD_TOOLS/aapt2"
ZIPALIGN="$BUILD_TOOLS/zipalign"
APKSIGNER="$BUILD_TOOLS/apksigner"
D8="$BUILD_TOOLS/d8"
[ -x "$D8" ] || D8="$BUILD_TOOLS/dx"
[ -x "$AAPT" ] || AAPT=""
[ -x "$AAPT2" ] || AAPT2=""

ANDROID_JAR=""
for candidate in "$SDK"/platforms/android-*/android.jar; do
	[ -f "$candidate" ] || continue
	case "$(basename "$(dirname "$candidate")")" in
		android-*) ANDROID_JAR=$candidate ;;
	esac
done
[ -n "$ANDROID_JAR" ] || die "no platforms/android-*/android.jar under $SDK - install platforms;android-33"

JAVAC=${JAVAC:-javac}
JAR=${JAR:-jar}
command -v "$JAVAC" >/dev/null 2>&1 || die "javac not found (install a JDK)"
command -v zip >/dev/null 2>&1 || die "zip not found"

log "SDK         $SDK"
log "build-tools $(basename "$BUILD_TOOLS")"
log "android.jar $ANDROID_JAR"
log "arch        $ARCH (API $API)"
log "packager    $([ -n "$AAPT" ] && echo aapt || echo "aapt2 ($(basename "$AAPT2"))")"
log "java        $(command -v java || echo none)"
log "javac       $(command -v javac || echo none)"

case "$ARCH" in
	aarch64|arm64-v8a) ABI=arm64-v8a ;;
	armeabi-v7a) ABI=armeabi-v7a ;;
	x86_64) ABI=x86_64 ;;
	*) die "unsupported arch: $ARCH" ;;
esac

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# ---------------------------------------------------------------------------
# Java
# ---------------------------------------------------------------------------

log "compiling Java"
mkdir -p "$WORK/classes" "$WORK/dex"
# android.jar goes on the classpath, not -bootclasspath: the latter was dropped
# from recent JDKs, and every JDK from 8 to 21 accepts this form. -source 8
# rather than --release for the same reason the SDK is not a JDK: --release
# insists on a matching platform image.
"$JAVAC" -nowarn -Xlint:none -source 8 -target 8 -classpath "$ANDROID_JAR" \
	-d "$WORK/classes" $(find "$APK_SRC/src" -name '*.java') \
	|| die "javac rejected the Java sources (see above)"
[ -d "$WORK/classes/com" ] || die "javac produced no classes - read the errors above"

log "dexing"
CLASSES=$(find "$WORK/classes" -name '*.class')
if [ "$(basename "$D8")" = "d8" ]; then
	"$D8" --lib "$ANDROID_JAR" --min-api "$API" --output "$WORK/dex" $CLASSES \
		|| die "d8 could not dex the classes (see above)"
else
	"$D8" --dex --output="$WORK/dex/classes.dex" $CLASSES \
		|| die "dx could not dex the classes (see above)"
fi
[ -f "$WORK/dex/classes.dex" ] || die "no classes.dex produced"

# ---------------------------------------------------------------------------
# Resources, native libraries, package
# ---------------------------------------------------------------------------

log "packaging"
UNSIGNED="$WORK/unsigned.apk"
if [ -n "$AAPT" ]; then
	"$AAPT" package -f -M "$APK_SRC/AndroidManifest.xml" -I "$ANDROID_JAR" \
		--min-sdk-version "$API" -F "$UNSIGNED" \
		|| die "aapt could not package the manifest"
else
	# aapt2 link, for build-tools that no longer ship aapt. Same output: an APK
	# whose only entry is the compiled manifest.
	"$AAPT2" link -o "$UNSIGNED" -I "$ANDROID_JAR" \
		--manifest "$APK_SRC/AndroidManifest.xml" \
		|| die "aapt2 could not link the manifest"
fi

STAGE="$WORK/stage"
mkdir -p "$STAGE/lib/$ABI"
# At the root, not in a subdirectory. The dex loader only looks at the root, and
# getting this wrong produces a package that signs and verifies perfectly and then
# refuses to install ("package appears to be invalid"). This was a real bug here.
cp "$WORK/dex/classes.dex" "$STAGE/classes.dex"
copied=0
for library in "$LIBS"/*.so; do
	[ -f "$library" ] || continue
	cp "$library" "$STAGE/lib/$ABI/"
	copied=$((copied + 1))
done
log "packaged $copied native libraries into lib/$ABI/"
[ "$copied" -gt 0 ] || die "no .so files in $LIBS - did the engine build?"

# Deterministic order, so two builds of the same input differ only in timestamps.
# The sed is belt and braces: zip happens to strip a leading "./" itself, but the
# entry names are not something to leave to a tool's good manners.
(cd "$STAGE" && find . -type f | sed 's|^\./||' | sort | zip -q -X "$UNSIGNED" -@)

log "aligning"
ALIGNED="$WORK/aligned.apk"
"$ZIPALIGN" -f -p 4 "$UNSIGNED" "$ALIGNED" || die "zipalign failed"

if [ -z "$KEYSTORE" ]; then
	KEYSTORE="$WORK/debug.keystore"
	log "generating a throwaway debug key"
	keytool -genkeypair -v -keystore "$KEYSTORE" -storetype JKS -alias androiddebugkey \
		-storepass android -keypass android -keyalg RSA -keysize 2048 -validity 10000 \
		-dname "CN=Android Debug,O=Android,C=US" >/dev/null 2>&1 \
		|| die "keytool failed - install a JDK"
fi
[ -f "$KEYSTORE" ] || die "no such keystore: $KEYSTORE"

log "signing"
mkdir -p "$(dirname "$OUT")"
rm -f "$OUT"
"$APKSIGNER" sign --ks "$KEYSTORE" --ks-key-alias androiddebugkey \
	--ks-pass pass:android --key-pass pass:android --out "$OUT" "$ALIGNED" \
	|| die "apksigner could not sign (see above)"

"$APKSIGNER" verify "$OUT" >/dev/null || die "the APK does not verify"

# A valid signature says nothing about whether the file will install, as the first
# APK built here demonstrated. This asks the install question.
log "checking the package would install"
python3 "$ROOT/scripts/verify-apk.py" "$OUT" --abi "$ABI" --expect-libs "$copied" \
	|| die "the package is structurally invalid - see above"

log "$OUT ($(wc -c <"$OUT" | tr -d ' ') bytes)"
cat <<EOF

Install it:
  adb install -r "$OUT"        # from a computer
  # or copy it to the phone and open it (allow "install unknown apps")

Then open "L4D1 debug": it loads every module it finds and reports, step by step,
whether bionic resolved it and whether CreateInterface answered. The transcript is
written to the app's files/probe.log and shown again on the next launch, so a
crash still leaves the evidence behind.
EOF
