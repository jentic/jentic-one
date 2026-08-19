/**
 * The relayable outbound-event catalog.
 *
 * This is a **curated front-end mirror** of the backend event truth. There is no
 * backend endpoint that returns event *descriptions*, so the picker needs a
 * source of human copy — and inventing types would let the UI offer a
 * subscription the platform can never deliver. Every entry below is transcribed
 * from the backend, with its source noted, so the list stays honest:
 *
 * - **Types**: `EventType` in `src/jentic_one/shared/models/events.py` — the
 *   `ALL` frozenset is the full internal catalog.
 * - **Relayable set**: `ALL` minus `NEVER_RELAYED` in
 *   `src/jentic_one/admin/services/webhooks/fanout.py`. The three withheld types
 *   (`credential.accessed`, `instance.booted`, `instance.initialized`) are
 *   deliberately absent here — an endpoint can never receive them, so offering
 *   them would be a lie.
 * - **Descriptions/severity**: paraphrased from each event's `emit_event(...)`
 *   call site (the `summary=` string and `severity=`) — cited per entry.
 *
 * If the backend adds or renames a relayable type, update this file to match; a
 * test (`eventCatalog.test.ts`) pins the shape so a typo is caught early.
 */

/** A coarse grouping used only to organise the picker visually. */
export type WebhookEventCategory =
	'credentials' | 'executions' | 'catalog' | 'access' | 'toolkits' | 'agents' | 'security';

export interface WebhookEventTypeInfo {
	/** The exact `event_type` string the backend emits and filters on. */
	type: string;
	/** Short human label for the picker row. */
	label: string;
	/** One-sentence "what it means / when it fires", grounded in the emit site. */
	description: string;
	category: WebhookEventCategory;
	/**
	 * Whether the backend emits this with `requires_action=True` — surfaced as a
	 * subtle "needs action" hint, because these are the events an operator is
	 * most likely to want routed to an alert channel.
	 */
	actionable?: boolean;
}

export const WEBHOOK_EVENT_CATEGORY_LABELS: Record<WebhookEventCategory, string> = {
	credentials: 'Credentials',
	executions: 'Executions & jobs',
	catalog: 'Catalog & overlays',
	access: 'Access requests',
	toolkits: 'Toolkits',
	agents: 'Agents',
	security: 'Security',
};

/**
 * Every event an outbound endpoint can actually receive, in a sensible reading
 * order. Grouped by `category` in the picker.
 */
