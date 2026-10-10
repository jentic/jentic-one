/**
 * Fleet-aware avatar initials: distinct letters for the near-identical names a
 * real fleet grows (`my-agent-34` → M34, `my-agent-34-staging` → M34S,
 * `support-triage` vs `support-triage-eu` → ST vs STE).
 *
 * Each name yields a ranked list of candidates. Names whose first candidate
 * clashes form a group, and the group moves down the ranks together until every
 * member reads differently (else each takes its best candidate still free).
 * Unique fleet-wide: a group never lands on letters another name already
 * reads. Pure and deterministic: the same fleet always reads the same letters.
 */

/** The longest initials an avatar carries. */
const MAX_INITIALS = 4;

interface Named {
	id: string;
	name: string;
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

/**
 * Caps every candidate's length. Repeats stay: ranks line up across a clash
 * group, so `support-triage`'s rank-1 `ST` (no third word) stands against
 * `support-triage-eu`'s `STE`.
 */
function capped(list: string[]): string[] {
	return list.map((c) => c.slice(0, MAX_INITIALS)).filter(Boolean);
}

/** Initials for every agent in the fleet, keyed by id (upper case). */
export function smartInitials(fleet: readonly Named[]): Map<string, string> {
	const picks = new Map<string, string>();
	const cand = new Map(fleet.map((a) => [a.id, initialsCandidates(a.name)]));
	const groups = new Map<string, Named[]>();
	for (const a of fleet) {
		const first = cand.get(a.id)?.[0];
		if (!first) continue;
		const group = groups.get(first);
		if (group) group.push(a);
		else groups.set(first, [a]);
	}
	// Every pick so far, fleet-wide. A name alone on its first candidate keeps
	// it, so those are taken before any clash group chooses.
	const taken = new Set<string>();
	for (const [first, group] of groups) {
		if (group.length !== 1) continue;
		picks.set(group[0].id, first.toUpperCase());
		taken.add(first);
	}
	for (const group of groups.values()) {
		if (group.length === 1) continue;
		const lists = group.map((a) => cand.get(a.id) ?? []);
		const deepest = Math.max(...lists.map((l) => l.length));
		let picked: string[] | null = null;
		// The first rank at which every member of the clash group reads apart —
		// and apart from every pick elsewhere in the fleet.
		for (let rank = 1; rank < deepest && !picked; rank++) {
			const picks = lists.map((l) => l[Math.min(rank, l.length - 1)]);
			if (new Set(picks).size === group.length && !picks.some((p) => taken.has(p))) {
				picked = picks;
			}
		}
		const final = picked ?? greedyPicks(lists, taken);
		final.forEach((pick) => taken.add(pick));
		group.forEach((a, i) => picks.set(a.id, final[i].toUpperCase()));
	}
	// In fleet order, whatever order the groups chose in.
	const out = new Map<string, string>();
	for (const a of fleet) {
		const pick = picks.get(a.id);
		if (pick) out.set(a.id, pick);
	}
	return out;
}

/**
 * No single rank separates the group: each member, in fleet order, takes its
 * best candidate nobody has taken; one with none left numbers its first
 * (`M342`), counting on until the result is free fleet-wide.
 */
function greedyPicks(lists: string[][], taken: ReadonlySet<string>): string[] {
	const mine = new Set<string>();
	const free = (c: string) => !taken.has(c) && !mine.has(c);
	return lists.map((list, i) => {
		let pick = list.find(free);
		for (let n = i + 1; pick === undefined; n++) {
			const digits = String(n);
			const numbered = list[0].slice(0, Math.max(1, MAX_INITIALS - digits.length)) + digits;
			if (free(numbered)) pick = numbered;
		}
		mine.add(pick);
		return pick;
	});
}
