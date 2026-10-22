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
	/** The session's current revision; ``:confirm`` names it as ``revision_id``. */
	revision?: { id: string; number: number } | null;
	/** Edit sessions only: the binding change under review. */
	edit?: ReviewEdit | null;
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
	/** An OAuth scheme's authorize URL — set only when the API's spec is from the public catalog. */
	authorization_url?: string | null;
	/** An OAuth scheme's token URL — set only when the API's spec is from the public catalog. */
	token_url?: string | null;
}

/** A rule in an edit's current or resulting access; ``id`` is null for one the edit adds. */
export interface EditRuleView extends PermissionRule {
	id: string | null;
	/** In a result: who put the rule in the request; null for one the binding has. */
	added_by?: string | null;
}

/** Who asked for one scope change (added or removed), and why. */
export interface EditScopeNote {
	added_by: string | null;
	comment: string | null;
}

/** The shared rule set a binding points at; its rules apply instead of the binding's own. */
export interface EditRuleSetView {
	id: string;
	name: string;
	/** A curated set is never changed: approving copies the rules onto the binding. */
	curated: boolean;
	created_by?: string | null;
	rules: EditRuleView[];
}

/** The binding as the edit session recorded it when it opened. */
export interface EditCurrentAccess {
	rules: EditRuleView[];
	rule_set: EditRuleSetView | null;
	suspended: boolean;
	granted_scopes: string[] | null;
}

/** What approving the current revision leaves in place. */
export interface EditResultAccess {
	target: 'binding' | 'rule_set';
	rules: EditRuleView[];
	scopes: string[];
	/** Each scope the change adds or removes: who asked for it, and why. */
	scope_notes?: Record<string, EditScopeNote>;
	suspended: boolean;
}

/** The binding change an edit session asks a human to approve. */
export interface ReviewEdit {
	binding: { agent_id: string; credential_id: string; credential_name: string | null };
	revision: { id: string; number: number };
	current: EditCurrentAccess;
	result: EditResultAccess;
	/** True when the change could let the agent do more than today. */
	widens_access: boolean;
	/** Other agents bound to the credential: added scopes and shared-set changes reach them. */
	other_bound_agents: BoundAgentRef[];
	/** Approving starts a vendor consent for the added scopes. */
	needs_vendor_consent: boolean;
	/** What the request can't follow since the binding changed under it. */
	conflicts?: EditConflict[];
}

/** A part of an edit request the binding no longer fits: a rule it names is gone. */
export interface EditConflict {
	kind: 'missing_rule';
	rule_id: string;
}

/** One version of what a session asks for (``GET /connect-sessions/{id}/revisions``). */
export interface SessionRevision {
	id: string;
	number: number;
	/** Who began or amended the session; null for a rebase onto a changed binding. */
	created_by: string | null;
	created_at: string;
	proposal: Record<string, unknown>;
	result: { rules?: EditRuleView[]; scopes?: string[]; suspended?: boolean; target?: string };
}

/** Another agent bound to a credential the approver could use. */
export interface BoundAgentRef {
	agent_id: string;
	name: string | null;
}

/**
 * A credential the viewer already holds for the session's target.
 * ``granted_scopes`` / ``missing_scopes`` are set for OAuth credentials only.
 * When ``needs_vendor_consent`` is set, using it first asks the vendor for
 * ``missing_scopes``, which widens the access of every agent in
 * ``other_bound_agents`` too.
 */
