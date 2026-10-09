/**
 * Client-side types mirroring the backend integrations schemas
 * (`src/jentic_one/control/web/schemas/integrations.py`).
 *
 * These are hand-authored because the endpoints were added after the last
 * OpenAPI regeneration. Once `make openapi` runs, they can be replaced with
 * generated types imported from `@/shared/api`.
 */

export type ScopeClassification = 'read' | 'write' | 'admin';

export interface ReviewScope {
	name: string;
	classification: ScopeClassification;
	default: boolean;
	requested: boolean;
	description: string;
}

export interface ReviewSession {
	session_id: string;
	state: string;
	vendor_key: string;
	vendor_display_name: string;
	resolved_flow: string;
	requested_by_actor_id: string;
	scopes: ReviewScope[];
	/**
	 * Optional free-text supplied by the initiating agent at ``:connect`` time
	 * (``POST /integrations:connect``'s ``reason`` field). Renders under the
	 * agent-request card on the review page so the human approver can see
	 * *why* the agent wants this credential.
	 */
	reason: string | null;
	/**
	 * Rules the initiating agent asked the human owner to approve, captured
	 * verbatim from ``IntegrationsConnectRequest.requested_permission_rules``
	 * at ``:connect`` time. Renders on the rules-page as pre-filled rows the
	 * user can accept / edit / drop. Empty when a user initiated the flow
	 * themselves (nothing to pre-populate).
	 */
	requested_permission_rules: PermissionRule[];
	/**
	 * Where the vendor's OpenAPI lives in the registry. ``version`` is
	 * nullable because the catalog import runs asynchronously — the SPA
	 * treats a 404 from the ops-list endpoint as "still importing".
	 */
	api_reference: {
		vendor: string;
		name: string | null;
		version: string | null;
	};
	/** ``vendor`` for a vendor-registry key, ``api`` for a registry API identity. */
	target_kind?: 'vendor' | 'api';
	/** OAuth scopes the session asks for — compared with an existing credential's grant. */
	requested_scopes?: string[];
	/** Where an API target's live revision came from; null for vendor targets. */
	provenance?: ReviewProvenance | null;
	/** The agent the session binds and its owner (server data). */
	agent?: ReviewAgent | null;
	/** The declared scheme an API target collects; null for vendor targets. */
	scheme?: ReviewScheme | null;
	/** The hosts the credential may be sent to, pinned at ``:connect``. */
	pinned_hosts?: string[] | null;
	/**
	 * Fingerprint of what the approver decides on. Echoed on ``:confirm`` with
	 * ``expected_agent_id``; a mismatch is 409 ``review_stale``.
	 */
	digest: string;
	/** Whether this viewer may confirm the session as it stands. */
	can_confirm: boolean;
	/** Credentials the viewer already holds for the target. */
	existing_credentials?: ExistingCredential[];
}

/** Session states a review can show; the terminal ones never reach the review read. */
export type ReviewSessionState = 'created' | 'awaiting_app' | 'polling' | 'connected';

export interface ReviewProvenance {
	/** ``catalog`` for a public-catalog import; anything else is a submitted spec. */
	origin: string | null;
	catalog_api_id: string | null;
	/** Actor id of whoever submitted the spec, when known. */
	submitted_by: string | null;
	source_url: string | null;
	revision_id: string;
}

export interface ReviewAgent {
	agent_id: string;
	name: string | null;
	owner_id: string | null;
	status: string;
}

export interface ReviewScheme {
	type: 'api_key' | 'bearer' | 'basic' | 'oauth2';
	/** ``header`` / ``query`` / ``cookie`` for an API key. */
	location: string | null;
	field_name: string | null;
}

/**
 * A credential the viewer already holds for the session's target.
 * ``granted_scopes`` / ``missing_scopes`` are set for OAuth credentials only.
 * ``can_reauthorize`` is false whenever another agent is bound to it.
 */
