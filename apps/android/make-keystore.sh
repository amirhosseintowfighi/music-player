#!/usr/bin/env bash
# One-time: the key the Android app is signed with, forever. Keep the file and the
# password somewhere safe — an app signed with a different key cannot update it.
#
#   bash apps/android/make-keystore.sh
#
# Prints what to put in GitHub (Settings → Secrets and variables → Actions) and the
# fingerprint to put in the server's env file as ANDROID_CERT_SHA256.
set -euo pipefail
cd "$(dirname "$0")"
[ -f android.keystore ] && { echo "android.keystore already exists; not overwriting it." >&2; exit 1; }
read -r -s -p "Keystore password (6+ characters): " PASS; echo
keytool -genkeypair -v -keystore android.keystore -alias noax -keyalg RSA -keysize 2048 \
  -validity 10000 -storepass "$PASS" -keypass "$PASS" -dname "CN=Noax, O=Noax, C=IR" >/dev/null
echo
echo "GitHub secrets to add:"
echo "  ANDROID_KEYSTORE_B64  = (the next line)"
base64 -w0 android.keystore; echo
echo "  ANDROID_KEYSTORE_PASSWORD = the password you just typed"
echo
echo "Server env file (.env), then redeploy:"
echo "  ANDROID_CERT_SHA256=$(keytool -list -v -keystore android.keystore -alias noax -storepass "$PASS" | sed -n 's/^.*SHA256: //p')"
