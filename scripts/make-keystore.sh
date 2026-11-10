#!/bin/sh
# Create the release keystore for the Android launcher.
#
# Do this once, now, and back the file up somewhere you will still have in five
# years. A release keystore cannot be recreated: if you lose it, you cannot
# publish an update to an app signed with it. There is no recovery path.
#
# Usage:
#   sh scripts/make-keystore.sh                  # prompts for the password
#   KEYSTORE=~/keys/l4d1-release.jks sh scripts/make-keystore.sh
#   ALIAS=l4d1 DAYS=10000 KEYSIZE=4096 sh scripts/make-keystore.sh
#
# Then follow the printed instructions to store it as GitHub secrets.

set -e

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

KEYSTORE=${KEYSTORE:-l4d1-release.jks}
ALIAS=${ALIAS:-l4d1}
DAYS=${DAYS:-10000}
KEYSIZE=${KEYSIZE:-4096}
DNAME=${DNAME:-"CN=Left 4 Dead 1 (Android port), OU=l4d1, O=l4d1, C=NL"}

log() { printf '==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

command -v keytool >/dev/null 2>&1 \
	|| die "keytool not found - install a JDK (Debian/Ubuntu: default-jdk-headless)"

case "$KEYSTORE" in
	*/*) mkdir -p "$(dirname "$KEYSTORE")" ;;
esac

if [ -e "$KEYSTORE" ]; then
	die "$KEYSTORE already exists - refusing to overwrite a signing key"
fi

log "creating $KEYSTORE (alias $ALIAS, RSA $KEYSIZE, $DAYS days)"
# No -storepass / -keypass on purpose: the password is typed at the prompt, so
# it stays out of shell history and out of the process list.
keytool -genkeypair -v \
	-keystore "$KEYSTORE" \
	-alias "$ALIAS" \
	-keyalg RSA \
	-keysize "$KEYSIZE" \
	-validity "$DAYS" \
	-storetype JKS \
	-dname "$DNAME"

log "certificate"
keytool -list -v -keystore "$KEYSTORE" -alias "$ALIAS" \
	| grep -E 'Alias name|Valid from|SHA1:|SHA256:' || true

b64="$KEYSTORE.base64"
base64 < "$KEYSTORE" | tr -d '\n' > "$b64"

cat <<EOF

Next: back it up, then hand it to CI as secrets.

  cp "$KEYSTORE" /somewhere/you/trust/and/will/find/again/
  # and, if you keep a password manager: store the password there too

GitHub secrets (Settings -> Secrets and variables -> Actions -> Secrets):

  gh secret set ANDROID_KEYSTORE_BASE64 < "$b64"
  gh secret set ANDROID_KEYSTORE_PASSWORD    # then paste the store password
  gh secret set ANDROID_KEY_ALIAS            # then type: $ALIAS
  gh secret set ANDROID_KEY_PASSWORD         # the key password (same, usually)

Wipe the temporary copy of the key material:

  rm -f "$b64"

Which folder it is in:

  $ROOT/$KEYSTORE     (ignored by git; never commit it)

EOF
