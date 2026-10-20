# Direct ChatGPT connection · Build 3

The default connection is now an Android-native ChatGPT account flow. Termux, Linux, a Python bridge, CLI binaries, an API key and a separate server are not prerequisites for this path. Runtime pairing remains an optional advanced connection. Claude currently still requires its own runtime; straightforward Claude subscription onboarding is unfinished.

## Acceptance flow

Install Build 3 over the existing ACC installation. Open Connections → Continue with ChatGPT. ACC opens the system browser at the official OpenAI authorization endpoint. Complete sign-in and consent there, then return to ACC. Select a model from the account's catalog and send a message. Never paste credentials or callback URLs into a support chat.

If sign-in fails after registration, select the unfinished registration and continue with it. ACC retains the issued client ID instead of repeatedly registering the same attempt. A cancelled, expired or process-killed browser attempt must be started again. Account allowance, eligibility, app permissions and model availability come from OpenAI. The user may manage plan/credit usage through ChatGPT Settings → Usage. There is no ACC API-key billing fallback or automatic provider switch.

This connection creates local ACC conversations; it does not import ChatGPT conversations, memory, plugins or the tools in ChatGPT's app. Chat is text-only, without tool execution. Sentry files are not attached automatically. Switching accounts/projects separates histories. New conversation clears the displayed thread; save content you need in Sentry first.

## Implementation boundaries

- `modules/acc-chatgpt` is a local Expo Android module, autolinked from `mobile/modules`; it is source, not a hand-edited generated `android/` directory.
- A short-lived, ephemeral-port IPv4 loopback listener exists only during sign-in. It binds 127.0.0.1, checks the callback path/Host/state, rejects duplicate query keys and foreign client IDs, caps request headers, and times out after five minutes. It serves static plain text without echoing callback parameters. It does not offer runtime command or filesystem APIs.
- The system browser owns sign-in. PKCE S256, random state and OIDC nonce are fresh per attempt. A stable opaque per-install host ID and issued per-account/workspace client ID are reused.
- ID tokens require an RS256 signature against the official JWKS, issuer, audience/authorized party, expiry, nonce and identity continuity. Identity alone does not grant inference: plan-use and resource scopes are checked.
- Access, refresh and ID tokens remain in native memory and one Android Keystore AES-GCM encrypted private credential record. They are not returned to JavaScript, put in AsyncStorage, sent to Drive/GitHub, or logged. Android backup and OTA remain disabled. Chat transcripts remain app-private AsyncStorage, not separately encrypted by ACC.
- Refreshes/account operations/chat are serialized on a native worker. Rotating credentials are written together with synchronous private preference commits. Sign-out attempts official revocation, removes local tokens and reports unconfirmed remote revocation. Registration/account identity remains for reuse. Up to ten account registrations are retained; removal UI is not yet included.
- Network requests target fixed official HTTPS endpoints, with default certificate validation, redirects and connection-failure retries disabled. No provider token is placed in a URL. No browser storage or WebView-based provider session is used.
- Models come from the selected account's catalog. Inference uses the public Responses endpoint, `store:false`, `stream:true`, and the displayed conversation as input. No CLI, backend-api endpoint, account-cookie scraping, hosted tools or remote orchestration is involved.
- A response is saved as completed only after `response.completed` with a completed assistant message. Failed/incomplete/interrupted streams are not promoted to successful replies. Requests and streams are bounded. Stop cancels the native HTTP call; a provider may already have consumed allowance.
- Intent is saved before sending. On restart, an unfinished direct request is marked interrupted and is never replayed automatically. There is no stored-response retrieval in this flow. Starting a new conversation after interruption is an explicit user action, not proof that the old request never ran.

## Evidence and release limits

Build/fixture/unit results do not establish successful live account access, actual Android callback behavior, Keystore recovery, background/process-death behavior or provider eligibility. Real tablet sign-in and a completed request are acceptance checks, not prerequisites the user must assemble manually.

This is personal local testing, not permission to sell subscription access. The official direct flow is documented for open-source/locally hosted apps; commercial or hosted access has separate vendor requirements. ACC's source-license/distribution decision is not silently changed here. No paid/store release, commercial approval or production deployment is implied.

Official sources checked 2026-10-20 UTC:
- https://developers.openai.com/siwc/token-sharing-open-source
- https://developers.openai.com/siwc/token-sharing-open-source/sign-in
- https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions
- https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference
- https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations
- https://code.claude.com/docs/en/legal-and-compliance
- https://docs.expo.dev/versions/v58.0.0/
- https://docs.expo.dev/modules/get-started/
