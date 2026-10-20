#!/usr/bin/env bash
set -euo pipefail
unset TYPESAFE_API_KEY
cd "$(dirname "$0")/.."
npm ci --no-audit --no-fund
npm run typecheck
npm run lint
npx expo prebuild --platform android --no-install
cd android
./gradlew :acc-chatgpt:testDebugUnitTest :app:assembleRelease -PreactNativeArchitectures=arm64-v8a --no-daemon
cd ..
mkdir -p builds
cp android/app/build/outputs/apk/release/app-release.apk builds/ACC-Android-build-3.apk
sha256sum builds/ACC-Android-build-3.apk
