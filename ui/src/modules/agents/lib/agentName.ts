/**
 * The agent name rules, shared by every surface that names an agent (the create
 * form, and the register command on the landing and in the New agent panel):
 * what a valid name is, which name to suggest, and whether a name is already
 * another agent's.
 *
 * The backend accepts duplicate names, but two agents with one name can't be
 * told apart in the fleet — so the UI never suggests a taken name, and flags one
 * the operator types without blocking it.
 */

/** The backend's limit for an agent name (`AgentCreateRequest.name` and
 * `RegisterRequest.client_name` both cap at 255). */
export const AGENT_NAME_MAX_LENGTH = 255;

/** The suggestion for an org that has no agent at all yet. */
export const FIRST_AGENT_NAME = 'my-first-agent';

/** The suggestion for an org that has (or had) agents. */
export const NEXT_AGENT_NAME = 'my-agent';

/** The longest name a plain-text line (a toast, a native tooltip) prints whole. */
const AGENT_NAME_TEXT_BUDGET = 40;

/**
 * `name` cut to `max` characters with an ellipsis, for plain-text copy that
 * can't truncate visually (toast titles). Accessible names keep the full name.
 */
export function clipName(name: string, max: number = AGENT_NAME_TEXT_BUDGET): string {
	const chars = [...name];
	return chars.length <= max
		? name
		: `${chars
				.slice(0, max - 1)
				.join('')
				.trimEnd()}…`;
}

/** Why `name` can't name an agent, or `null` when it can. */
export function agentNameError(name: string): string | null {
	return name.trim() ? null : 'A name is required.';
}

/** Names compare trimmed and case-insensitively: `Bot` and `bot ` read as one. */
function nameKey(name: string): string {
	return name.trim().toLowerCase();
}

/**
 * A default name no agent in `existingNames` has. `existingNames` is the whole
 * roster — archived, denied, pending and disabled agents included, since they
 * are all still listed. `base` defaults to {@link FIRST_AGENT_NAME} for an
 * empty roster and {@link NEXT_AGENT_NAME} otherwise; when it is taken, the
 * first free of `base-2`, `base-3`, … is returned.
 */
export function suggestAgentName(existingNames: Iterable<string>, base?: string): string {
	const taken = new Set<string>();
	for (const name of existingNames) taken.add(nameKey(name));
	const stem = base ?? (taken.size === 0 ? FIRST_AGENT_NAME : NEXT_AGENT_NAME);
	if (!taken.has(nameKey(stem))) return stem;
	for (let n = 2; ; n++) {
		const candidate = `${stem}-${n}`;
		if (!taken.has(nameKey(candidate))) return candidate;
	}
}

/** The existing agent name `name` duplicates, or `null` when it is free (or blank). */
export function duplicateAgentName(existingNames: Iterable<string>, name: string): string | null {
	const key = nameKey(name);
	if (!key) return null;
	for (const existing of existingNames) if (nameKey(existing) === key) return existing.trim();
	return null;
}

/** The hint shown under a name field whose name another agent already has. */
export function duplicateAgentNameHint(existing: string): string {
	return `An agent named ${existing} already exists — pick a different name so you can tell them apart.`;
}
