/**
 * ActorLabel — resolve an opaque `actor_id` to a human-readable name.
 *
 * Executions, audit entries, the events feed, and access requests carry a raw
 * `actor_id` (a KSUID like `agnt_6a3d3c62…`). Drop this anywhere one of those
 * ids would otherwise be rendered: it looks the actor up in the cached actor
 * directory (`useActorDirectory`) and shows its name, falling back to the raw
 * id (mono font, as before) while the directory is loading or when the id is
 * unknown. The raw id is always available on hover via `title`.
 *
 * Dependency-light by design — one shared hook, no module coupling — so any
 * surface (monitor, dashboard, agents, toolkits, access-requests) can use it.
 *
 * Directory scope is `user` / `agent` / `service_account` — those are the only
 * actor types `GET /actors` returns (the backend UNION excludes toolkits;
 * "Toolkits are not platform actors"). A toolkit can still appear as the
 * `actor_id` of a HISTORICAL execution/audit/event record with
 * `actor_type === "toolkit"` (a retired actor type; live `jntc_live_…` keys now
 * resolve as migrated service accounts), so we render those gracefully with a
 * "Toolkit" prefix + the raw `tk_…` id rather than trying — and failing — to
 * resolve a name that the directory never holds.
 *
 * Some attribution fields carry non-id SENTINELS rather than a KSUID — e.g.
 * `registered_by: "self"` (an agent self-registered via DCR). Those render as a
 * plain word ("Self") instead of a mono id-token, so they never masquerade as an
 * unresolved opaque id.
 */
import { ActorType } from '@/shared/api';
import { useActorDirectory } from '@/shared/hooks';

/** Subtle, human-friendly noun for each actor type. Keyed by the wire string
 * (not the enum) because historical rows persist the retired
 * `actor_type='toolkit'` — the enum no longer carries it, but read paths must
 * still label it rather than round-trip it through `ActorType`. */
const ACTOR_TYPE_LABEL: Record<string, string> = {
	[ActorType.USER]: 'User',
	[ActorType.AGENT]: 'Agent',
	[ActorType.SERVICE_ACCOUNT]: 'Service account',
	// Retired actor type: toolkits never mint new identities (their keys resolve
	// as migrated service accounts), but persisted executions/audit entries still
	// carry the string — label them so the raw `tk_…` id reads as a toolkit
	// rather than an unexplained token.
	toolkit: 'Toolkit',
};

/**
 * Non-id sentinel actor values the backend uses in attribution fields. These are
 * NOT opaque ids — `registered_by: "self"` means the actor self-registered via
 * DCR — so they must render as plain words, never as a mono id-looking token.
 */
const ACTOR_SENTINEL_LABEL: Record<string, string> = {
	self: 'Self',
	system: 'System',
};

/** A subtle type prefix for a known `actor_type`, or undefined otherwise. */
function typePrefix(actorType: ActorType | string | null | undefined): string | undefined {
	if (actorType == null) return undefined;
	return ACTOR_TYPE_LABEL[actorType];
}

export interface ActorLabelProps {
	/** The opaque actor id to resolve (e.g. `agnt_6a3d3c62…`). */
	actorId: string;
	/** Optional hint used for a subtle type prefix; accepts the enum or a raw string. */
	actorType?: ActorType | string | null;
	/**
	 * A name the caller already resolved out-of-band (e.g. a direct
	 * `GET /agents/{id}` fallback when the cached directory predates a
	 * just-registered agent). Takes precedence over the directory lookup so a
	 * surface that resolved the name elsewhere never shows the raw id here.
	 */
	resolvedName?: string;
	className?: string;
}

export function ActorLabel({ actorId, actorType, resolvedName, className }: ActorLabelProps) {
	const { resolve } = useActorDirectory();
	const name = resolvedName ?? resolve(actorId);
	const typeLabel = typePrefix(actorType);

	// Resolved → friendly name (with an optional subtle type prefix). The raw id
	// stays reachable on hover so operators can still copy/correlate it.
	if (name) {
		return (
			<span className={className} title={actorId}>
				{typeLabel && <span className="text-muted-foreground">{typeLabel} </span>}
				{name}
			</span>
		);
	}

	// Known non-id sentinel (e.g. "self") → a plain word, NOT a mono id-token, so
	// "registered by self" doesn't masquerade as an unresolved opaque id.
	const sentinel = ACTOR_SENTINEL_LABEL[actorId];
	if (sentinel) {
		return (
			<span className={className} title={actorId}>
				{sentinel}
			</span>
		);
	}

	// Loading or unknown id (incl. toolkits, which the directory never holds) →
	// the raw id in mono, prefixed by the subtle type noun when we have one so a
	// `tk_…` reads as "Toolkit tk_…" instead of a bare token.
	return (
		<span className={className} title={actorId}>
			{typeLabel && <span className="text-muted-foreground">{typeLabel} </span>}
			<span className="font-mono">{actorId}</span>
		</span>
	);
}
