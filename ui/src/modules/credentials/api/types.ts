// Credentials domain types — the module's schema layer (≈ backend web+service
// schemas). These re-export and narrow the generated jentic-one contract, which
// is sourced from the live `/openapi.json` (regenerate with `npm run codegen`).
//
// Wire shape (verified against the running backend):
//   - Create is a discriminated union by `type`
//     (bearer_token | api_key | basic | oauth2).
//   - Create returns the redacted credential PLUS a one-time `secret` object.
//   - Read/list/patch return `CredentialRedactedResponse` (secrets never returned);
//     type-specific projection (location/field_name/hint) lives inside `details`.
//   - OAuth connect: POST /credentials/{id}/connect -> { authorize_url, state }.
import {
	CredentialLocation,
	CredentialType,
	type APIReference,
	type APIReferenceRequest,
	type ApiKeyCreateRequest,
	type ApiKeyUpdateRequest,
	type BasicAuthCreateRequest,
	type BasicAuthUpdateRequest,
	type BearerTokenCreateRequest,
	type BearerTokenUpdateRequest,
	type ConnectChallengeResponse,
	type ConnectRequestBody,
	type CredentialCreateResponse,
	type CredentialListResponse,
	type CredentialRedactedResponse,
	type OAuth2CreateRequest,
	type OAuth2UpdateRequest,
	type RuntimeConfig,
} from '@/shared/api';

export { CredentialType };

export type {
	APIReference,
	APIReferenceRequest,
	ApiKeyUpdateRequest,
	BasicAuthCreateRequest,
	BasicAuthUpdateRequest,
	BearerTokenCreateRequest,
	BearerTokenUpdateRequest,
	ConnectChallengeResponse,
	ConnectRequestBody,
	CredentialCreateResponse,
	CredentialListResponse,
	CredentialRedactedResponse,
	OAuth2CreateRequest,
	OAuth2UpdateRequest,
	RuntimeConfig,
};

/** A single credential as returned by list/get/patch (secrets redacted). */
export type Credential = CredentialRedactedResponse;

/** The discriminated create body. `type` is the discriminator. */
export type CredentialCreateRequest =
	BearerTokenCreateRequest | ApiKeyCreateRequest | BasicAuthCreateRequest | OAuth2CreateRequest;

/** The discriminated update/rotate body. `type` is the discriminator. */
export type CredentialUpdateRequest =
	BearerTokenUpdateRequest | ApiKeyUpdateRequest | BasicAuthUpdateRequest | OAuth2UpdateRequest;

/** Where an api_key credential is injected. Mirrors `CredentialLocation`. */
export type CredentialKeyLocation = 'header' | 'query';

export const KEY_LOCATIONS: readonly CredentialKeyLocation[] = ['header', 'query'];

/**
 * Map a UI location string to the generated create-request enum. Lives in the
 * api layer so view code (forms) never imports the generated models directly.
 */
export function toKeyLocationEnum(location: CredentialKeyLocation): CredentialLocation {
	return location === 'query' ? CredentialLocation.QUERY : CredentialLocation.HEADER;
}

/** Stable order for the credential-type picker in the create wizard. */
export const CREDENTIAL_TYPE_ORDER: readonly CredentialType[] = [
	CredentialType.BEARER_TOKEN,
	CredentialType.API_KEY,
	CredentialType.BASIC,
	CredentialType.OAUTH2,
];

/** Human label for each credential type. */
export const CREDENTIAL_TYPE_LABELS: Record<CredentialType, string> = {
	[CredentialType.BEARER_TOKEN]: 'Bearer token',
	[CredentialType.API_KEY]: 'API key',
	[CredentialType.BASIC]: 'Basic auth',
	[CredentialType.OAUTH2]: 'OAuth 2.0',
};

/** One-line description for each credential type, shown on the type cards. */
export const CREDENTIAL_TYPE_DESCRIPTIONS: Record<CredentialType, string> = {
	[CredentialType.BEARER_TOKEN]: 'A single token sent as a Bearer Authorization header.',
	[CredentialType.API_KEY]: 'A key passed in a request header or query parameter.',
	[CredentialType.BASIC]: 'A username and password pair.',
	[CredentialType.OAUTH2]: 'Client credentials exchanged for access tokens.',
};

/**
 * Redacted, type-specific projection carried on `CredentialRedactedResponse.details`.
 * The wire type is an open object (`Record<string, any> | null`); this is the
 * defensive view our UI reads — every field is optional and accessed safely so a
 * future backend reshape can't crash rendering.
 */
export interface CredentialDetails {
	/** api_key: where the key is injected. */
	location?: CredentialKeyLocation | string;
	/** api_key: the header/query param name carrying the key. */
	field_name?: string;
	/** A redacted hint (e.g. last-N chars) — never the secret. */
	hint?: string;
	[key: string]: unknown;
}

/** Read `details` off a credential without trusting its exact shape. */
export function credentialDetails(cred: Credential): CredentialDetails {
	const d = cred.details;
	return d && typeof d === 'object' ? (d as CredentialDetails) : {};
}

/** Format an API reference tuple as `vendor/name@version` for display. */
export function formatApiReference(api: APIReference): string {
	const name = api.name ? `/${api.name}` : '';
	const version = api.version ? `@${api.version}` : '';
	return `${api.vendor}${name}${version}`;
}
