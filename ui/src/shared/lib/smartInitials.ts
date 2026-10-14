/**
 * Fleet-aware avatar initials: distinct letters for the near-identical names a
 * real fleet grows (`my-agent-34` → M34, `my-agent-34-staging` → M34S,
 * `support-triage` vs `support-triage-eu` → ST vs STE).
 *
 * Each name yields a ranked list of candidates. Agents are served first come,
 * first served by creation date (ties broken by id): each takes the first of its
 * own candidates no older agent holds, and one with none left numbers its first
 * (`M342`, `M343`, …) until the result is free fleet-wide.
 *
 * Avatar letters are an identity cue, so an older agent's pick never depends on
 * a newer one: adding an agent never changes the letters of any agent already
 * there, nor does reordering the list it arrives in. Removing one can let a
 * younger agent take a better candidate it frees. Pure and deterministic: the
 * same fleet always reads the same letters.
 */

/** The longest initials an avatar carries. */
const MAX_INITIALS = 4;

interface Named {
	id: string;
	name: string;
	/** ISO-8601 creation time: decides who keeps contested letters. An absent
	 *  or unreadable time is served after every agent that has one. */
	createdAt: string;
}

function words(name: string): string[] {
	return name
		.toLowerCase()
		.split(/[-_\s.]+/)
		.filter(Boolean);
}

/** Ranked initials for one name, most compact first (lower case). */
export function initialsCandidates(name: string): string[] {
	const w = words(name);
	if (w.length === 0) return [];
	const digitAt = w.findIndex((x) => /\d/.test(x));
	if (digitAt >= 0) {
		// A number is the identity: the first word's letter, the number, then
		// whatever follows it (M34, M34S, R2).
		const num = w[digitAt].replace(/\D/g, '');
		const after = w.slice(digitAt + 1);
		const tail = after.map((x) => x[0]).join('');
		const tail2 = after.map((x) => x.slice(0, 2)).join('');
		const lead = digitAt > 0 ? w[0][0] : (w[0].match(/[a-z]/)?.[0] ?? '');
		const lead2 =
			digitAt > 1
				? w
						.slice(0, 2)
						.map((x) => x[0])
						.join('')
				: lead;
		return capped([lead + num + tail, lead + num + tail2, lead2 + num + tail]);
	}
	const [a, b = '', c = ''] = w;
	if (!b) return capped([a.slice(0, 2), a.slice(0, 3), a.slice(0, 4)]);
	return capped([
		a[0] + b[0],
		a[0] + b[0] + (c[0] ?? ''),
		a.slice(0, 2) + b[0],
		a[0] + b.slice(0, 2),
		a.slice(0, 3) + b[0],
		a[0] + b.slice(0, 3),
	]);
}

/** Caps every candidate's length. */
function capped(list: string[]): string[] {
	return list.map((c) => c.slice(0, MAX_INITIALS)).filter(Boolean);
}

/** Creation instant in ms; an absent or unreadable time sorts after every real one. */
function createdMs(a: Named): number {
	const ms = Date.parse(a.createdAt);
	return Number.isNaN(ms) ? Number.POSITIVE_INFINITY : ms;
}

/**
 * Oldest first; equal creation times fall back to id, so the order is total.
 * Compared as instants, not strings, so two surfaces reading the same agents
 * through differently formatted timestamps still agree.
 */
function byCreation(a: Named, b: Named): number {
	const at = createdMs(a);
	const bt = createdMs(b);
	if (at !== bt) return at < bt ? -1 : 1;
	if (a.id !== b.id) return a.id < b.id ? -1 : 1;
	return 0;
}

/** Initials for every agent in the fleet, keyed by id (upper case), in fleet order. */
export function smartInitials(fleet: readonly Named[]): Map<string, string> {
	const taken = new Set<string>();
	const picks = new Map<string, string>();
	for (const agent of [...fleet].sort(byCreation)) {
		if (picks.has(agent.id)) continue;
		const pick = firstFree(initialsCandidates(agent.name), taken);
		if (pick === undefined) continue;
		taken.add(pick);
		picks.set(agent.id, pick.toUpperCase());
	}
	const out = new Map<string, string>();
	for (const agent of fleet) {
		const pick = picks.get(agent.id);
		if (pick) out.set(agent.id, pick);
	}
	return out;
}

/**
 * The best candidate nobody has taken; with none left, the first candidate
 * numbered (`M342`), counting on until the result is free fleet-wide.
 * `undefined` only for a name with no candidates at all.
 */
function firstFree(list: readonly string[], taken: ReadonlySet<string>): string | undefined {
	if (list.length === 0) return undefined;
	const free = list.find((c) => !taken.has(c));
	if (free !== undefined) return free;
	for (let n = 2; ; n++) {
		const digits = String(n);
		const numbered = list[0].slice(0, Math.max(1, MAX_INITIALS - digits.length)) + digits;
		if (!taken.has(numbered)) return numbered;
	}
}
