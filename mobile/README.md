# ACC Android · build 3

Current personal test: [ACC Android Build 7](https://github.com/mkinsey29-source/The-ACC-AgentCommandCenter/releases/download/android-build-7/ACC-Android-build-7.apk). Install over the existing app and keep the current sign-in. Start **New conversation** and send a short message. Build7 fixes the direct-route missing response Content-Type compatibility case and explicitly requests an event stream. A reply still requires terminal completion; actual live provider access remains unverified. If it fails report only the safe `ACC-CHAT-…` diagnostic and request ID. Native phone/tablet checks, 23 native unit tests and packaging/signing checks pass; OTA remains off. Simple Claude subscription onboarding remains unfinished. [Current continuation packet](session-notes/android-build7-20261021/handoff.md).

Previous Build5/6 sign-in/reply diagnostics are included and superseded by Build7.

React Native + Expo standalone Android workspace. APK iterations are the primary feedback loop. OTA updates are disabled. The existing Python ACC and desktop/web surfaces remain intact.

## Try it

Build 3 adds a direct ChatGPT account connection: install `ACC-Android-build-3.apk`, open **Connections → Continue with ChatGPT**, finish sign-in and consent on the official browser page, then return to ACC to choose a model and send a message. This path needs no Termux, CLI, API key or separate runtime. Live tablet/account acceptance remains pending; successful build and fixture checks are not proof of provider access. See [direct account sign-in](DIRECT-ACCOUNT-SIGNIN.md).

Claude's easy subscription setup remains unfinished. Its existing official CLI connection and optional Codex runtime are available under **Advanced: use an existing runtime**; the [runtime guide](runtime/README.md) applies only to those optional connections. No automatic API billing or provider fallback occurs. The direct connection does not import ChatGPT chats or memory.

The local Markdown feature is **Sentry**. **Memory → Choose folder** copies the five notes into a new `Sentry-<timestamp>` folder, verifies the copies, then switches. Existing saved folder references and notes are reused on upgrade, including folders created by Build 1 under the older name. Existing notes and headings are not rewritten. OTA updates remain disabled; install APK updates over the existing app to keep app-private data.

The previously released [Build 1](https://github.com/mkinsey29-source/The-ACC-AgentCommandCenter/releases/tag/android-build-1) is the offline workspace with the earlier Second Brain labels. The user reported successful installation, note persistence after reopening, folder creation and task creation. Those observations do not establish reboot/grant recovery or live provider access.

Default notes live in app-private device storage; uninstall removes app-private data. Choose a separate local folder to retain readable Markdown outside the app. Workspace tasks/settings remain in app-private AsyncStorage. The five-note vault is currently shared across the workspace, not separate per project. No Google Drive API integration or automatic sync runs.

## What works

- Tablet side rails and compact phone layout, Chat/Agent/Task/Terminal/Memory views.
- Native direct ChatGPT sign-in and streamed text chat implementation, account-specific models, separate project/account histories, native protected token storage, explicit cancellation and interruption without automatic resend. Real account/device acceptance is pending.
- Optional paired runtime for Claude Code and Codex under Advanced; existing histories and pairing are preserved.
- Runtime pairing stored through Expo SecureStore on Android; loopback HTTP or trusted HTTPS only; credential-bearing requests refuse redirects.
- Projects, local manual tasks, status changes and recent activity persisted on device.
- Sentry with five local Markdown notes: AGENTS.md, consolidated_summary.md, session_summary.md, lessons.md and handoff.md.
- Read-before-save conflict detection, local pre-write recovery copies, readback verification and unsaved-draft protection when changing notes/folders.
- A local console with `help`, `status`, `tasks`, `clear`.

## Still to connect

Direct ChatGPT sign-in/inference requires actual account/device acceptance. Optional Termux/PRoot compatibility is also unverified; fixture tests are not live model proof. Real task-agent execution, a managed on-device runtime installer, PTY, Git/worktrees, notification/background runtime, automatic handoffs, per-project vaults, existing-vault import and recovery-copy UI remain future slices. Local console commands do not execute a shell. Direct ChatGPT credentials stay in native encrypted storage; optional runtime provider credentials stay in their official tools. Manual task status is a planning label, not execution/test evidence. Paid/commercial subscription integration remains a separate vendor-authorization requirement; this personal/local test does not establish it.

The vault conflict check is not a cross-process lock. SAF providers do not promise atomic replacement; backups mitigate failed writes but do not establish crash-safe multi-writer coordination. Physical Android folder grants, process death, reboot, low storage and device behavior require actual device acceptance.

## Build

Requirements: Node >=22.13, a full JDK17, Android SDK (including platform37.0/build-tools37.0.0), NDK27.1.12297006/CMake3.22.1, accepted SDK licenses and dependency network access. Use Expo58/RN0.88 defaults: the compiled APK reports compile SDK37, target36 and minimum24. The available SDK package is named `platforms;android-37.0`; asking for `platforms;android-37` failed. Gradle installed the matching platform/build tools automatically after license acceptance. No forced compile-SDK downgrade is used.

```sh
cd mobile
unset TYPESAFE_API_KEY
npm ci
npm run typecheck
npm run lint
npm run test:providers
npx expo prebuild --platform android --no-install
cd android
./gradlew :acc-chatgpt:testDebugUnitTest :app:assembleRelease -PreactNativeArchitectures=arm64-v8a --no-daemon
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

Build8 is in preparation: assemble streamed text through terminal completion and support the official stream framing. [Build8 continuation](session-notes/android-build8-20261021/handoff.md).
