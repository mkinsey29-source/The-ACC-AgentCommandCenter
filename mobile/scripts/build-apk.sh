#!/usr/bin/env bash
set -euo pipefail
unset TYPESAFE_API_KEY
cd "$(dirname "$0")/.."
npm ci --no-audit --no-fund
unset TYPESAFE_API_KEY
npm run test:native-props
unset TYPESAFE_API_KEY
npm run typecheck
unset TYPESAFE_API_KEY
npm run lint
npx expo prebuild --platform android --no-install
cd android
unset TYPESAFE_API_KEY
./gradlew :acc-chatgpt:testDebugUnitTest :app:assembleRelease -PreactNativeArchitectures=arm64-v8a --no-daemon
cd ..
mkdir -p builds
cp android/app/build/outputs/apk/release/app-release.apk builds/ACC-Android-build-7.apk
sha256sum builds/ACC-Android-build-7.apk
