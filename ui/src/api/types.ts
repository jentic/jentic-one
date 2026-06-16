// TypeScript types derived from OpenAPI spec

export interface UserOut {
	logged_in: boolean;
	username?: string | null;
	status?: string;
}

export interface HealthOut {
	status: string;
	default_key_claimed?: boolean;
	setup_url?: string | null;
	[key: string]: unknown;
}

export interface DefaultKeyOut {
	key: string;
}

export interface ToolkitKeyOut {
	id: string;
	name?: string | null;
	prefix?: string | null;
	allowed_ips?: string[] | null;
	revoked?: boolean;
	created_at?: number | null;
}

export interface ToolkitKeyCreated extends ToolkitKeyOut {
	key: string;
}

export interface KeyCreate {
	name?: string | null;
	allowed_ips?: string[] | null;
}

export interface CredentialBindingOut {
	credential_id: string;
	label?: string | null;
	api_id?: string | null;
	scheme_name?: string | null;
}

export interface ToolkitOut {
	id: string;
	name: string;
	description?: string | null;
	created_at?: number | null;
	simulate?: boolean;
	disabled?: boolean;
	keys: ToolkitKeyOut[];
	credentials: CredentialBindingOut[];
	permissions: Record<string, unknown>[];
	pending_requests?: number;
}

/** A single agent granted access to a toolkit (reverse of agent grants). */
export interface ToolkitAgentRow {
	client_id: string;
	client_name: string;
	status: string;
	granted_at?: number | null;
	granted_by?: string | null;
}

/** Response shape of `GET /toolkits/{id}/agents`. */
export interface ToolkitAgentsResponse {
	agents: ToolkitAgentRow[];
}

export interface ToolkitCreate {
	name: string;
	description?: string | null;
	simulate?: boolean;
	initial_key_label?: string | null;
}

export interface ToolkitPatch {
	name?: string | null;
	description?: string | null;
	simulate?: boolean | null;
	disabled?: boolean | null;
}

export interface PermissionRule {
	effect: 'allow' | 'deny';
	methods?: string[] | null;
	path?: string | null;
	operations?: string[] | null;
	_system?: boolean;
}

export interface AccessRequestOut {
	id: string;
	toolkit_id: string;
	type: 'grant' | 'modify_permissions';
	payload: Record<string, unknown>;
	status: 'pending' | 'approved' | 'denied';
	reason?: string | null;
	description?: string | null;
	approve_url?: string | null;
	created_at?: number | null;
	resolved_at?: number | null;
	applied_effects?: string[] | null;
}

export interface CredentialOut {
	id: string;
	label: string;
	api_id?: string | null;
	auth_type?: string | null;
	identity?: string | null;
	server_variables?: Record<string, string> | null;
	scheme?: Record<string, unknown> | null;
	routes?: string[] | null;
	// NB: the backend / generated client narrow `auth_type` to a fixed enum; we
	// keep it as a free string here because the UI also renders Pipedream-synced
	// values. `scheme_name` is deliberately NOT on this type — it is only
	// returned by CredentialBindingOut, never by GET /credentials.
	description?: string | null;
	created_at?: number | null;
	updated_at?: number | null;
	last_used_at?: number | null;
	account_id?: string | null;
	app_slug?: string | null;
	synced_at?: number | null;
	healthy?: boolean | null;
	/**
	 * Unix timestamp of the last health observation — a broker call verdict
	 * (<400 → healthy, 401/403 → broken) or an explicit Test connection.
	 * Null until the credential has been exercised. Drives the "checked Xm ago"
	 * line in the StatusDot tooltip.
	 */
	health_checked_at?: number | null;
}

export interface CredentialCreate {
	label: string;
	api_id?: string | null;
	auth_type?: 'bearer' | 'basic' | 'apiKey' | 'oauth2' | 'none' | null;
	identity?: string | null;
	value: string;
	server_variables?: Record<string, string> | null;
	description?: string | null;
}

export interface CredentialPatch {
	label?: string | null;
	api_id?: string | null;
	auth_type?: 'bearer' | 'basic' | 'apiKey' | 'oauth2' | 'none' | null;
	identity?: string | null;
	value?: string | null;
	server_variables?: Record<string, string> | null;
	scheme?: Record<string, unknown> | null;
	routes?: string[] | null;
	description?: string | null;
}

export interface ApiOut {
	id: string;
	name?: string | null;
	description?: string | null;
	base_url?: string | null;
	version?: string | null;
	operation_count?: number | null;
	created_at?: number | null;
	[key: string]: unknown;
}

export interface ApiListPage {
	items?: ApiOut[];
	data?: ApiOut[];
	total?: number | null;
	page?: number | null;
	size?: number | null;
}

export interface OperationOut {
	id?: string | null;
	capability_id?: string | null;
	method?: string | null;
	path?: string | null;
	summary?: string | null;
	description?: string | null;
	[key: string]: unknown;
}

