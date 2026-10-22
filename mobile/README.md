## Delivered Build 9 — verified final state

[Download APK](https://github.com/mkinsey29-source/The-ACC-AgentCommandCenter/releases/download/android-build-9/ACC-Android-build-9.apk) · [verification metadata](https://github.com/mkinsey29-source/The-ACC-AgentCommandCenter/releases/download/android-build-9/build9-verification.json) · [draft PR52](https://github.com/mkinsey29-source/The-ACC-AgentCommandCenter/pull/52), unmerged. Source/verifier99376eb0d33f528a1e8f5aae9dd81af480413fc9. Public download independently verified52,355,962 bytes/SHA256 `ebeb2f6b03a0ba561ffec57074909d65fb339301db08b9cdf6b36281a5889e87`, matching metadata and GitHub asset digest. Metadata digest14447a05ec30eb9567a57cbe05bd426f171bff70e1bc1a43a8968738ba2e2c41; compiled new-feature strings verified in the downloaded Hermes bundle.

[Native39711591714](https://github.com/mkinsey29-source/The-ACC-AgentCommandCenter/actions/runs/39711591714) succeeded:36 Kotlin units/zero failures/errors/skips, strict version/signing/OTA/backup/ABI/16KiB/Hermes checks, ten native assertions per phone/tablet layout (20 total), including actual public repository/README read, preview, attachment and removal. Native compile10m22s/653tasks. Same certificate as Build3, version9/0.9.0, packagecom.agentcommandcenter.acc,48 ARM64/x86_64 native libraries, Hermes99. CI39711593451 passed553 tests per Python version, one3.10skip.13 GitHub client checks,14 runtime tests, eight signer tests, TypeScript/ESLint/provider/60 native-role checks passed. Frozen spine unchanged.

Claude default is the owner-selected official browser handoff, with separate Claude sign-in/GitHub authorization/task session. ACC does not read or claim a Claude session. GitHub supports public repositories or encrypted selected-repository fine-grained token setup, pinned UTF-8 previews and explicit next-message attachments to ChatGPT. It is read-only browsing/context; no autonomous tools, pushes or merges. Private-account read, Claude cloud task completion, and an actual model reply about an attached file remain owner acceptance checks. Build8 plain ChatGPT live replies were accepted. Physical Back/data retention/lifecycle/SAF and production gates remain separate.

Install OVER Build8, never uninstall. Keep existing ChatGPT sign-in, Sentry and tasks. Open Claude Code→Open Claude Code in browser for official setup. Open GITHUB→repository→branch/file→Attach to next ChatGPT message, then ask a question and Send. Private repos: Connections→Create GitHub token in browser→selected repositories/Contents Read-only/expiry→paste only into ACC token field→Connect GitHub. Never put tokens/codes/callback URLs in chat.

## Earlier build records and implementation checkpoints retained

# Current Android slice — Build 9

Claude's default setup now opens official Claude Code in the browser, as selected by the owner. Sign-in, GitHub authorization and tasks run there; ACC does not claim an in-app Claude session. No manual terminal/computer required for that route.

The GitHub tab reads public repositories without sign-in. For private repositories, create a fine-grained selected-repository token with Contents: Read-only and an expiry, paste only into Connections, then connect. Android encrypts the token in SecureStore. Choose a repository/branch, preview a UTF-8 file up to30KB, and explicitly attach it to the next ChatGPT message. Files are pinned to a commit and are sent only when Send is pressed; sent content remains in local conversation history. This is repository browsing/context, not autonomous GitHub tools or push/merge.

Build8 live ChatGPT reply acceptance passed. Build9 native/package/public delivery and new account checks are tracked in [handoff](session-notes/android-build9-20261021/handoff.md). Preserve signing, install over the current app, keep data/OTA-off.

## Earlier build records retained

# ACC Android · build 3

Current personal test: [ACC Android Build 8](https://github.com/mkinsey29-source/The-ACC-AgentCommandCenter/releases/download/android-build-8/ACC-Android-build-8.apk). Install over the existing app and keep the current sign-in. Start **New conversation** and send a short message. Build8 collects reply chunks through terminal completion, accepts compact completed events and fixes SSE framing compatibility. Actual completed live provider access remains unverified. If it fails report only the safe `ACC-CHAT-…` diagnostic and request ID. Native phone/tablet checks,36 native units and packaging/signing checks pass; OTA remains off. Simple Claude subscription onboarding remains unfinished. [Current continuation packet](session-notes/android-build8-20261021/handoff.md).

Previous Build5/6/7 sign-in/reply diagnostics are included and superseded by Build8.

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
