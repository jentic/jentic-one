# Credential Deeplink — Pre-filling the Add Credential Form

The `/credentials/new` page supports query parameters to pre-select an API and
pre-fill form fields. This lets an agent configure everything it knows
(API ID, label, username, server URL) and send the user a direct link to
complete only the secret — keeping sensitive values out of the conversation.

> **Two surfaces, one contract.** Since the credentials revamp v3 the UI
> also offers an in-page **Add Credential** dialog (and an inline edit
> sheet), reachable from the workspace, API detail, toolkit detail, and
> Discover surfaces — no page navigation. The deeplink format documented
> below is unchanged: it is the canonical contract for **agent-driven
> handoffs** and continues to resolve to the standalone form route. The
> dialog is the **human-driven** entry point for the same flow and never
> requires a deeplink. Use deeplinks when an agent needs to hand a user
> a one-shot URL; use the in-page dialog otherwise.

## URL format

```
/credentials/new?api_id=<api_id>[&label=...][&identity=...][&server_vars[<key>]=<value>...]
```

### Parameters

| Parameter | Description |
|-----------|-------------|
| `api_id` | **Required for deeplink.** The registered API ID (e.g. `discourse.org`, `api.openai.com`). Skips the API picker and goes straight to the fill form. |
| `label` | Pre-fills the credential label shown in the UI. |
| `identity` | Pre-fills the identity/username field (used for compound auth like Discourse `Api-Username`, or HTTP Basic username). |
| `server_vars[<name>]` | Pre-fills a server variable. Repeat for multiple variables. Example: `server_vars[host]=techpreneurs.ie` |
| `value` | Pre-fills the secret/token field. **Avoid passing real secrets** — send the URL without `value` and let the user paste the secret in the browser. |

## Example — OpenAI (simple API key)

```
https://jentic-mini.home.seanblanchfield.com/credentials/new?api_id=api.openai.com&label=OpenAI+API+Key
```

The user opens the link, pastes their OpenAI key, and clicks Save.

## Example — Discourse (compound auth + server variable)

The agent constructs this URL and sends it to the user:

```
https://jentic-mini.home.seanblanchfield.com/credentials/new
  ?api_id=discourse.org
  &label=Techpreneurs+Discourse
  &identity=seanblanchfield
  &server_vars[host]=techpreneurs.ie
```

The user opens it, sees the form pre-filled with everything except the API key,
pastes their Discourse API key, and clicks Save. The secret never passes through
the agent.

## Agent workflow pattern

This is the recommended pattern for adding sensitive credentials:

1. **Agent:** Import the API (if not already present) and submit any required overlay.
2. **Agent:** Build the deeplink URL with all non-secret fields prefilled.
3. **Agent:** Send the URL to the user with a one-line instruction:
   > "Open this link, paste your API key, and click Save."
4. **User:** Opens the URL, pastes the secret, saves.
5. **Agent:** Confirms the credential is saved (GET /credentials) and proceeds.

This pattern:
- Keeps secrets out of the conversation and agent context entirely
- Reduces friction for the user (no manual form navigation)
- Allows the agent to control all non-sensitive configuration

## Security note

The `value` parameter is intentionally supported (some non-sensitive credentials
like public API tokens are fine to pass this way), but for real secrets always
omit it. The server variables and identity fields are not sensitive and safe to
include in the URL.
