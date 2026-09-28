# Credentials module — providers & connect flow

This module manages stored credentials and the OAuth **connect** flow. It also
ports the "add credential" style from `jentic-mini` while building strictly
against `jentic-one`'s real backend contract.

## How OAuth providers work here (vs jentic-mini)

`jentic-one` and `jentic-mini` reach the same outcome (a connected, managed
OAuth credential) through **very different surfaces**:

|                                     | jentic-mini                                                      | jentic-one (this repo)                                                                                                                                                                                        |
| ----------------------------------- | ---------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Distinguishing a managed credential | A separate `pipedream_oauth` auth type                           | The normal `oauth2` type with `provider: "pipedream"`                                                                                                                                                         |
| Choosing the provider               | Dedicated Pipedream form/card                                    | A `provider` string on `POST /credentials` (default `"static"`)                                                                                                                                               |
| Configuring Pipedream itself        | Runtime, in the UI (`/oauth-brokers` CRUD + `sync` + `accounts`) | **Server-side only** — an entry in `cfg.credentials.providers` (env/Helm)                                                                                                                                     |
| Connect                             | Pipedream-hosted link via broker                                 | `POST /credentials/{id}/connect` → `{authorize_url, state}`; provider is chosen server-side from the stored `provider`                                                                                        |
| Callback                            | Redirect back into the app                                       | `GET /credentials/oauth/callback` 303-redirects the popup to `/oauth/connected?status=ok\|error` (a self-closing SPA page); the connection is persisted server-side and the SPA learns the outcome by polling |

The platform's `PipedreamProvider` and `DirectOAuth2Provider` share **one**
`oauth2` wire type and **one** connect endpoint. The UI therefore does not need
Pipedream-specific form fields — it only needs to (1) let the user pick the
`provider`, and (2) run the connect round-trip.

### Connect round-trip (this UI)

Because the callback returns JSON instead of redirecting, `runConnectFlow`
(`api/index.ts`):

1. calls `POST /credentials/{id}/connect`,
2. opens the returned `authorize_url` (a vendor IdP page, or a Pipedream hosted
   link) in a popup,
3. polls `GET /credentials/{id}` until a connection appears
   (`provider_account_ref` for managed, or an `updated_at` bump for direct) or
   the popup is closed,
4. falls back to a same-tab redirect when popups are blocked.

## Known limitations / backend gaps (vs jentic-mini)

These are the reasons full mini-parity is **not** achievable from the UI alone:

1. **Provider discovery, not enum.** `provider` is an open free-string in the
   OpenAPI with no enum, but `GET /credentials/providers` returns discovery
   metadata (including a `configured` flag) for each provider. The managed
   Pipedream option is shown only when discovery reports a `pipedream` provider
   with `configured === true` — runtime config (`admin config providers set
pipedream …`) decides, with no build-time flag (see `config.ts`).
2. **No self-serve Pipedream setup.** mini exposes `/oauth-brokers` CRUD +
   `/sync` + `/accounts` so operators paste Pipedream `client_id/secret/
project_id` in the UI. `jentic-one` has **none of these endpoints** —
   Pipedream is configured server-side. Rebuilding mini's "Enable OAuth" card
   would require new CORE backend endpoints.
3. **If the flag is on but the backend lacks a `pipedream` provider**, the
   connect call surfaces the backend's error rather than silently succeeding.
   Empirically, on a backend without Pipedream configured the failure actually
   lands as a **500 at create time** (the create path resolves the provider and
   an unconfigured lookup throws). The create form maps this to a friendly
   "Pipedream isn't enabled on this server" message — see
   `managedProviderUnavailableMessage` in `config.ts`.
4. **oauth2 create still requires vendor `client_id/secret/token_url`** even for
   managed connections (the create schema does not branch on `provider`). For
   Pipedream those are effectively placeholders; the form shows a note.

See the filed backend issue for (1) and (2): jentic/jentic-one#388.

## Guided add-credential flow (API picker + security schemes)

The create sheet is a **two-step wizard** that mirrors `jentic-mini`'s
guided UX — the user picks an API first, then a form auto-shaped from that
API's OpenAPI `components.securitySchemes` collects the secret.

### Architecture

