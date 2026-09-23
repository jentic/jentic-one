/**
 * ActorLabel — resolve an opaque `actor_id` to a human-readable name.
 *
 * Executions, audit entries, and the events feed carry a raw
 * `actor_id` (a KSUID like `agnt_6a3d3c62…`). Drop this anywhere one of those
 * ids would otherwise be rendered: it looks the actor up in the cached actor
 * directory (`useActorDirectory`) and shows its name, falling back to the raw
 * id (mono font, as before) while the directory is loading or when the id is
 * unknown. The raw id is always available on hover via `title`.
 *
 * Dependency-light by design — one shared hook, no module coupling — so any
 * surface (monitor, dashboard, agents) can use it.
 *
 * Directory scope is `user` / `agent` — the only actor types `GET /actors`
 * returns (toolkits and service accounts are retired actor types). Either can
 * still appear as the `actor_id` of a HISTORICAL execution/audit/event record
 * (`actor_type === "toolkit"` / `"service_account"`; live `jntc_live_…` and
 * `sak_…` keys now resolve as successor agents), so we render those
 * gracefully with a type prefix + the raw `tk_…` / `sva_…` id rather than
 * trying — and failing — to resolve a name that the directory never holds.
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
 * `actor_type='toolkit'` / `'service_account'` — the enum no longer carries
 * (or will soon drop) them, but read paths must still label them rather than
 * round-trip them through `ActorType`. */
const ACTOR_TYPE_LABEL: Record<string, string> = {
	[ActorType.USER]: 'User',
	[ActorType.AGENT]: 'Agent',
	// Retired actor types: neither mints new identities any more (their keys
	// resolve as successor agents), but persisted executions/audit entries
	// still carry the strings — label them so the raw `tk_…` / `sva_…` id reads
	// as what it was rather than an unexplained token.
	service_account: 'Service account',
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
