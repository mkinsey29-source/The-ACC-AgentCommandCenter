# Build 9 continuation

Codex implementer, task ACC-APK9-20261021-01; October21,2026 America/New_York. Branch feature/android-build9-claude-github-20261021, base e77d4020c98150819fea761524fb4253da78360d / draft PR51, stacked and unmerged. Repository owns code; Drive continuity will point to this record. Repo3.2/module Android/Drive root3.2/ACC3.3 policies read. Routine Android development and personal-test delivery authorized; no separate-review prerequisite, no subagents/independent review claimed. Actual platform checks and PR35/production/store gates retained.

User requested both Claude simple setup and GitHub, then chose official browser handoff. ClaudeSetup opens https://claude.ai/code with official sign-in/GitHub steps, explicit external-session status, optional selected repository URL copy. No credentials or ACC/Sentry content sent automatically; ACC does not read/monitor Claude sessions. Existing advanced runtime retained.

GitHub tab browses public repositories or selected private repositories through an encrypted fine-grained token entered only in Connections. Contents read-only/expiry/selected repositories requested in GitHub browser. GET-only fixed api.github.com origin, no redirects/download URLs, explicit pagination, branch pinned to commit, bounded UTF-8 file previews. User selects one file and explicitly attaches to their next ChatGPT message. Queued attachment is account/project-bound and cleared on disconnect; sent file stays in local conversation history. No model-driven GitHub tools, push/merge, code execution or autonomous task execution. Do not describe this as OAuth sign-in.

| ID | Intended result | State / evidence / next action |
|---|---|---|
| APK9-01 | Supported simple Claude setup | Implemented official browser handoff selected by owner; live Claude account/task acceptance pending |
| APK9-02 | GitHub setup + repository access | Implemented public reads and encrypted fine-grained-token setup; private-account acceptance pending |
| APK9-03 | Repository context in ChatGPT | Implemented bounded pinned file preview and deliberate attachment; native/public and real account acceptance tracked separately |
| APK9-04 | Focused validation | Local13 GitHub contract checks,14 runtime/eight signer tests, typecheck/lint/providers/60native JSX roles passed after documented corrections; native workflow pending |
| APK9-05 | Deliver compatible numbered APK | Version9/0.9.0, same package/signing/data/OTA-off; package/native smoke/public hash pending |
| APK8-05 / APK7-04 | Actual ChatGPT completed reply | Completed: manual owner acceptance on Build8; earlier raw failure payload remains unknown |
| APK4-01 | Physical Back/data retention | Carryover: confirm Back/Sentry/tasks without uninstall; separate from emulator checks |
| APK4-09 | Older reuse publication403 | Cause open; later new-build publications passed; do not bypass actual rejection |

Commands, unset TYPESAFE_API_KEY before EVERY test:
`npm run test:github`, `npm run test:providers`, `npm run test:native-props`, `npm run typecheck`, `npm run lint` in mobile. Python unittest discovery from root: `python3 -m unittest discover -s tests -p test_mobile_runtime.py` and `... -p test_mobile_apk_signers.py`. Native workflow performs36 Kotlin units, strict package/signature/OTA/ABI/16KiB checks and public repository/file/attachment smoke on phone+tablet layouts. Never clear data on a real owner device; workflow pm clear applies only to disposable emulator.

Next agent: verify native/current source, candidate preservation and public hash; deliver install-over APK after gates. Then owner can open GITHUB, open owner/repository, choose branch and preview a file, Attach to next ChatGPT message, ask a question and Send using existing ChatGPT sign-in. For private repos: Connections→Create GitHub token in browser→selected repositories/Contents Read-only/expiry→paste only into ACC token field→Connect GitHub. For Claude: Claude Code tab→Open Claude Code in browser→official sign-in/GitHub setup/task. Do not request credentials/codes/callback URLs in chat.

Reviewer: inspect fixed-origin authenticated transport, disconnect/store races, pinning/path/encoding/bounds, explicit source sharing/account identity, native secure input/status/Back and no false connection state. Read lessons for failed checks. New native/public success is not proof of private account or Claude cloud task completion. Frozen spine unchanged; older PRs unmerged. Do not restart desktop reviews.