| Layer               | File                                                       | Role                                                                                                                                                                                                                                                                                                                                             |
| ------------------- | ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Pure scheme helpers | `lib/schemes.ts`                                           | Parse `securitySchemes` into typed `SchemeOption`s; map `SchemeType` to jentic-one's `CredentialType` enum; derive apiKey `field_name`/`location`; extract raw OAuth2 scopes (`oauth2ScopesFromSchemes`)                                                                                                                                         |
| Scope utilities     | `lib/scope-utils.ts`                                       | Ported from jentic-webapp: `EnhancedScope` enrichment, resource grouping (`groupScopesByResource`), recommended-scope detection (read-only/safe vs write/admin), search filtering, and `getRecommendedScopes` for auto-selection                                                                                                                 |
| Data wrappers       | `api/apis.ts`                                              | Thin functions over `ApisService` + `CatalogService` (`listApis`, `getApiSpec`, `listCatalog`, `importCatalogEntry`) plus a `fetchPublicSpec` helper                                                                                                                                                                                             |
| React Query hooks   | `api/apis-hooks.ts`                                        | `useApis`, `useCatalog`, `useApiSchemes`, `useImportCatalogEntry`; owns `SelectedApi`, `ServerVarDef`, and the namespaced `apiPickerKeys` cache slice                                                                                                                                                                                            |
| Picker UI           | `components/ApiPicker.tsx`                                 | Debounced search; "In your workspace" (local) + "From the Jentic public catalog" sections; emits `SelectedApi`; "Enter manually" escape hatch                                                                                                                                                                                                    |
| Auth type cards     | `components/AuthTypeCards.tsx`                             | Radio cards (icon + title + description, webapp-style) for the credential type — single "Detected" card in spec mode, all four in manual mode                                                                                                                                                                                                    |
| Server variables    | `components/ServerVariablesSection.tsx`                    | One input/dropdown per OpenAPI **server variable** (e.g. Atlassian `{your-domain}`); enum vars → `<Select>`, free-form → text; required vars gate submit                                                                                                                                                                                         |
| Scope picker        | `components/ScopePicker.tsx` + `components/ScopeGroup.tsx` | Ported `ScopeSelector`/`ScopeGroup`: scopes grouped by resource into collapsible groups with tri-state select-all, per-group + global select/deselect, search, a `selected/total` count, and "Recommended" badges. Recommended scopes are auto-selected once when the spec loads (dialog tracks manual interaction so it never overrides intent) |
| Wizard              | `components/CreateCredentialFlow.tsx`                      | Drawer (`surface="dialog"` for a host in the browser's top layer) with step 1 = picker, step 2 = form; auto-types from the picked scheme; pre-fills apiKey `field_name`/`location`, seeds server-variable defaults, and auto-selects recommended OAuth2 scopes from the spec; skeletons the auth section until the spec resolves                 |

### Submit path

1. **Catalog + un-registered** → fire `POST /catalog/{api_id}:import` first.
   The import is async, but the backend resolves the `{vendor,name,version}`
   triple at create time so we don't have to wait for the import worker.
2. **Always** → `POST /credentials` with the discriminated body built by
   `buildCreateBody`, using the picker's `apiRef` triple.

### Picker-vs-mini caveats

- **`/apis` row `security_schemes` is a `string[]`** (just type names), so it
  isn't enough for field-level shaping. We **always fetch the full OpenAPI**
  (`GET /apis/{v}/{n}/{ver}/openapi` for local, `spec_url` for catalog) and
  read `components.securitySchemes` off that. Cached for 5 minutes.
- **Catalog spec fetch is client-side** from `raw.githubusercontent.com`.
  Subject to CORS / host availability. Mitigated by a 10s abort + react-query
  cache + the manual-entry fallback.
- **`provider` discovery is still hard-coded.** The picker doesn't change the
  Pipedream-vs-static `provider` story — see the gaps section above.
- **Two-endpoint merge.** mini had a single unified `/apis?q=` that blended
  both surfaces; jentic-one's `/apis` and `/catalog` are separate, so the
  picker client-merges them (workspace first, catalog filtered by `q`).
- **Manual entry stays.** APIs missing/owning malformed specs always have an
  "Enter manually" escape so we never regress on the old free-text flow.

### Server variables

The form collects OpenAPI server-variable values (e.g. Atlassian's
`{your-domain}` for `https://{your-domain}.atlassian.net`) via
`ServerVariablesSection` and transmits them as `server_variables` on both
create and update. The backend persists these as a JSONB column and the broker
substitutes them into the upstream URL template at proxy time.