export interface ExistingCredential {
	credential_id: string;
	name: string;
	type: string;
	granted_scopes: string[] | null;
	missing_scopes: string[] | null;
	other_bound_agents: BoundAgentRef[];
	can_bind: boolean;
	needs_vendor_consent: boolean;
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
 * Backend model-validator forbids a condition-less ``allow`` or
 * ``require-approval`` — such a rule MUST constrain at least one of
 * methods, path, or operations.
 */
export interface PermissionRule {
	effect: 'allow' | 'deny' | 'require-approval';
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
	/** The revision the approver saw; a newer one is 409 ``revision_changed``. */
	revision_id?: string | null;
}

/** Fields every non-OAuth confirm carries: the rules and the review it answers. */
interface ReviewedConfirm {
	permission_rules: PermissionRule[];
	agent_id?: string | null;
	expected_agent_id: string | null;
	digest: string;
	revision_id: string | null;
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

/** Bind a credential the approver already holds (consenting to any missing scopes first). */
export interface ExistingCredentialConfirmRequest extends ReviewedConfirm {
	kind: 'existing_credential';
	credential_id: string;
}

/**
 * Approve an edit session's change with the final rules the reviewer chose.
 * ``confirmed_scopes`` beyond the credential's grant start a vendor consent.
 */
export interface EditConfirmRequest {
	kind: 'edit';
	revision_id: string;
	permission_rules: PermissionRule[];
	resume: boolean;
	confirmed_scopes?: string[] | null;
	/** Approve a revision listing conflicts: the reviewer's rules settle them. */
	resolve_conflicts?: boolean;
}

/** Every ``:confirm`` body; ``kind`` picks the variant server-side. */
export type ConfirmSessionBody =
	| ConfirmRequest
	| ApiKeyConfirmRequest
	| BearerConfirmRequest
	| BasicConfirmRequest
	| OwnOAuthClientConfirmRequest
	| ExistingCredentialConfirmRequest
	| EditConfirmRequest;

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
 * Every ``:confirm`` result. Beyond the vendor challenges above, ``connected``
 * means the confirm finished the session (a typed secret or an existing
 * credential); ``credential_id`` is bound to the agent. An existing credential
 * that lacks scopes answers with a vendor challenge instead, and binds once
 * the grant lands.
 */
export type ConfirmSessionResponse = ConfirmResponse | ConnectedConfirmResponse;

export interface ConnectedConfirmResponse {
	kind: 'connected';
	credential_id: string;
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

/** ``applied``: an edit session's change was approved and landed (edit sessions only). */
export type SessionStatus = 'pending' | 'polling' | 'connected' | 'applied' | 'failed' | 'expired';

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

/** ``POST /integrations:edit-connection``: ask to change one binding's access. */
export interface EditConnectionBody {
	/** The binding's agent; a person names it, an agent caller omits it. */
	agent_id?: string;
	credential_id: string;
	/** The full rule list to replace today's with (the edit's ``set``). */
	rules?: { set: PermissionRule[] };
	/** The scope change, shaped like the rule change: ``add``/``remove`` or ``set``. */
	scopes?: { add?: string[]; remove?: string[]; set?: string[] };
	resume?: boolean;
	reason?: string;
	/** A person approving their own change as they make it: written at once. */
	apply?: boolean;
}

export interface ConnectResponse {
	session_id: string;
	approval_url: string;
	poll_token: string;
	resolved_flow: string;
	/** Revision 1; a confirm names it until the session is amended. */
	revision_id?: string | null;
}

/** ``:amend`` on an edit session: the parts sent replace those of the ask. */
export interface AmendEditBody {
	rules?: { set: PermissionRule[] };
	scopes?: { set: string[] };
}

/** ``:amend`` on a new connection request: the rules sent replace the ask's. */
export interface AmendCreateBody {
	requested_permission_rules?: PermissionRule[];
}

export type AmendSessionBody = AmendEditBody | AmendCreateBody;

/** ``:amend``'s answer: the session's new current revision. */
export interface AmendedSession {
	session_id: string;
	revision_id: string;
	revision_number: number;
}

/** ``:edit-connection``'s answer: with ``apply``, whether it landed or waits on the vendor. */
export interface EditConnectionResult extends ConnectResponse {
	status: 'pending' | 'applied' | 'consenting';
	/** With ``apply`` and added scopes: the vendor sign-in to complete. */
	challenge: ConfirmResponse | null;
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
	/**
	 * For ``db`` rows, the catalog API the shared app signs in to
	 * (``<domain>/<sub>``); ``null`` for config rows.
	 */
	catalog_api_id?: string | null;
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
