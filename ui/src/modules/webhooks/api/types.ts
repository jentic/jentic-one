/**
 * Webhooks module — UI entities and wire shapes.
 *
 * PROTOTYPE: there is no backend surface yet, so these mirror the wire shapes
 * the mocked handlers serve (snake_case rows adapted into camelCase entities,
 * matching the module conventions used by agents/toolkits).
 */

/**
 * Where deliveries go (Stripe "event destination" pattern):
 * - `https` — generic signed webhook for machines (verify + retry).
 * - `slack` — Slack incoming webhook; events are rendered as human-readable
 *   Block Kit messages and carry no signature (the URL itself is the secret).
 */
export type DestinationType = 'https' | 'slack';

/** Wire status of an endpoint. `failing` is derived client-side. */
export type EndpointWireStatus = 'active' | 'paused' | 'disabled';

/** Display status — `failing` = active but with consecutive delivery failures. */
export type EndpointDisplayStatus = 'active' | 'failing' | 'paused' | 'disabled';

export interface WebhookEndpointRow {
	id: string;
	name: string;
	url: string;
	description: string | null;
	destination_type: DestinationType;
	status: EndpointWireStatus;
	/** Subscribed event types; `['*']` means all events. */
	event_types: string[];
	/** Null for destinations without a signing secret (e.g. Slack). */
	secret_preview: string | null;
	consecutive_failures: number;
	deliveries_24h: number;
	/** 0–1 success ratio over the trailing 24 h; null when no deliveries. */
	success_rate_24h: number | null;
	last_delivery_at: string | null;
	last_delivery_ok: boolean | null;
	created_at: string;
	updated_at: string | null;
}

export interface WebhookEndpointEntity {
	id: string;
	name: string;
	url: string;
	description: string | null;
	destinationType: DestinationType;
	status: EndpointDisplayStatus;
	wireStatus: EndpointWireStatus;
	eventTypes: string[];
	subscribesToAll: boolean;
	secretPreview: string | null;
	consecutiveFailures: number;
	deliveries24h: number;
	successRate24h: number | null;
	lastDeliveryAt: string | null;
	lastDeliveryOk: boolean | null;
	createdAt: string;
	updatedAt: string | null;
}

/** Endpoints with this many consecutive failures render as `failing`. */
const FAILING_THRESHOLD = 3;

export function toEndpointEntity(row: WebhookEndpointRow): WebhookEndpointEntity {
	const subscribesToAll = row.event_types.includes('*');
	const status: EndpointDisplayStatus =
		row.status === 'active' && row.consecutive_failures >= FAILING_THRESHOLD
			? 'failing'
			: row.status;
	return {
		id: row.id,
		name: row.name,
		url: row.url,
		description: row.description,
		destinationType: row.destination_type,
		status,
		wireStatus: row.status,
		eventTypes: row.event_types,
		subscribesToAll,
		secretPreview: row.secret_preview,
		consecutiveFailures: row.consecutive_failures,
		deliveries24h: row.deliveries_24h,
		successRate24h: row.success_rate_24h,
		lastDeliveryAt: row.last_delivery_at,
		lastDeliveryOk: row.last_delivery_ok,
		createdAt: row.created_at,
		updatedAt: row.updated_at,
	};
}

// ---------------------------------------------------------------------------
// Deliveries
// ---------------------------------------------------------------------------

export type DeliveryStatus = 'delivered' | 'pending' | 'failed' | 'exhausted';

export interface DeliveryAttemptRow {
	attempted_at: string;
	ok: boolean;
	http_status: number | null;
	error: string | null;
	latency_ms: number;
}

export interface WebhookDeliveryRow {
	id: string;
	endpoint_id: string;
	event_id: string;
	event_type: string;
	status: DeliveryStatus;
	attempts: DeliveryAttemptRow[];
	next_attempt_at: string | null;
	request_headers: Record<string, string>;
	payload: unknown;
	response_body: string | null;
	is_test: boolean;
	is_redelivery: boolean;
	created_at: string;
}

export interface WebhookDeliveryEntity {
	id: string;
	endpointId: string;
	eventId: string;
	eventType: string;
	status: DeliveryStatus;
	attempts: DeliveryAttemptRow[];
	nextAttemptAt: string | null;
	requestHeaders: Record<string, string>;
	payload: unknown;
	responseBody: string | null;
	isTest: boolean;
	isRedelivery: boolean;
	createdAt: string;
	/** Latency of the most recent attempt, ms. */
	lastLatencyMs: number | null;
	/** HTTP status of the most recent attempt. */
	lastHttpStatus: number | null;
}

export function toDeliveryEntity(row: WebhookDeliveryRow): WebhookDeliveryEntity {
	const last = row.attempts.length > 0 ? row.attempts[row.attempts.length - 1] : null;
	return {
		id: row.id,
		endpointId: row.endpoint_id,
		eventId: row.event_id,
		eventType: row.event_type,
		status: row.status,
		attempts: row.attempts,
		nextAttemptAt: row.next_attempt_at,
		requestHeaders: row.request_headers,
		payload: row.payload,
		responseBody: row.response_body,
		isTest: row.is_test,
		isRedelivery: row.is_redelivery,
		createdAt: row.created_at,
		lastLatencyMs: last?.latency_ms ?? null,
		lastHttpStatus: last?.http_status ?? null,
	};
}

// ---------------------------------------------------------------------------
// Event catalog
// ---------------------------------------------------------------------------

/** One subscribable platform event type (mirrors `EventType` on the backend). */
export interface EventTypeDef {
	type: string;
	/** Namespace group, e.g. `execution`, `credential`, `agent`. */
	group: string;
	description: string;
}

// ---------------------------------------------------------------------------
// Mutation payloads / results
// ---------------------------------------------------------------------------

export interface EndpointCreateInput {
	name: string;
	url: string;
	description: string | null;
	destination_type: DestinationType;
	event_types: string[];
}

export interface EndpointPatch {
	name?: string;
	url?: string;
	description?: string | null;
	event_types?: string[];
}

export interface PingResult {
	ok: boolean;
	http_status: number | null;
	latency_ms: number;
	error: string | null;
}

/**
 * `POST /webhooks` result — for `https` destinations the plaintext secret is
 * returned exactly once; Slack destinations have no signing secret (null).
 */
export interface EndpointCreateResult {
	endpoint: WebhookEndpointEntity;
	secret: string | null;
	ping: PingResult;
}

export interface RotateSecretResult {
	secret: string;
	secretPreview: string;
}
