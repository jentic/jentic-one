// Pure builders that turn the flat form state into the discriminated wire
// bodies. Kept separate from the sheet components so the type-narrowing logic
// is unit-testable without rendering.
import {
	CredentialType,
	toKeyLocationEnum,
	type APIReferenceRequest,
	type CredentialCreateRequest,
	type CredentialUpdateRequest,
	type RuntimeConfig,
	type SelectedApi,
	type ServerVarDef,
} from '@/modules/credentials/api';
import {
	apiKeyFieldsFromScheme,
	oauth2FlowsFromSchemes,
	type OAuth2FlowDef,
	type RawSchemes,
} from '@/modules/credentials/lib/schemes';
import type { CredentialFormState } from '@/modules/credentials/components/CredentialTypeFields';

function apiRef(state: CredentialFormState): APIReferenceRequest {
	return {
		vendor: state.apiVendor.trim(),
		name: state.apiName.trim() || undefined,
		version: state.apiVersion.trim() || undefined,
	};
}

function parseScopes(raw: string): string[] | undefined {
	const scopes = raw
		.split(/\s+/)
		.map((s) => s.trim())
		.filter(Boolean);
	return scopes.length ? scopes : undefined;
}

/**
 * Whether `raw` parses as an absolute http(s) URL with a hostname. The OAuth2
 * Token/Authorize fields render as `<input type="url">`, but that native
 * constraint isn't enforced because the form submits via React's `onSubmit`
 * (not native form validation) — so a value like `token` or `foo` sails
 * through to the backend, which then rejects it with a 400 (see
 * `shared/url_validation.py`). We validate the shape client-side to turn that
 * opaque 400 into an inline field error. We deliberately do NOT replicate the
 * backend's SSRF policy (private-range / metadata-host blocking) here — that
 * stays authoritative on the server to avoid two copies drifting apart.
 */
export function isValidHttpUrl(raw: string): boolean {
	const trimmed = raw.trim();
	if (!trimmed) return false;
	let url: URL;
	try {
		url = new URL(trimmed);
	} catch {
		return false;
	}
	return (url.protocol === 'http:' || url.protocol === 'https:') && !!url.hostname;
}

/**
 * Build the optional `runtime_config` from collected server-variable values.
 *
 * Server variables now have a dedicated `server_variables` field on the
 * credential contract and are no longer transmitted here. This function is
 * retained for any future header/query-param overrides that may use
 * `RuntimeConfig`.
 */
export function buildRuntimeConfig(_serverVars: Record<string, string>): RuntimeConfig | undefined {
	return undefined;
}

/**
 * Build the `server_variables` dict from form state. Returns `undefined` when
 * empty so it is omitted from the wire body (nullable field semantics).
 */
export function buildServerVariables(
	serverVars: Record<string, string>,
): Record<string, string> | undefined {
	const entries = Object.entries(serverVars).filter(([, v]) => v.trim());
	return entries.length ? Object.fromEntries(entries) : undefined;
}

/** Assemble the create body for the chosen credential type. */
export function buildCreateBody(
	type: CredentialType,
	state: CredentialFormState,
): CredentialCreateRequest {
	const runtime_config = buildRuntimeConfig(state.serverVars);
	const server_variables = buildServerVariables(state.serverVars);
	const base = {
		api: apiRef(state),
		name: state.name.trim(),
		provider: state.provider.trim() || 'static',
		...(runtime_config ? { runtime_config } : {}),
		...(server_variables ? { server_variables } : {}),
	};

	switch (type) {
		case CredentialType.BEARER_TOKEN:
			return { ...base, type, token: state.token };
		case CredentialType.API_KEY:
			return {
				...base,
				type,
				key: state.key,
				field_name: state.fieldName.trim(),
				location: toKeyLocationEnum(state.location),
			};
		case CredentialType.BASIC:
			return { ...base, type, username: state.username.trim(), password: state.password };
		case CredentialType.OAUTH2:
			return {
				...base,
				type,
				client_id: state.clientId.trim(),
				client_secret: state.clientSecret,
				token_url: state.tokenUrl.trim(),
				authorize_url: state.authorizeUrl.trim() || undefined,
				grant_type: state.grantType.trim() || undefined,
				scopes: parseScopes(state.scopes) ?? undefined,
			};
	}
}

