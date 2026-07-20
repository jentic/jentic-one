/**
 * UI-facing types for the LLM Proxy · Sessions surface.
 *
 * These mirror the shape of `mocks/sessions-mock.json` (the single source of
 * truth while there is no backend — see `docs/plans/llm-proxy-sessions.md`).
 * When the real backend lands, reconcile these with the generated OpenAPI
 * models once and the views stay unchanged.
 */

export type Verdict = 'allow' | 'deny';
export type CallStatus = 'completed' | 'denied' | 'failed';
export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE' | string;
export type AgentRole = 'main' | 'subagent';

/** Per-agent rollup stats (own = just this node; rollup = node + descendants). */
export interface AgentStats {
	calls: number;
	allow: number;
	deny: number;
	error: number;
	cost_usd: number;
	tokens: number;
	apis: string[];
}

/** A node in the agent → subagent → sub-subagent tree. */
export interface ProxyAgent {
	id: string;
	actor_id: string;
	name: string;
	role: AgentRole;
	parent_id: string | null;
	depth: number;
	subagent_type: string;
	spawned_at: string;
	stats: AgentStats;
	rollup: AgentStats;
}

/**
 * The redacted request the agent made — query/path params and (for writes) a
 * JSON body. Any secret/token/key value is redacted to `"***"` in the mock.
 */
export interface CallRequest {
	params?: Record<string, unknown>;
	body?: unknown;
}

/**
 * A compact model of the call's lifecycle timings (ms, relative to call start).
 * `credential_ms` / `upstream_ms` are null when the call was denied at the
 * policy gate (no credential injected, no upstream request made).
 */
export interface CallTimeline {
	queued_ms: number;
	policy_ms: number;
	credential_ms: number | null;
	upstream_ms: number | null;
}

/** The governance rule that matched this call (null when no rule matched). */
export interface CallRule {
	id: string;
	name: string;
	matched: boolean;
}

/** One tool call (a broker execution), enriched with correlation + governance. */
export interface ProxyCall {
	call_id: string;
	session_id: string;
	execution_id: string | null;
	agent_id: string;
	actor_id: string;
	actor_type: string;
	api_vendor: string;
	api_name: string;
	api_version: string;
	operation_id: string;
	method: HttpMethod;
	path: string;
	summary: string;
	verdict: Verdict;
	status: CallStatus;
	http_status: number | null;
	duration_ms: number | null;
	started_at: string;
	error: string | null;
	origin: string;
	destructive: boolean;
	credential_id: string | null;
	credential_provider: string | null;
	credential_wire_type: string | null;
	trace_id: string;
	tokens_in: number;
	tokens_out: number;
	cost_usd: number;
	/** The chat turn that produced this call, if resolvable (chat linkage). */
	turn_id?: string | null;
	/** Redacted request params + body (secrets → `"***"`). */
	request?: CallRequest | null;
	/** A short, redacted response body snippet (JSON or text). */
	response_snippet?: string | null;
	/** Per-stage lifecycle timings (roughly sum to `duration_ms`). */
	timeline?: CallTimeline | null;
	/** The governance rule that matched, if any. */
	rule?: CallRule | null;
	/** Scopes the operation requires. */
	scopes_required?: string[];
	/** Scopes actually granted to the actor. */
	scopes_granted?: string[];
	/** On a deny: a short, actionable "how to grant" hint. */
	grant_hint?: string | null;
	/** Present + true on demo rows that were synthesised (not from the real run). */
	synthesised?: boolean;
}

export interface ChatToolUse {
	name: string;
	preview: string;
}

/** One LLM-proxy round-trip: the agent's thinking + tool intents for a turn. */
export interface ChatTurn {
	turn_id: string;
	/** The agent this turn belongs to, if resolvable (chat linkage). */
	agent_id?: string | null;
	ts: string;
	model: string | null;
	n_messages: number | null;
	first_user_msg: string;
	assistant_text: string;
	tool_uses: ChatToolUse[];
	latency_ms: number | null;
	usage: Record<string, unknown> | null;
	status: string | null;
}

export interface SessionTiles {
	calls: number;
	agents: number;
	apis: number;
	cost_usd: number;
	tokens: number;
}

export interface ProxySession {
	id: string;
	title: string;
	agent_id: string;
	actor_id: string;
	started_at: string | null;
	ended_at: string | null;
	status: string;
	tiles: SessionTiles;
	apis_touched: string[];
}

export interface AccessDenial {
	request_id: string | null;
	status: string | null;
	summary: string | null;
	created_at: string | null;
	actor_id: string | null;
}

/** One bucket in the stacked calls-over-time chart. */
export interface CallsOverTimeBucket {
	t: string;
	allow: number;
	deny: number;
	error: number;
}

export interface ProxyCharts {
	calls_over_time: CallsOverTimeBucket[];
}

/** The run's final deliverable — the main agent's closing synthesis text. */
export interface FinalOutput {
	summary: string;
}

/** The full session bundle (`GET /proxy/sessions/:id` in the future backend). */
export interface SessionBundle {
	session: ProxySession;
	agents: ProxyAgent[];
	calls: ProxyCall[];
	chat: ChatTurn[];
	denials: AccessDenial[];
	charts: ProxyCharts;
	/** The run's final output (closing synthesis), or null if none recorded. */
	final_output: FinalOutput | null;
}

/** The top-level mock document shape. */
export interface SessionsMockDoc {
	generated_at: string;
	source: string;
	note: string;
	sessions: ProxySession[];
	agents: ProxyAgent[];
	calls: ProxyCall[];
	chat: ChatTurn[];
	denials: AccessDenial[];
	charts: ProxyCharts;
	/** Per-session final output, keyed by session id (populated by the enrich script). */
	final_outputs?: Record<string, FinalOutput | null>;
}
