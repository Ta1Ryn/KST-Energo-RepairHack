#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
export JAVA_HOME="$PWD/.tools/java"
export ANDROID_HOME="$PWD/.tools/android-sdk"
export GRADLE_USER_HOME="$PWD/.tools/gradle-cache"
exec RepairHack/gradlew -p RepairHack assembleDebug --console=plain "$@"
