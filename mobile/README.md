# ACC Android · build 1

React Native + Expo standalone Android workspace. APK iterations are the primary feedback loop. OTA updates are disabled. The existing Python ACC and desktop/web surfaces remain intact.

## Try it

Install `ACC-Android-build-1.apk` on an ARM64 Android device. Open ACC, create a project, add a task and change its manual status. Close/reopen the app to check persistence. In Memory, edit a Markdown note and save it. On Android, **Choose folder** copies the five current notes into a new `ACC-SecondBrain-<timestamp>` child and switches to it after verifying every copy. Select an on-device folder for offline files; Android's picker may also offer cloud providers. Existing folders/files are never replaced by that operation.

Default notes live in app-private device storage; uninstall removes app-private data. Choose a separate local folder to retain readable Markdown outside the app. Workspace tasks/settings remain in app-private AsyncStorage. The five-note vault is currently shared across the workspace, not separate per project. No Google Drive API integration or automatic sync runs.

## What works

- Tablet side rails and compact phone layout, Agent/Task/Terminal/Memory views.
- Projects, local manual tasks, status changes and recent activity persisted on device.
- Local Markdown Second Brain: AGENTS.md, consolidated_summary.md, session_summary.md, lessons.md and handoff.md.
- Read-before-save conflict detection, local pre-write recovery copies, readback verification and unsaved-draft protection when changing notes/folders.
- A local console with `help`, `status`, `tasks`, `clear`.

## Still to connect

Providers, real agent execution, PTY/Termux, Git/worktrees, notification/background runtime, automatic handoffs, per-project vaults, existing-vault import and recovery-copy UI are future slices. Provider panels accurately show disconnected. Local console commands do not execute a shell. There are no API keys, account sessions or working subscription connections in this build. Manual task status is a planning label, not execution/test evidence.

The vault conflict check is not a cross-process lock. SAF providers do not promise atomic replacement; backups mitigate failed writes but do not establish crash-safe multi-writer coordination. Physical Android folder grants, process death, reboot, low storage and device behavior require actual device acceptance.

## Build

Requirements: Node >=22.13, a full JDK17, Android SDK platform36/build-tools36.0.0, NDK27.1.12297006/CMake3.22.1, network access for dependencies. Expo58/RN0.88 defaults to compile SDK37; build-properties currently selects compile/target36 because the available Android SDK repository did not offer37. Native compilation determines whether that override is viable; do not infer compatibility from a JavaScript bundle.

```sh
cd mobile
unset TYPESAFE_API_KEY
npm ci
npm run typecheck
npm run lint
npx expo prebuild --platform android --no-install
cd android
./gradlew :app:assembleRelease -PreactNativeArchitectures=arm64-v8a --no-daemon
```

Or use `bash scripts/build-apk.sh`. Export ANDROID_HOME and JAVA_HOME for the installed SDK/JDK. Local native builds require no Expo account. An optional EAS preview profile is included; no cloud build or submission is triggered automatically. The generated Expo release template uses a development signing key. This is an internal test APK, not a production signing/release setup. `android/` is generated/ignored; use app config/plugins for native settings.

```sh
unset TYPESAFE_API_KEY
npx expo export --platform web --output-dir web-dist
npx expo export --platform android
```

Browser export is a UI/persistence preview and cannot validate Android SAF or native execution.

## Reuse

See [REUSE.md](REUSE.md) for observed source candidates. No external application source was copied into build1. Existing code informs the boundaries; runtime/native bridges can be ported as useful app slices rather than requiring whole-project migration.
