/**
 * ActorLabel — resolve an opaque `actor_id` to a human-readable name.
 *
 * Executions, audit entries, and the events feed carry a raw
 * `actor_id` (a KSUID like `agnt_6a3d3c62…`). Drop this anywhere one of those
 * ids would otherwise be rendered: it looks the actor up in the cached actor
 * directory (`useActorDirectory`) and shows its name, falling back to the raw
 * id (mono font) while the directory is loading or when the id is unknown.
 * Callers without `users:read` resolve the id through the batched by-id
 * lookup instead of the full directory. The raw id is always available on hover via `title`.
 *
 * Dependency-light by design — one shared hook, no module coupling — so any
 * surface (monitor, rail, agents) can use it.
 *
 * Directory scope is `user` / `agent` — the only actor types `GET /actors`
 * and `GET /actors/lookup` return (toolkits and service accounts are retired actor types). Either can
 * still appear as the `actor_id` of a HISTORICAL execution/audit/event record
 * (`actor_type === "toolkit"` / `"service_account"`; live `jntc_live_…` keys
 * now resolve as successor agents, `sak_…` keys are refused), so we render those
 * gracefully with a type prefix + the raw `tk_…` / `sva_…` id rather than
 * trying — and failing — to resolve a name that the directory never holds.
 *
 * An agent the caller may not see (the by-id lookup answers for it with no
 * match — another user's agent, for a caller without `users:read`) renders as
 * "Agent (not visible to you)" rather than a bare id; the id stays on hover.
 *
 * Some attribution fields carry non-id SENTINELS rather than a KSUID — e.g.
 * `registered_by: "self"` (an agent self-registered via DCR). Those render as a
 * plain word ("Self") instead of a mono id-token, so they never masquerade as an
 * unresolved opaque id.
 */
import { ActorType } from '@/shared/api';
import { useActorDirectory } from '@/shared/hooks';
import {
	RETIRED_SERVICE_ACCOUNT_ACTOR_TYPE,
	RETIRED_SERVICE_ACCOUNT_SUFFIX,
	SERVICE_ACCOUNT_SUCCESSOR_REGISTRAR,
} from '@/shared/lib/retiredActors';

/** Subtle, human-friendly noun for each actor type. Keyed by the wire string
 * (not the enum) because historical rows persist the retired
 * `actor_type='toolkit'` / `'service_account'` — the enum no longer carries
 * them, but read paths must still label them rather than round-trip them
 * through `ActorType`. */
const ACTOR_TYPE_LABEL: Record<string, string> = {
	[ActorType.USER]: 'User',
	[ActorType.AGENT]: 'Agent',
	// Retired actor types: neither mints new identities any more (their keys
	// resolve as successor agents), but persisted executions/audit entries
	// still carry the strings — label them so the raw `tk_…` / `sva_…` id reads
	// as what it was rather than an unexplained token. (An unresolved
	// `service_account` id renders with a "(retired service account)" suffix
	// instead — see below.)
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
	// `registered_by` on every successor agent the theme-8 migration minted.
	[SERVICE_ACCOUNT_SUCCESSOR_REGISTRAR]: 'Service-account migration',
};

/** The prefix every agent id carries. */
const AGENT_ID_PREFIX = 'agnt_';

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
	// Only a real id needs resolving: a caller-supplied name or a sentinel never
	// reaches the directory (and never costs a lookup).
	const needsLookup =
		resolvedName === undefined &&
		!Object.prototype.hasOwnProperty.call(ACTOR_SENTINEL_LABEL, actorId);
	const { resolve, isHidden } = useActorDirectory(needsLookup ? [actorId] : []);
	const name = resolvedName ?? resolve(actorId);
	const typeLabel = typePrefix(actorType);

	// Resolved → friendly name (with an optional subtle type prefix). The raw id
	// stays reachable on hover so operators can still copy/correlate it.
	if (name) {
		return (
			// `dir="auto"` isolates the resolved name: a direction override inside
			// an operator-chosen agent name is resolved within this span and cannot
			// reverse the copy around the label (#1543). It sits on the element that
			// already exists rather than nesting a `<bdi>`, so the DOM — and the
			// `title` an operator hovers — stays exactly as it was.
			<span className={className} title={actorId} dir="auto">
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

	// Historical service-account actor (theme 8): nothing left to resolve or
	// link to, so show the raw `sva_…` id marked as retired. Matches the Monitor
	// usage label and the enterprise admin console.
	if (actorType === RETIRED_SERVICE_ACCOUNT_ACTOR_TYPE) {
		return (
			<span className={className} title={actorId}>
				<span className="font-mono">{actorId}</span>{' '}
				<span className="text-muted-foreground">{RETIRED_SERVICE_ACCOUNT_SUFFIX}</span>
			</span>
		);
	}

	// An agent the lookup answered for without a match is outside what this caller
	// may see: say so, rather than print an id that resolves to nothing for them.
	const isAgent =
		actorType === ActorType.AGENT || (actorType == null && actorId.startsWith(AGENT_ID_PREFIX));
	if (isAgent && isHidden(actorId)) {
		return (
			<span className={className} title={actorId} data-testid="actor-label-hidden">
				Agent <span className="text-muted-foreground">(not visible to you)</span>
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