export interface ExistingCredential {
	credential_id: string;
	name: string;
	type: string;
	granted_scopes: string[] | null;
	missing_scopes: string[] | null;
	other_bound_agent_ids: string[];
	can_bind: boolean;
	can_reauthorize: boolean;
}

/**
 * Wire shape for a single agent↔credential permission rule.
 *
 * Mirrors the backend's ``PermissionRuleSchema`` (see
 * ``control/web/schemas/permission_rules.py``): first-match-wins ordered
 * list, evaluated on ``(method, path, operation_id)`` triples. Any of the
 * four match conditions being ``null`` means "match anything for this
 * field"; ``match_mode`` picks how ``path`` is interpreted.
 *
 * Backend model-validator forbids a condition-less ``allow`` — a rule
 * with ``effect=allow`` MUST constrain at least one of methods, path, or
 * operations.
 */
export interface PermissionRule {
	effect: 'allow' | 'deny';
	methods?: string[] | null;
	path?: string | null;
	match_mode?: 'regex' | 'prefix' | 'exact';
	operations?: string[] | null;
	comment?: string | null;
}

/** The OAuth confirm — the default variant (a body without ``kind``). */
export interface ConfirmRequest {
	kind?: 'oauth';
	confirmed_scopes: string[];
	permission_rules: PermissionRule[];
	// Set when the caller wants the credential bound to an agent that
	// wasn't specified at ``:connect`` time (self-flow: session opens
	// on vendor click, agent picked on the rules-page Continue). The
	// server refuses to re-target sessions that already carry an
	// agent_id — so this is only meaningful for user-initiated
	// unbound sessions.
	agent_id?: string | null;
	/** The review the approver saw; checked when given, required for an API target. */
	expected_agent_id?: string | null;
	digest?: string | null;
}

/** Fields every non-OAuth confirm carries: the rules and the review it answers. */
interface ReviewedConfirm {
	permission_rules: PermissionRule[];
	agent_id?: string | null;
	expected_agent_id: string | null;
	digest: string;
}

export interface ApiKeyConfirmRequest extends ReviewedConfirm {
	kind: 'api_key';
	key: string;
}

export interface BearerConfirmRequest extends ReviewedConfirm {
	kind: 'bearer';
	token: string;
}

export interface BasicConfirmRequest extends ReviewedConfirm {
	kind: 'basic';
	username: string;
	password: string;
}

/** Resolve an ``awaiting_app`` session with the approver's own OAuth client. */
export interface OwnOAuthClientConfirmRequest extends ReviewedConfirm {
	kind: 'own_oauth_client';
	client_id: string;
	client_secret: string;
	/** Default to the API's declared authorization-code endpoints when omitted. */
	authorize_url?: string | null;
	token_url?: string | null;
	confirmed_scopes: string[];
}

/** Bind a credential the approver already holds (optionally re-consenting it). */
export interface ExistingCredentialConfirmRequest extends ReviewedConfirm {
	kind: 'existing_credential' | 'reauthorize';
	credential_id: string;
}

/** Every ``:confirm`` body; ``kind`` picks the variant server-side. */
export type ConfirmSessionBody =
	| ConfirmRequest
	| ApiKeyConfirmRequest
	| BearerConfirmRequest
	| BasicConfirmRequest
	| OwnOAuthClientConfirmRequest
	| ExistingCredentialConfirmRequest;

/**
 * Discriminated on ``kind`` — the UI branches on the tag, not on which
 * optional field happens to be populated. Two shapes:
 *
 * * ``device_authorization`` — RFC 8628 result: the user types ``user_code`` at
 *   ``verification_uri``; the SPA polls ``/status`` until connected.
 * * ``authorization_code`` — browser redirect target: the SPA opens
 *   ``authorize_url`` (popup or same-tab); completion lands server-side at
 *   ``/credentials/oauth/callback`` and the SPA observes it via ``/status``.
 */