export const WEBHOOK_EVENT_CATALOG: readonly WebhookEventTypeInfo[] = [
	// --- Credentials -----------------------------------------------------
	{
		type: 'credential.expired',
		label: 'Credential expired',
		description:
			'A stored credential passed its expiry — calls that rely on it will now start failing until it is reconnected.',
		category: 'credentials',
		actionable: true,
	},
	{
		type: 'credential.expiring_soon',
		label: 'Credential expiring soon',
		description:
			'A stored credential is approaching its expiry, so you can reconnect it before anything breaks.',
		category: 'credentials',
	},
	{
		type: 'credential.stored',
		label: 'Credential stored',
		description: 'A new credential was saved to the platform.',
		category: 'credentials',
	},
	{
		type: 'credential.connected',
		label: 'Credential connected',
		description: 'An OAuth connect flow completed and the credential is now usable.',
		category: 'credentials',
	},
	{
		type: 'credential.connection_failed',
		label: 'Credential connection failed',
		description: 'An OAuth connect flow failed before the credential could be used.',
		category: 'credentials',
	},
	{
		type: 'credential.refresh_failed',
		label: 'Credential refresh failed',
		description:
			"Jentic's OAuth refresh for a credential was rejected upstream; an agent call needs the credential reconnected.",
		category: 'credentials',
	},
	{
		type: 'credential.not_provisioned',
		label: 'Credential not provisioned',
		description:
			'An agent tried to call an API for which no credential is provisioned — an operator must add one.',
		category: 'credentials',
	},
	{
		type: 'credential.undecryptable',
		label: 'Credential undecryptable',
		description:
			'A stored credential can no longer be decrypted with the configured keys; retrying will not help — it must be removed and re-added.',
		category: 'credentials',
		actionable: true,
	},
	{
		type: 'credential.bound_to_toolkit',
		label: 'Credential bound to toolkit',
		description: 'A credential was attached to a toolkit so its tools can authenticate.',
		category: 'credentials',
	},
	{
		type: 'credential.unbound_from_toolkit',
		label: 'Credential unbound from toolkit',
		description: 'A credential was detached from a toolkit.',
		category: 'credentials',
	},

	// --- Executions & jobs ----------------------------------------------
	{
		type: 'execution.completed',
		label: 'Execution completed',
		description: 'An operation the broker ran finished successfully.',
		category: 'executions',
	},
	{
		type: 'execution.failed',
		label: 'Execution failed',
		description: 'An operation the broker ran failed; carries a source tag for why it failed.',
		category: 'executions',
		actionable: true,
	},
	{
		type: 'execution.repeated_failure',
		label: 'Execution repeated failure',
		description:
			'The same operation on the same toolkit failed repeatedly inside a short window — a signal something is systematically broken.',
		category: 'executions',
		actionable: true,
	},
	{
		type: 'upstream.circuit_open',
		label: 'Upstream circuit opened',
		description:
			'The circuit breaker for an upstream host opened after repeated failures, so calls to it are being shed.',
		category: 'executions',
	},
	{
		type: 'import.completed',
		label: 'Import completed',
		description: 'An API-spec import job finished successfully.',
		category: 'executions',
	},
	{
		type: 'import.failed',
		label: 'Import failed',
		description: 'An API-spec import job failed and needs attention.',
		category: 'executions',
		actionable: true,
	},
	{
		type: 'job.failed_permanently',
		label: 'Job failed permanently',
		description:
			'A background job exhausted its retries and was dead-lettered rather than retried forever.',
		category: 'executions',
		actionable: true,
	},

	// --- Catalog & overlays ---------------------------------------------
	{
		type: 'catalog.update_available',
		label: 'Catalog update available',
		description: "A registered API's upstream spec changed; re-import to pick up the update.",
		category: 'catalog',
		actionable: true,
	},
	{
		type: 'catalog.update_conflicts_overlay',
		label: 'Catalog update conflicts with overlay',
		description:
			"An upstream spec change collides with a confirmed overlay's base — adopting it would supersede your overlay, so it is an operator decision.",
		category: 'catalog',
		actionable: true,
	},
	{
		type: 'overlay.deprecated',
		label: 'Overlay deprecated',
		description:
			'A live confirmed overlay was auto-deprecated by an authorized catalog re-import that adopted the upstream spec.',
		category: 'catalog',
	},

	// --- Access requests -------------------------------------------------
	{
		type: 'access_request.filed',
		label: 'Access request filed',
		description:
			'An agent filed a request for access that an operator needs to approve or deny.',
		category: 'access',
		actionable: true,
	},
	{
		type: 'access_request.approved',
		label: 'Access request approved',
		description: 'A pending access request was approved.',
		category: 'access',
	},
	{
		type: 'access_request.denied',
		label: 'Access request denied',
		description: 'A pending access request was denied.',
		category: 'access',
	},
	{
		type: 'access_request.withdrawn',
		label: 'Access request withdrawn',
		description: 'A pending access request was withdrawn by its requester.',
		category: 'access',
	},

	// --- Toolkits --------------------------------------------------------
	{
		type: 'toolkit.created',
		label: 'Toolkit created',
		description: 'A new toolkit was created.',
		category: 'toolkits',
	},
	{
		type: 'toolkit.key_created',
		label: 'Toolkit key created',
		description: 'A new API key was minted for a toolkit.',
		category: 'toolkits',
	},
	{
		type: 'toolkit.permission_rule_set',
		label: 'Toolkit permission rule set',
		description: "Permission rules on a toolkit's credential binding were set or patched.",
		category: 'toolkits',
	},

	// --- Agents ----------------------------------------------------------
	{
		type: 'agent.created',
		label: 'Agent created',
		description: 'A new agent was created.',
		category: 'agents',
	},
	{
		type: 'agent.self_registered',
		label: 'Agent self-registered',
		description: 'An agent self-registered and is awaiting operator approval.',
		category: 'agents',
		actionable: true,
	},
	{
		type: 'agent.registration_approved',
		label: 'Agent registration approved',
		description: "An agent's registration was approved.",
		category: 'agents',
	},
	{
		type: 'agent.registration_denied',
		label: 'Agent registration denied',
		description: "An agent's registration was denied.",
		category: 'agents',
	},
	{
		type: 'toolkit.bound_to_agent',
		label: 'Toolkit bound to agent',
		description: 'A toolkit was bound to an agent, granting it those tools.',
		category: 'agents',
	},
	{
		type: 'toolkit.unbound_from_agent',
		label: 'Toolkit unbound from agent',
		description: 'A toolkit was unbound from an agent.',
		category: 'agents',
	},

	// --- Security --------------------------------------------------------
	{
		type: 'security.unauthorized_access_attempt',
		label: 'Unauthorized access attempt',
		description:
			'An agent exceeded the authorization-failure threshold in a short window — a possible probing or misconfiguration signal.',
		category: 'security',
		actionable: true,
	},
	{
		type: 'broker.pbac_denied',
		label: 'Permission denied (PBAC)',
		description: 'The broker denied an operation because a toolkit permission rule forbids it.',
		category: 'security',
	},
	{
		type: 'broker.toolkit_binding_unserved',
		label: 'Toolkit binding unserved',
		description:
			'An agent needs an API that no toolkit yet serves — provision a credential to enable the binding.',
		category: 'security',
	},
] as const;

/** Fast lookup from `event_type` string to its catalog entry. */
export const WEBHOOK_EVENT_BY_TYPE: ReadonlyMap<string, WebhookEventTypeInfo> = new Map(
	WEBHOOK_EVENT_CATALOG.map((info) => [info.type, info]),
);
