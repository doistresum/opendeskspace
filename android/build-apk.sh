#!/bin/sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
SDK="${ANDROID_SDK_ROOT:-$HOME/.local/opt/android-sdk}"
JAVA_HOME="${JAVA_HOME:-$HOME/.local/opt/jdk}"
BUILD_TOOLS="$(echo "$SDK"/build-tools/* | awk '{print $1}')"
PLATFORM="$(echo "$SDK"/platforms/android-* | awk '{print $NF}')"
ANDROID_JAR="$PLATFORM/android.jar"
OUT="$ROOT/android/build"
APP="$ROOT/android"

export JAVA_HOME
export PATH="$JAVA_HOME/bin:$PATH"

rm -rf "$OUT"
mkdir -p "$OUT/classes"
javac -classpath "$ANDROID_JAR" -d "$OUT/classes" "$APP/src/com/desklink/viewer/MainActivity.java"
"$BUILD_TOOLS/d8" --min-api 14 --lib "$ANDROID_JAR" --output "$OUT" "$OUT/classes/com/desklink/viewer/"*.class
"$BUILD_TOOLS/aapt" package -f -M "$APP/AndroidManifest.xml" -S "$APP/res" -I "$ANDROID_JAR" -F "$OUT/unsigned.apk"
(cd "$OUT" && "$JAVA_HOME/bin/jar" uf unsigned.apk classes.dex) 2>/dev/null || (cd "$OUT" && zip -q -j unsigned.apk classes.dex)
if [ ! -f "$APP/debug.keystore" ]; then
  keytool -genkeypair -keystore "$APP/debug.keystore" -storepass android -keypass android \
    -alias desk -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=Open Deskspace"
fi
"$BUILD_TOOLS/apksigner" sign \
  --v1-signing-enabled true \
  --v2-signing-enabled true \
  --ks "$APP/debug.keystore" \
  --ks-pass pass:android \
  --key-pass pass:android \
  --out "$APP/OpenDeskspace.apk" \
  "$OUT/unsigned.apk"
echo "Built $APP/OpenDeskspace.apk"