export type ConfirmResponse = DeviceAuthorizationConfirmResponse | AuthCodeConfirmResponse;

/**
 * Every ``:confirm`` result. Beyond the vendor challenges above:
 *
 * * ``connected`` — the confirm finished the session (a typed secret or an
 *   existing credential); ``credential_id`` is bound to the agent.
 * * ``reauthorize`` — the agent is bound to ``credential_id``; ``authorize_url``
 *   asks the vendor for the wider scopes on that credential.
 */
export type ConfirmSessionResponse =
	ConfirmResponse | ConnectedConfirmResponse | ReauthorizeConfirmResponse;

export interface ConnectedConfirmResponse {
	kind: 'connected';
	credential_id: string;
}

export interface ReauthorizeConfirmResponse {
	kind: 'reauthorize';
	credential_id: string;
	authorize_url: string;
}

export interface DeviceAuthorizationConfirmResponse {
	kind: 'device_authorization';
	user_code: string | null;
	verification_uri: string | null;
	verification_uri_complete: string | null;
	poll_interval_seconds: number | null;
}

export interface AuthCodeConfirmResponse {
	kind: 'authorization_code';
	authorize_url: string;
}

export type SessionStatus = 'pending' | 'polling' | 'connected' | 'failed' | 'expired';

export interface StatusResponse {
	status: SessionStatus;
	connected_as: string | null;
	credential_id: string | null;
	bound_scopes: string[] | null;
	error_code: string | null;
}

export interface ConnectRequest {
	vendor: string;
	/**
	 * Optional user-facing label for the resulting credential. Defaults to
	 * the vendor's display name when omitted. Lets a user distinguish
	 * multiple credentials minted from the same vendor.
	 */
	name?: string | null;
	/**
	 * Optional pin to a specific admin-registered OAuth app. Without it the
	 * server uses the platform config entry, else the vendor's single active
	 * registration — several matching registrations is a 400
	 * `ambiguous_vendor`, so picker cards for registrations always pin.
	 */
	oauth_app_registration_id?: string | null;
	agent_id?: string | null;
	requested_scopes?: string[];
	preferred_flow?: string | null;
}

export interface ConnectResponse {
	session_id: string;
	approval_url: string;
	poll_token: string;
	resolved_flow: string;
}

// ---------------------------------------------------------------------------
// Vendor registry
// ---------------------------------------------------------------------------

export interface VendorSummary {
	/**
	 * Stable per-row UI key. For admin-registered rows this is the
	 * ``oar_...`` registration id; for platform-shipped config entries it is
	 * the vendor slug. Two admin registrations for the same vendor share
	 * ``key`` but differ on ``entry_id`` — key the picker off this so cards
	 * do not collapse.
	 */
	entry_id: string;
	/** Present when the row came from an ``oauth_app_registrations`` row. */
	registration_id?: string | null;
	/** Vendor slug shared by every registration for the same vendor. */
	key: string;
	/**
	 * Config-side fully-qualified vendor id when available; falls back to
	 * ``key`` for DB-only vendors.
	 */
	vendor: string;
	/** Vendor's family display name (e.g. "Gmail"). */
	display_name: string;
	/**
	 * Per-row human label. For DB rows this is the admin-picked registration
	 * name; for config rows this equals ``display_name``.
	 */
	name: string;
	/** ``db`` = admin registration, ``config`` = platform-shipped entry. */
	source: 'db' | 'config';
	flow_kinds: string[];
}

export interface VendorListResponse {
	data: VendorSummary[];
}

export interface VendorFlow {
	kind: string;
}

export interface VendorScopeCatalog {
	name: string;
	classification: ScopeClassification;
	default: boolean;
	description: string;
}

export interface VendorAuthCapabilities {
	vendor: string;
	display_name: string;
	flows: VendorFlow[];
	scopes: VendorScopeCatalog[];
}
