# Lessons — ACC-APK5-20261021-01

- OPEN, observed Samsung Build4: accepted local callback followed by signed-out unfinished registration. Exact post-callback cause unknown; generic catch discards the failure stage. This does not prove the token endpoint rejected it or that ID validation failed.
- IMPLEMENTED AND NATIVE-TESTED, pending device validation: fixed-category diagnostics and allowlisted OAuth error codes, with bounded non-success HTTP response parsing. Never persist raw exception messages, OAuth response bodies or callback addresses in project records.
- Source inspection/public metadata: issuer, token endpoint, JWKS URL and RS256 match the current official OpenAI discovery metadata and SIWC documentation. No authenticated API request was performed. No API key is part of this owner-selected subscription flow.
- Local edit corrected before publication: broad version text replacement changed two unrelated dependency version strings in package-lock. Restored them from base; final lock changes only top-level/root package version. No dependency change intended.
- CARRY ACC-APK4-20261020-01: physical panel-open failure no longer observed; Back/data checks remain deferred. Exact original crash diagnosis still unproven.
- CARRY ACC-APK4-20261020-09: previous Actions release publication403; cause unproven. Preserve raw expensive APK before auxiliary checks. Do not rebuild it solely to retry verifiers.
- CARRY: live reply, refresh/revocation, upgrade/data retention, lifecycle/Keystore/SAF/Chromebook checks remain unverified; Claude simple subscription onboarding unfinished; task board manual only. Historical runner flakes and production/PR35 gates unchanged.

- BUILD5 DELIVERY VERIFIED: native workflow39679433238 overall SUCCESS (baseline job intentionally skipped). Native compile11m13s/653tasks;11unit tests and six UI checks per phone/tablet layout pass. Strict packaging/signing and public artifact hash match. No fresh independent review or live account acceptance follows.
- Actions publication: Build5 automatic publication succeeded at the source commit83fa7ae. Prior Build4 saved-candidate publication403 cause is still unproven; do not infer the old issue is diagnosed from this successful path.
- Tooling: initial connected GitHub wrapper argument-binding errors made no writes; discovered Composio schemas used successfully. Local/remote staged tree hashes matched before commit. Drive capture hit transient rateLimitExceeded403, then succeeded after delayed retry. This was an API quota error, not an automatic-review rejection.