/**
 * Assemble the update body. Only sends fields the user actually changed; blank
 * secret fields are omitted so the existing secret is preserved (rotation is
 * opt-in by typing a new value).
 */
export function buildUpdateBody(
	type: CredentialType,
	state: CredentialFormState,
	originalName: string,
): CredentialUpdateRequest {
	const name = state.name.trim();
	const namePatch = name && name !== originalName ? { name } : {};
	const secret = (v: string): string | undefined => (v.trim() ? v : undefined);
	const server_variables = buildServerVariables(state.serverVars);
	const svPatch = server_variables ? { server_variables } : {};

	switch (type) {
		case CredentialType.BEARER_TOKEN:
			return { type, ...namePatch, ...svPatch, token: secret(state.token) };
		case CredentialType.API_KEY:
			return {
				type,
				...namePatch,
				...svPatch,
				key: secret(state.key),
				field_name: state.fieldName.trim() || undefined,
				location: toKeyLocationEnum(state.location),
			};
		case CredentialType.BASIC:
			return {
				type,
				...namePatch,
				...svPatch,
				username: state.username.trim() || undefined,
				password: secret(state.password),
			};
		case CredentialType.OAUTH2:
			return {
				type,
				...namePatch,
				...svPatch,
				client_secret: secret(state.clientSecret),
				token_url: state.tokenUrl.trim() || undefined,
				scopes: parseScopes(state.scopes),
			};
	}
}

/** Returns a map of field errors for required create fields, empty when valid. */
export function validateCreate(
	type: CredentialType,
	state: CredentialFormState,
): Partial<Record<keyof CredentialFormState, string>> {
	const errors: Partial<Record<keyof CredentialFormState, string>> = {};
	if (!state.name.trim()) errors.name = 'Name is required.';
	if (!state.apiVendor.trim()) errors.apiVendor = 'API vendor is required.';

	switch (type) {
		case CredentialType.BEARER_TOKEN:
			if (!state.token) errors.token = 'Token is required.';
			break;
		case CredentialType.API_KEY:
			if (!state.key) errors.key = 'API key is required.';
			if (!state.fieldName.trim()) errors.fieldName = 'Field name is required.';
			break;
		case CredentialType.BASIC:
			if (!state.username.trim()) errors.username = 'Username is required.';
			if (!state.password) errors.password = 'Password is required.';
			break;
		case CredentialType.OAUTH2:
			if (!state.clientId.trim()) errors.clientId = 'Client ID is required.';
			if (!state.clientSecret) errors.clientSecret = 'Client secret is required.';
			{
				const grant = state.grantType.trim();
				// The implicit grant has no token endpoint, so a Token URL is N/A.
				// Every other grant exchanges a token and requires it.
				if (grant !== 'implicit') {
					if (!state.tokenUrl.trim()) {
						errors.tokenUrl = 'Token URL is required.';
					} else if (!isValidHttpUrl(state.tokenUrl)) {
						errors.tokenUrl = 'Token URL must be a valid http(s) URL.';
					}
				}
				// The authorization_code grant is a browser redirect flow, so an
				// Authorize URL is mandatory — without it the credential is
				// created but can never connect (the backend's begin_connect
				// raises NotConnectableError). Validate the format for any grant
				// when a value is present.
				if (grant === 'authorization_code' && !state.authorizeUrl.trim()) {
					errors.authorizeUrl =
						'Authorize URL is required for the authorization code grant.';
				} else if (state.authorizeUrl.trim() && !isValidHttpUrl(state.authorizeUrl)) {
					errors.authorizeUrl = 'Authorize URL must be a valid http(s) URL.';
				}
			}
			break;
	}
	return errors;
}

/**
 * Seed the form state from a picked API. Replaces the manually-typed
 * vendor/name/version triple with the picker's values and uses the API's
 * display name as a sensible default credential name.
 *
 * `nameDirty` guards the credential name: when the user hasn't manually edited
 * it we always refresh it to the newly-picked API's label (so switching APIs
 * updates the name), but once they've typed their own we never clobber it.
 */
