/**
 * Fleet-aware avatar initials: distinct letters for the near-identical names a
 * real fleet grows (`my-agent-34` → M34, `my-agent-34-staging` → M34S,
 * `support-triage` vs `support-triage-eu` → ST and STE, the older keeping ST).
 *
 * Each name yields a ranked list of candidates. Agents are served first come,
 * first served by creation date (ties broken by id): each takes the first of its
 * own candidates no older agent holds. One with none left numbers its first
 * (`M342`, `M343`, …), counting past every pick made and past the first
 * candidate of every name in the fleet, so a numbered fallback never spells
 * another agent's own letters (`my-agent-342` keeps M342).
 *
 * Avatar letters are an identity cue, so an older agent's pick never depends on
 * a newer one: adding an agent never changes the letters of any agent already
 * there, nor does reordering the list it arrives in. The one exception is a
 * numbered fallback, which steps past a newer agent whose own letters it would
 * otherwise spell. Removing an agent can let a younger one take a better
 * candidate it frees. Pure and deterministic: the same fleet always reads the
 * same letters.
 *
 * `tieredInitials` serves whole tiers in turn — agents, then the users drawn
 * beside them on a chart — so every avatar on one surface reads apart while
 * an agent's letters never depend on a user.
 */

/** The longest initials an avatar carries. */
const MAX_INITIALS = 4;

/** An actor the initials are read for. */
export interface Named {
	id: string;
	name: string;
	/** ISO-8601 creation time: decides who keeps contested letters. An absent
	 *  or unreadable time is served after every agent that has one. */
	createdAt: string;
}

/** Code points, so a name's first "letter" is never half an emoji. */
const chars = (s: string): string[] => Array.from(s);
const take = (s: string, n: number): string => chars(s).slice(0, n).join('');
const first = (s: string): string => chars(s)[0] ?? '';

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
		const tail = after.map(first).join('');
		const tail2 = after.map((x) => take(x, 2)).join('');
		const lead = digitAt > 0 ? first(w[0]) : (w[0].match(/[a-z]/)?.[0] ?? '');
		const lead2 = digitAt > 1 ? w.slice(0, 2).map(first).join('') : lead;
		return capped([lead + num + tail, lead + num + tail2, lead2 + num + tail]);
	}
	const [a, b = '', c = ''] = w;
	if (!b) return capped([take(a, 2), take(a, 3), take(a, 4)]);
	return capped([
		first(a) + first(b),
		first(a) + first(b) + first(c),
		take(a, 2) + first(b),
		first(a) + take(b, 2),
		take(a, 3) + first(b),
		first(a) + take(b, 3),
	]);
}

/** Caps every candidate's length. */
function capped(list: string[]): string[] {
	return list.map((c) => take(c, MAX_INITIALS)).filter(Boolean);
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
	return tieredInitials([fleet]);
}

/**
 * Initials across tiers served in turn: every actor of the first tier picks
 * (first come, first served) before any of the second, and so on, so a tier's
 * letters are exactly what {@link smartInitials} gives it alone, and each later
 * tier reads apart from everything before it. Keyed by id (upper case), in
 * the order given; an id listed twice is served where it first appears.
 */
export function tieredInitials(tiers: readonly (readonly Named[])[]): Map<string, string> {
	const candidates = new Map<string, string[]>();
	// Every name's own first letters, within the tiers served so far: no
	// numbered fallback may spell them. A later tier reserves nothing from an
	// earlier one, so an agent's letters never depend on a user's name.
	const reserved = new Set<string>();
	const taken = new Set<string>();
	const picks = new Map<string, string>();
	for (const tier of tiers) {
		for (const actor of tier) {
			if (candidates.has(actor.id)) continue;
			const list = initialsCandidates(actor.name);
			candidates.set(actor.id, list);
			if (list[0]) reserved.add(list[0]);
		}
		for (const actor of [...tier].sort(byCreation)) {
			if (picks.has(actor.id)) continue;
			const pick = firstFree(candidates.get(actor.id) ?? [], taken, reserved);
			if (pick === undefined) continue;
			taken.add(pick);
			picks.set(actor.id, pick.toUpperCase());
		}
	}
	const out = new Map<string, string>();
	for (const tier of tiers) {
		for (const actor of tier) {
			const pick = picks.get(actor.id);
			if (pick) out.set(actor.id, pick);
		}
	}
	return out;
}

/**
 * The best candidate nobody has taken; with none left, the first candidate
 * numbered (`M342`), counting on until the result is free fleet-wide and is
 * no name's own first candidate (`reserved`). `undefined` only for a name
 * with no candidates at all.
 */
function firstFree(
	list: readonly string[],
	taken: ReadonlySet<string>,
	reserved: ReadonlySet<string>,
): string | undefined {
	if (list.length === 0) return undefined;
	const free = list.find((c) => !taken.has(c));
	if (free !== undefined) return free;
	for (let n = 2; ; n++) {
		const digits = String(n);
		const numbered = take(list[0], Math.max(1, MAX_INITIALS - digits.length)) + digits;
		if (!taken.has(numbered) && !reserved.has(numbered)) return numbered;
	}
}