export interface OperationListPage {
	items: OperationOut[];
	total?: number | null;
	page?: number | null;
	size?: number | null;
}

export interface SearchResult {
	capability_id?: string | null;
	api_id?: string | null;
	api_name?: string | null;
	method?: string | null;
	path?: string | null;
	summary?: string | null;
	description?: string | null;
	score?: number | null;
	registered?: boolean;
	type?: 'operation' | 'workflow';
	[key: string]: unknown;
}

export interface WorkflowOut {
	slug: string;
	name?: string | null;
	description?: string | null;
	steps?: WorkflowStep[];
	inputs?: Record<string, unknown>;
	involved_apis?: string[];
	[key: string]: unknown;
}

export interface WorkflowStep {
	id: string;
	operation?: string | null;
	description?: string | null;
	[key: string]: unknown;
}

export interface TraceOut {
	id: string;
	toolkit_id?: string | null;
	agent_id?: string | null;
	operation_id?: string | null;
	workflow_id?: string | null;
	spec_path?: string | null;
	status?: string | null;
	http_status?: number | null;
	duration_ms?: number | null;
	error?: string | null;
	created_at?: number | null;
	completed_at?: number | null;
	job_id?: string | null;
	parent_trace_id?: string | null;
	api_id?: string | null;
	api_name?: string | null;
	inputs?: Record<string, unknown> | null;
	outputs?: Record<string, unknown> | null;
	steps?: TraceStepOut[];
	children?: TraceChildOut[];
	[key: string]: unknown;
}

export interface TraceChildOut {
	id: string;
	operation_id?: string | null;
	status?: string | null;
	http_status?: number | null;
	duration_ms?: number | null;
	created_at?: number | null;
	api_id?: string | null;
	api_name?: string | null;
	[key: string]: unknown;
}

export interface TraceStepOut {
	id?: string | null;
	step_id?: string | null;
	operation?: string | null;
	status?: string | null;
	http_status?: number | null;
	output?: unknown;
	inputs?: unknown;
	error?: string | null;
	started_at?: number | null;
	completed_at?: number | null;
	[key: string]: unknown;
}

export interface TraceListPage {
	traces: TraceOut[];
	total?: number | null;
	limit?: number | null;
	offset?: number | null;
}

export interface UsageStats {
	total: number;
	success: number;
	failed: number;
	pending: number;
	avg_ms?: number | null;
	p50_ms?: number | null;
	p95_ms?: number | null;
	active_now: number;
	[key: string]: unknown;
}

export interface UsageBucket {
	ts: number;
	total: number;
	success: number;
	failed: number;
	avg_ms?: number | null;
	[key: string]: unknown;
}

export interface UsageTopRow {
	key: string;
	label?: string | null;
	total: number;
	success: number;
	failed: number;
	avg_ms?: number | null;
	trend?: number[] | null;
	[key: string]: unknown;
}

export interface UsageResponse {
	since: number;
	until: number;
	bucket_seconds: number;
	group_by: 'toolkit' | 'api' | 'agent' | string;
	stats: UsageStats;
	buckets: UsageBucket[];
	top: UsageTopRow[];
	[key: string]: unknown;
}

export interface JobOut {
	job_id: string;
	kind?: string | null;
	capability?: string | null;
	toolkit_id?: string | null;
	agent_id?: string | null;
	status: 'pending' | 'running' | 'complete' | 'failed' | 'upstream_async' | string;
	result?: unknown;
	error?: string | null;
	http_status?: number | null;
	upstream_async?: boolean;
	upstream_job_url?: string | null;
	trace_id?: string | null;
	parent_trace_id?: string | null;
	created_at?: number | null;
	completed_at?: number | null;
	[key: string]: unknown;
}

export interface JobListPage {
	data: JobOut[];
	total?: number | null;
	page?: number | null;
	limit?: number | null;
	total_pages?: number | null;
	has_more?: boolean;
}

export interface ImportRequest {
	source: string;
	type?: string | null;
	[key: string]: unknown;
}

export interface ImportOut {
	id?: string | null;
	status?: string | null;
	message?: string | null;
	[key: string]: unknown;
}

export interface NoteCreate {
	resource: string;
	content: string;
}

export interface NoteOut {
	id: string;
	resource: string;
	content: string;
	created_at?: number | null;
}

export interface OverlaySubmit {
	content: string;
	contributor?: string | null;
}

export interface OverlayOut {
	id: string;
	status?: string | null;
	contributor?: string | null;
	created_at?: number | null;
	[key: string]: unknown;
}

export interface SchemeInput {
	scheme_name: string;
	scheme_type?: string | null;
	[key: string]: unknown;
}

export interface CatalogEntry {
	id: string;
	name?: string | null;
	domain?: string | null;
	description?: string | null;
	registered?: boolean;
	[key: string]: unknown;
}

export interface PermissionsPatch {
	add?: PermissionRule[];
	remove?: PermissionRule[];
}

export interface UserCreate {
	username: string;
	password: string;
}