export function seedFormFromSelectedApi(
	state: CredentialFormState,
	api: SelectedApi,
	nameDirty = false,
): CredentialFormState {
	return {
		...state,
		apiVendor: api.vendor,
		apiName: api.name,
		apiVersion: api.version,
		name: nameDirty ? state.name : api.label,
	};
}

/**
 * Seed the apiKey-specific fields (`fieldName`, `location`) from the picked
 * scheme. Only mutates apiKey fields — other types are unaffected. Used when
 * the spec exposes a recognisable apiKey scheme so the user doesn't have to
 * re-type the header/query name the spec already declares.
 *
 * `fieldName` is preserved if the user already typed one; `location` is always
 * taken from the scheme. We can't use a truthiness guard for `location` the way
 * we do for `fieldName` — it's a `'header' | 'query'` enum that is never empty
 * (`EMPTY_FORM` defaults it to `'header'`), so `state.location || location`
 * would always keep the default and silently drop a spec-declared `query`
 * location. Seeding only happens right after picking the API / switching to the
 * apiKey type (before the user has touched the field), so taking the spec value
 * is correct; the user can still override it afterwards.
 */
export function seedApiKeyFromScheme(
	state: CredentialFormState,
	schemes: RawSchemes,
	schemeName: string | null | undefined,
): CredentialFormState {
	const { fieldName, location } = apiKeyFieldsFromScheme(schemes, schemeName);
	return {
		...state,
		fieldName: state.fieldName.trim() ? state.fieldName : fieldName,
		location,
	};
}

/**
 * Seed OAuth2 URL fields (tokenUrl, authorizeUrl, grantType) from the spec's
 * declared flows. Only sets values the user hasn't already filled. Returns the
 * parsed flows for the caller to pass to the grant-type selector.
 *
 * Pass `flowId` to seed from a specific `(scheme, flow)` pair (matching
 * `OAuth2FlowDef.id`). When omitted, the first flow is used. This matters when
 * the spec declares multiple OAuth2 schemes with different token URLs — the
 * caller drives which one the user picked.
 */
export function seedOAuth2FromScheme(
	state: CredentialFormState,
	schemes: RawSchemes,
	schemeName?: string | null,
	flowId?: string | null,
): { state: CredentialFormState; flows: OAuth2FlowDef[]; activeFlowId: string | null } {
	const flows = oauth2FlowsFromSchemes(schemes, schemeName);
	if (flows.length === 0) return { state, flows, activeFlowId: null };
	const active = (flowId && flows.find((f) => f.id === flowId)) || flows[0];
	return {
		state: {
			...state,
			tokenUrl: state.tokenUrl.trim() ? state.tokenUrl : (active.tokenUrl ?? ''),
			authorizeUrl: state.authorizeUrl.trim()
				? state.authorizeUrl
				: (active.authorizationUrl ?? ''),
			grantType: state.grantType.trim() ? state.grantType : active.grantType,
		},
		flows,
		activeFlowId: active.id,
	};
}

/**
 * Seed server-variable values from their spec defaults, without clobbering
 * anything the user already typed. Keeps the value map sparse — only variables
 * with a default get a starting value.
 */
export function seedServerVars(
	current: Record<string, string>,
	vars: ServerVarDef[],
): Record<string, string> {
	const next = { ...current };
	for (const v of vars) {
		if (next[v.name] == null && v.default != null) next[v.name] = v.default;
	}
	return next;
}

/**
 * Validate that every required server variable has a value. Returns a map of
 * `{ varName: message }`, empty when all required vars are filled. Kept
 * separate from {@link validateCreate} because server-var keys aren't members
 * of `CredentialFormState`.
 */
export function validateServerVars(
	vars: ServerVarDef[],
	values: Record<string, string>,
): Record<string, string> {
	const errors: Record<string, string> = {};
	for (const v of vars) {
		if (v.required && !(values[v.name] ?? '').trim()) {
			errors[v.name] = 'Required.';
		}
	}
	return errors;
}
