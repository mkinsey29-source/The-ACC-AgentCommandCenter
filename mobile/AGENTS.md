This is an Expo/React Native mobile application. Prioritize mobile-first patterns, performance, and cross-platform compatibility.

## Owner-approved Android workflow — 2026-10-19

Owner clarification, 2026-10-21: routine Android implementation, workflow updates, tests and numbered personal-test APK delivery need no separate independent review or repeated owner approval. Continue authorized work to completion. Preserve validation and artifact checks and report any actual platform rejection; project instructions cannot disable platform tool checks. Production/store publication and PR35 integration retain their separate gates.

Follow the Android scope and standing authorization in the repository AGENTS.md. Deliver numbered installable APKs with OTA disabled. Use local Android builds when the build environment is available; EAS is an optional build service, not a publication authorization. Do not enable EAS Update or submit to a store without a separate request. Preserve the installed package/signing identity and existing workspace/Sentry data when preparing upgrades.

Default onboarding must not expect the owner to install Termux, Linux or CLIs manually. Prefer supported account sign-in using the system browser; explain provider-specific limitations honestly. Keep optional runtime pairing under advanced setup. Do not claim working provider access until an actual account request completes. Routine implementation, focused checks, commits/pushes, draft PRs and personal test APK delivery are already authorized; historical desktop review rounds do not block mobile-only work.

## Expo has changed — do not trust your training data

Expo ships breaking changes every SDK release. APIs you remember are likely renamed, moved, or removed. Before writing any code that touches an Expo, EAS, or React Native API:

1. Read the major version of the `expo` package in `package.json`.
2. Fetch the matching versioned docs: `https://docs.expo.dev/versions/v<major>.0.0/`
3. For anything else, fetch https://docs.expo.dev/llms.txt — an index of all Expo docs with corrections to common LLM misconceptions. Follow its links to the specific page you need; never answer from memory.

## Commands

Use `bunx` instead of `npx` if the project uses bun (`bun.lock` present).

```bash
npx expo install <package>  # ALWAYS use instead of npm/yarn/pnpm/bun add — resolves SDK-compatible versions
npx expo start              # start the dev server
npx expo lint               # lint
npx tsc --noEmit            # typecheck
npx expo-doctor             # diagnose dependency and config issues
npx expo install --fix      # fix incompatible package versions
```

Run lint and typecheck before declaring any task done.

## Navigation & Routing

- Use **Expo Router** for all navigation. Routes live in `src/app/` — every file there is a screen, `_layout.tsx` files define navigators. Keep non-route code (components, hooks, utils) outside `src/app/`.
- Import `Link`, `router`, and `useLocalSearchParams` from `expo-router`.
- Docs: https://docs.expo.dev/router/introduction.md

## Building with EAS

EAS may build and sign an APK in the cloud when needed (`eas build`). Store submission and OTA updates are not part of the authorized personal-testing workflow; OTA remains disabled. Run EAS CLI as `bunx eas-cli <command>` in Bun projects, or `npx eas-cli@latest <command>` otherwise; substitute that for bare `eas` in docs examples.
Docs: https://docs.expo.dev/eas/index.md

## Rules

- If `ios/` and `android/` directories do not exist, they are generated (Continuous Native Generation). Never create or edit them by hand — configure native behavior in `app.json` and config plugins.
- Expo Go only includes its bundled native modules. After adding a library with native code, the app needs a development build: `npx expo run:ios|android` locally, or `eas build --profile development`.
- Prefer recommended Expo modules over third-party libraries, and check your available skills before adding dependencies. Docs: https://docs.expo.dev/versions/latest/index.md
