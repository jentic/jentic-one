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
}

export interface PermissionRule {
	method: string;
	path: string;
	effect: 'allow' | 'deny';
}

export interface ConfirmRequest {
	confirmed_scopes: string[];
	permission_rules: PermissionRule[];
}

export interface ConfirmResponse {
	user_code: string | null;
	verification_uri: string | null;
	verification_uri_complete: string | null;
	poll_interval_seconds: number | null;
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
	key: string;
	vendor: string;
	display_name: string;
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
