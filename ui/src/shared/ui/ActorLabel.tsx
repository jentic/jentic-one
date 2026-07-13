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
 * "Toolkits are not platform actors"). A toolkit CAN still appear as the
 * `actor_id` of an execution/audit/event record with `actor_type === "toolkit"`
 * (a `jntc_live_…` key authenticating "as the toolkit" on the broker path), so
 * we render those gracefully with a "Toolkit" prefix + the raw `tk_…` id rather
 * than trying — and failing — to resolve a name that the directory never holds.
 *
 * Some attribution fields carry non-id SENTINELS rather than a KSUID — e.g.
 * `registered_by: "self"` (an agent self-registered via DCR). Those render as a
 * plain word ("Self") instead of a mono id-token, so they never masquerade as an
 * unresolved opaque id.
 */
import { ActorType } from '@/shared/api';
import { useActorDirectory } from '@/shared/hooks';

/** Subtle, human-friendly noun for each actor type. */
const ACTOR_TYPE_LABEL: Partial<Record<ActorType, string>> = {
	[ActorType.USER]: 'User',
	[ActorType.AGENT]: 'Agent',
	[ActorType.SERVICE_ACCOUNT]: 'Service account',
	// Toolkits are never in the actor directory, but they surface as the actor of
	// broker-path executions/audit entries — label them so the raw `tk_…` id reads
	// as a toolkit rather than an unexplained token.
	[ActorType.TOOLKIT]: 'Toolkit',
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
	return ACTOR_TYPE_LABEL[actorType as ActorType];
}

export interface ActorLabelProps {
	/** The opaque actor id to resolve (e.g. `agnt_6a3d3c62…`). */
	actorId: string;
	/** Optional hint used for a subtle type prefix; accepts the enum or a raw string. */
	actorType?: ActorType | string | null;
	className?: string;
}

export function ActorLabel({ actorId, actorType, className }: ActorLabelProps) {
	const { resolve } = useActorDirectory();
	const name = resolve(actorId);
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
