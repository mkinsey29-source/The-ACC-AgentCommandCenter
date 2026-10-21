# Lessons — ACC-APK5-20261021-01

- OPEN, observed Samsung Build4: accepted local callback followed by signed-out unfinished registration. Exact post-callback cause unknown; generic catch discards the failure stage. This does not prove the token endpoint rejected it or that ID validation failed.
- IMPLEMENTED, pending device validation: fixed-category diagnostics and allowlisted OAuth error codes, with bounded non-success HTTP response parsing. Never persist raw exception messages, OAuth response bodies or callback addresses in project records.
- Source inspection/public metadata: issuer, token endpoint, JWKS URL and RS256 match the current official OpenAI discovery metadata and SIWC documentation. No authenticated API request was performed. No API key is part of this owner-selected subscription flow.
- Local edit corrected before publication: broad version text replacement changed two unrelated dependency version strings in package-lock. Restored them from base; final lock changes only top-level/root package version. No dependency change intended.
- CARRY ACC-APK4-20261020-01: physical panel-open failure no longer observed; Back/data checks remain deferred. Exact original crash diagnosis still unproven.
- CARRY ACC-APK4-20261020-09: previous Actions release publication403; cause unproven. Preserve raw expensive APK before auxiliary checks. Do not rebuild it solely to retry verifiers.
- CARRY: live reply, refresh/revocation, upgrade/data retention, lifecycle/Keystore/SAF/Chromebook checks remain unverified; Claude simple subscription onboarding unfinished; task board manual only. Historical runner flakes and production/PR35 gates unchanged.
