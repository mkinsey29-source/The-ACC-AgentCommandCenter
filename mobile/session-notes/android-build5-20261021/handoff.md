# Build 5 — executable handoff

Task ACC-APK5-20261021-01, Codex author, October 21, 2026 America/New_York. Base Build4 final head03eb38e209efc43424dc0aad72c9105887cfe810; branch fix/android-build5-signin-diagnostics-20261021. No independent review claimed. Parent PR46 remains unmerged. Android implementation/tests/personal APK delivery authorized. Preserve frozen acc/contracts.py, domain.py, state_authority.py, connectors.py; PR35 integration remains separately gated.

## Acceptance/checklist

| ID | Intended outcome | Status | Evidence / next action |
|---|---|---|---|
| APK5-01 | Identify Samsung post-callback failure | In progress | Build4 screenshots show received callback and unfinished registration; needs safe diagnostic from Build5 |
| APK5-02 | Safe sign-in diagnostics | Implemented, validation ongoing | SignInCompletion.kt and tests; no raw errors/credentials exposed |
| APK5-03 | Numbered compatible test APK | In progress | 5/0.5.0 configured; build/signature/package/OTA/16KiB/native layout checks pending |
| APK5-04 | Actual ChatGPT response | Deferred verification | Only complete after owner receives a model reply |
| APK4-01 | Samsung panel/upgrade acceptance | Partial evidence | Panel opens; confirm Back and Sentry/task retention |
| APK4-09 | Actions publication automation | Open | Prior HTTP403; no guessed cause |

## Next agent

Run focused native tests and the manual android-native-smoke.yml on this exact branch. Before EVERY test invocation unset TYPESAFE_API_KEY. Python: python3 -m unittest discover -s tests -p test_mobile_apk_signers.py; do not use dotted modules. Mobile: npm run typecheck, lint, test:providers, test:native-props. Native workflow compiles both ARM64/x86_64 and runs all Kotlin unit tests, strict packaging and six UI checks in EACH Android16 phone/tablet layout. Retain the raw candidate before verifiers. Use candidate_tag only if application sources are identical. Confirm public download hash and exact artifact source before delivery. Do not overwrite numbered releases.

After delivery: install OVER the existing app, repeat Continue with ChatGPT and report only ACC-SIGNIN diagnostic text. No credentials, tokens, login codes or browser callback URLs in chat/Drive/source. If successful, select a model/send one message; otherwise fix the now-identified concrete failure. Claude simple setup unfinished; no Termux as default or silent API billing. Confirm Back and saved Sentry/tasks.

## Reviewer

Review only the Android completion/diagnostic/version/workflow diff against Build4; verify all original identity, scope and account-preservation checks remain. Probe safe diagnostic classification, unknown/secret-bearing OAuth errors, every failed completion stage preventing saving, and successful ordering. Native fixtures are not live account/Keystore/lifecycle proof. Do not restart historical desktop review campaigns or merge/deploy production.
