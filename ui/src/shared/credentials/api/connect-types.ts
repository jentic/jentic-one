/**
 * View types for the connect-session and vendor-registry endpoints, derived
 * from the generated models in `@/shared/api`.
 *
 * The generator emits TypeScript enums for closed string sets and plain
 * `string` for a single-value `kind` tag. Views build these objects from
 * string literals and branch on `kind`, so each type here is its generated
 * model with enum members widened to their string values ({@link Literals})
 * and, on a discriminated variant, `kind` pinned to its one value
 * ({@link Variant}). No field is declared here that the generated model
 * doesn't already carry, apart from the rule editor's local `comment`.
 */

import type {
	ApiKeyConfirmSessionRequest,
	AuthCodeConfirmSessionResponse,
	BasicConfirmSessionRequest,
	BearerConfirmSessionRequest,
	ConnectSessionStatusResponse,
	ConnectSessionSummaryResponse,
	ConnectedConfirmSessionResponse,
	DeviceAuthorizationConfirmSessionResponse,
	ExistingCredentialConfirmSessionRequest,
	ExistingCredentialResponse,
	IntegrationsConnectRequest,
	IntegrationsConnectResponse,
	OAuthConfirmSessionRequest,
	OperationSummaryResponse,
	OwnOAuthClientConfirmSessionRequest,
	PermissionRuleSchema,
	ReauthorizeConfirmSessionRequest,
	ReauthorizeConfirmSessionResponse,
	ReviewAgentResponse,
	ReviewProvenanceResponse,
	ReviewSchemeResponse,
	ReviewScopeResponse,
	ReviewSessionResponse,
	VendorAuthCapabilitiesResponse,
	VendorFlowResponse,
	VendorListResponse as GeneratedVendorListResponse,
	VendorScopeResponse,
	VendorSummaryResponse,
} from '@/shared/api';

/** A generated model with every enum member widened to its string value. */
export type Literals<T> = T extends string
	? `${T}`
	: T extends ReadonlyArray<infer U>
		? Literals<U>[]
		: T extends object
			? { [K in keyof T]: Literals<T[K]> }
			: T;

/** A discriminated-union variant: the generated model with `kind` pinned. */
type Variant<T, K extends string, Optional extends boolean = false> = Omit<Literals<T>, 'kind'> &
	(Optional extends true ? { kind?: K } : { kind: K });

/**
 * One agent↔credential permission rule, evaluated first-match-wins on
 * `(method, path, operation_id)`. `comment` is the rule editor's own note
 * and never part of the wire shape.
 */
export type PermissionRule = Literals<PermissionRuleSchema> & { comment?: string | null };

export type ScopeClassification = Literals<ReviewScopeResponse>['classification'];
export type ReviewScope = Literals<ReviewScopeResponse>;
export type ReviewProvenance = Literals<ReviewProvenanceResponse>;
export type ReviewAgent = Literals<ReviewAgentResponse>;
export type ReviewScheme = Literals<ReviewSchemeResponse>;
export type ExistingCredential = Literals<ExistingCredentialResponse>;
export type ReviewSession = Literals<ReviewSessionResponse>;

/** Session states a review can show; the terminal ones never reach the review read. */
export type ReviewSessionState = Literals<ConnectSessionSummaryResponse>['state'];

/** The OAuth confirm — the default variant (a body without `kind`). */
export type ConfirmRequest = Variant<OAuthConfirmSessionRequest, 'oauth', true>;
export type ApiKeyConfirmRequest = Variant<ApiKeyConfirmSessionRequest, 'api_key'>;
export type BearerConfirmRequest = Variant<BearerConfirmSessionRequest, 'bearer'>;
export type BasicConfirmRequest = Variant<BasicConfirmSessionRequest, 'basic'>;
/** Resolve an `awaiting_app` session with the approver's own OAuth client. */
export type OwnOAuthClientConfirmRequest = Variant<
	OwnOAuthClientConfirmSessionRequest,
	'own_oauth_client'
>;
/** Bind a credential the approver already holds (optionally re-consenting it). */
export type ExistingCredentialConfirmRequest =
	| Variant<ExistingCredentialConfirmSessionRequest, 'existing_credential'>
	| Variant<ReauthorizeConfirmSessionRequest, 'reauthorize'>;

/** Every `:confirm` body; `kind` picks the variant server-side. */
export type ConfirmSessionBody =
	| ConfirmRequest
	| ApiKeyConfirmRequest
	| BearerConfirmRequest
	| BasicConfirmRequest
	| OwnOAuthClientConfirmRequest
	| ExistingCredentialConfirmRequest;

export type DeviceAuthorizationConfirmResponse = Variant<
	DeviceAuthorizationConfirmSessionResponse,
	'device_authorization'
>;
export type AuthCodeConfirmResponse = Variant<AuthCodeConfirmSessionResponse, 'authorization_code'>;
export type ConnectedConfirmResponse = Variant<ConnectedConfirmSessionResponse, 'connected'>;
export type ReauthorizeConfirmResponse = Variant<ReauthorizeConfirmSessionResponse, 'reauthorize'>;

/** The two vendor challenges a `:confirm` can answer with. */
export type ConfirmResponse = DeviceAuthorizationConfirmResponse | AuthCodeConfirmResponse;

/** Every `:confirm` result, discriminated on `kind`. */
export type ConfirmSessionResponse =
	ConfirmResponse | ConnectedConfirmResponse | ReauthorizeConfirmResponse;

export type StatusResponse = Literals<ConnectSessionStatusResponse>;
export type SessionStatus = StatusResponse['status'];

export type ConnectRequest = Literals<IntegrationsConnectRequest>;
export type ConnectResponse = IntegrationsConnectResponse;

export type VendorSummary = Literals<VendorSummaryResponse>;
export type VendorListResponse = Literals<GeneratedVendorListResponse>;
export type VendorFlow = VendorFlowResponse;
export type VendorScopeCatalog = Literals<VendorScopeResponse>;
export type VendorAuthCapabilities = Literals<VendorAuthCapabilitiesResponse>;

/** The fields of an operation the rules-page preview matches and labels. */
export type VendorOperation = Pick<
	OperationSummaryResponse,
	'operation_id' | 'method' | 'path' | 'name' | 'description'
>;

export interface VendorOperationsPage {
	data: VendorOperation[];
	has_more: boolean;
	next_cursor: string | null;
}
