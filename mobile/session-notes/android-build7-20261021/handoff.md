# Build7 executable continuation

Codex implementer, task ACC-APK7-20261021-01, October 21, 2026 (America/New_York). Base `6e94b55eb9d14d97c3ac4eae65994e03c5eada7e`; branch `fix/android-build7-response-format-20261021`, stacked on unmerged draft PR48/Build6. Root/mobile v3.2 Android policy and owner standing authorization apply. No subagents or independent review claimed. Routine Android implementation/tests/workflow/personal-test publication need no separate review. Actual platform checks and PR35/production/store gates remain.

The observed safe diagnostic is `ACC-CHAT-RESPONSE-CONTENT_TYPE`: current code reaches HTTP200 and rejects the response before parsing. The diagnostic does not identify the actual returned header value. Source inspection found ACC unconditionally rejects an absent Content-Type. OpenAI's current official devkit explicitly documents that the direct route can return valid SSE with no Content-Type, and accepts that case while requiring completion. This is a confirmed compatibility defect and a matching explanation, not proof of the exact returned header or completed live inference.

Sources read October21: [official devkit responses.ts](https://github.com/openai/sign-in-with-chatgpt-devkit/blob/main/packages/local/src/responses.ts) and [models/inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference). Build7 permits missing/blank MIME metadata, validates the existing bounded SSE protocol, and requires response.completed with completed assistant output. Explicit incompatible MIME remains rejected before body parsing, with static JSON/HTML/TEXT/OTHER classification. No arbitrary header/body/exception text reaches the user. Production stream request explicitly asks for Accept:text/event-stream; body fields, bearer scope, official endpoint and no retry/redirect/billing fallback remain unchanged.

Local23 Kotlin/JUnit tests pass (18 existing + five new), including a controlled loopback OkHttp HTTP200 response without Content-Type, exact Accept/body headers, valid missing-header completion, wrong MIME no-body-read, partial/HTML/JSON/error rejection, and cancellation. This is protocol fixture evidence, not live account success. TypeScript/ESLint/provider checks,40 native roles and eight signing-parser tests passed. YAML/Bash/Python syntax and exact lock scope passed (only root package version changed). Native Gradle/build/package/signing/emulator/public hash checks pending. TYPESAFE_API_KEY unset before every test; Python uses unittest discovery. Version7/0.7.0; upgrade package/signer, local storage and OTA-off preserved. Frozen spine untouched.

| ID | Intended outcome | Current status / next action |
|---|---|---|
| APK7-01 | Handle documented missing header | Implemented; preserve terminal completion and reject explicit conflicting MIME |
| APK7-02 | Verify request/response boundary | 23 local units passed; require native same assertions |
| APK7-03 | Deliver compatible numbered APK | Build7 workflow pending; require native23, strict packaging/signing, phone/tablet panels and public download hash |
| APK7-04 | Confirm actual completed reply | Unverified; install OVER current app, keep sign-in, New conversation → short message |
| APK6-01 | Determine original live reply failure | Narrowed to MIME gate; exact header remains unknown |
| APK4-01 | Physical data/Back acceptance | Partial; confirm Sentry/tasks remain and Back works without uninstall |
| APK4-09 | Older reuse publication403 | Open cause; Build5/6 new-build publication succeeded |

Run android-native-smoke.yml at exact source. Preserve private raw candidate before verifier. If only scripts fail, reuse unchanged APK rather than rebuild; never publish before all gates pass. Before every test unset TYPESAFE_API_KEY; use Python unittest discovery. No automatic resend, billing/provider fallback or manual Termux default onboarding. Never request credentials, codes or callback URLs. If a reply completes, record actual evidence; do not infer from fixtures/model list. Simple Claude subscription setup unfinished.

A later reviewer can inspect streamRequest headers, missing vs conflicting MIME, HTTP/stream failure privacy, bounded parser and completion validation. Independent review is not a prerequisite for this routine Android slice. Do not restart desktop review campaigns or merge PR35/production/store changes.

Previous continuation: https://drive.google.com/file/d/16NtaNR15tkzlKF9BNOdg_Q_O6UnWM1Mt/view . New source/PR/artifact links will be recorded after build verification. Repository owns implementation; Drive copies are continuity records.
