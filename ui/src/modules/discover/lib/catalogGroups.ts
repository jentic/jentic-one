/**
 * catalogGroups — turns the loaded catalog pages into the Library ledger's
 * rows. Pure (no React), so the grouping rules are unit-tested on their own.
 *
 * Rules:
 *   - **Search** (a query is active): a flat list in the server's rank order.
 *     No groups, no vendor nesting, no A–Z rail.
 *   - **Browse**: entries already in your workspace come first, in their own
 *     group. The rest are grouped by vendor (the `api_id` domain) under A–Z
 *     letter headings, then `0–9 & symbols` LAST (matching the rail's `#`).
 *       · 1 API     → a plain row.
 *       · 2–5 APIs  → a vendor header row + indented child rows.
 *       · > 5 APIs  → one collapsed summary row ("vendor · A, B, C, D +N more")
 *                     that expands in place to the header + children.
 *   - **Counts are honest**: a vendor's count is what's loaded so far, shown as
 *     `N+` while it may continue on the next page — unless the placeholder
 *     module knows its total.
 *
 * Paging is APPEND-ONLY. `GET /catalog` browses in plain codepoint `api_id`
 * order (Python `sorted(key=api_id)`): digits, then the few Capitalised ids
 * (`Eventbrite`, `Mapbox`…), then the lowercase a–z run, then `{templated}`
 * hosts. Grouping/lettering is derived from that same key, so a new page
 * only ever adds rows after the ones already shown:
 *   - letters come from the `api_id`'s first character, compared
 *     case-insensitively (never from a display name);
 *   - the lowercase run arrives in order, so its highest key is the feed's
 *     *frontier*; a Capitalised id arrives early (page 1) and is held back
 *     until the frontier passes it, then lands exactly at the bottom;
 *   - digit/symbol ids (`#`) render after Z, and only once the whole feed has
 *     loaded (they sit at both ends of the server order, so showing them
 *     earlier would mean inserting letters above them);
 *   - the vendor at the frontier may continue on the next page: while it
 *     still has ≤ 5 APIs its shape (plain row vs group) isn't known, so it's
 *     held back (≤ 5 rows) until the next page settles it; past 5 it shows
 *     as its summary row and only its count/"+N more" update in place.
 * A big vendor's collapsed/expanded state is the caller's `expandedVendors`
 * set (keyed by the vendor item's `key`).
 */
import type { DiscoveryEntity } from '@/modules/discover/api';
import {
	PLACEHOLDER_LETTER_VENDOR_COUNTS,
	PLACEHOLDER_VENDOR_API_COUNTS,
} from '@/modules/discover/lib/catalogPlaceholders';

/** Vendors with more APIs than this collapse into one summary row. */
export const VENDOR_EXPAND_LIMIT = 5;
/** How many API names a collapsed vendor row previews before "+N more". */
const VENDOR_PREVIEW_NAMES = 4;

/** The A–Z rail's letters, in order — `#` (0–9 & symbols) LAST, as in the list. */
export const RAIL_LETTERS = [...'ABCDEFGHIJKLMNOPQRSTUVWXYZ', '#'] as const;
export type RailLetter = (typeof RAIL_LETTERS)[number];

export interface VendorCount {
	count: number;
	/** `count` is a floor — more of this vendor's APIs may be on later pages. */
	atLeast: boolean;
}

export type LedgerItem =
	| { kind: 'group'; key: string; label: string; detail?: string; letter?: RailLetter }
	| {
			kind: 'api';
			key: string;
			entity: DiscoveryEntity;
			/** The vendor domain this row belongs to. */
			vendor: string;
			/** Nested under a vendor header (indented, tree elbow). */
			child: boolean;
			/** The last child of its vendor (the tree line ends here). */
			lastChild?: boolean;
	  }
	| {
			kind: 'vendor';
			key: string;
			vendor: string;
			total: VendorCount;
			/** Present for big vendors: the header doubles as the collapse toggle. */
			collapsible: boolean;
	  }
	| {
			/** Not-loaded letters between the head and a jumped-to segment. */
			kind: 'gap';
			key: string;
			from: RailLetter;
			to: RailLetter;
	  }
	| {
			kind: 'vendor-summary';
			key: string;
			vendor: string;
			total: VendorCount;
			/** The first few API names, for "A, B, C, D". */
			names: string[];
			/** How many more beyond `names` ("+N more"). */
			more: number;
	  };

export interface RailEntry {
	letter: RailLetter;
	/** Vendors under this letter among the loaded rows, in order. */
	vendors: string[];
	/** Vendors under this letter in the whole catalog (placeholder), if known. */
	catalogVendors: number | null;
	/** The letter heading's item key, when it's loaded. */
	anchorKey: string | null;
}

export interface CatalogLedgerModel {
	mode: 'flat' | 'grouped';
	items: LedgerItem[];
	/** Empty in flat mode (no rail on search). */
	rail: RailEntry[];
	/**
	 * The highest letter the in-order feed has reached (`null` before any
	 * a–z row loads; `#` once everything has loaded) — a seek for a letter
	 * at or before it can stop paging.
	 */
	frontierLetter: RailLetter | null;
	/** Letters not loaded between the head and a jumped-to segment. */
	gap: { from: RailLetter; to: RailLetter } | null;
	/** A jump segment has met the head (page the jump's feed from here on). */
	jumpMerged: boolean;
}

/** The vendor domain of a catalog entry (`googleapis.com/gmail` → `googleapis.com`). */
export function vendorOf(entity: DiscoveryEntity): string {
	const slash = entity.apiId.indexOf('/');
	return slash === -1 ? entity.apiId : entity.apiId.slice(0, slash);
}

/** The rail letter a name/api_id files under (`#` = digit or symbol). */
export function railLetterOf(name: string): RailLetter {
	const first = name.trim().charAt(0).toUpperCase();
	return first >= 'A' && first <= 'Z' ? (first as RailLetter) : '#';
}

/** Heading text for a rail letter group. */
export function letterHeading(letter: RailLetter): string {
	return letter === '#' ? '0–9 & symbols' : letter;
}

/** "9 APIs" / "6+ APIs" / "1 API". */
export function formatVendorCount({ count, atLeast }: VendorCount): string {
	return `${count}${atLeast ? '+' : ''} API${count === 1 && !atLeast ? '' : 's'}`;
}

/** The browse order key: the `api_id`, case-folded. */
const sortKey = (entity: DiscoveryEntity) => entity.apiId.toLowerCase();
/** Ids the server streams in ascending case-folded order (its lowercase run). */
const inOrderRun = (entity: DiscoveryEntity) => {
	const c = entity.apiId.charAt(0);
	return c >= 'a' && c <= 'z';
};

export interface BuildCatalogLedgerOptions {
	/** A search query is active → flat list. */
	searching: boolean;
	/** More keyset pages exist (counts for the last loaded vendor are floors). */
	hasNextPage: boolean;
	/** Big vendors the user expanded in place (vendor item keys, `vendor:<domain>`). */
	expandedVendors?: ReadonlySet<string>;
	/** Per-vendor totals for the whole catalog; defaults to the placeholder table. */
	vendorTotals?: Readonly<Record<string, number>>;
	/** Per-letter vendor totals; defaults to the placeholder table. */
	letterTotals?: Readonly<Record<string, number>>;
	/**
	 * Every catalog entry already in your workspace (fetched on its own), so
	 * the top group is complete before those rows' pages have loaded.
	 */
	workspaceEntities?: readonly DiscoveryEntity[];
	/**
	 * A rail jump's segment: pages fetched forward from `startKey` (the
	 * keyset position just before the letter — see `jumpStartKey`).
	 */
	jump?: JumpSegment;
}

interface JumpSegment {
	entities: readonly DiscoveryEntity[];
	startKey: string;
	hasNextPage: boolean;
}

interface FeedPart {
	rows: readonly DiscoveryEntity[];
	start: string;
	hasMore: boolean;
}

/** The highest case-folded key of the in-order (lowercase) run, or ''. */
export function frontierKeyOf(rows: readonly DiscoveryEntity[]): string {
	let frontier = '';
	for (const e of rows) if (inOrderRun(e) && sortKey(e) > frontier) frontier = sortKey(e);
	return frontier;
}

function dedupe(rows: readonly DiscoveryEntity[]): DiscoveryEntity[] {
	const seen = new Set<string>();
	return rows.filter((e) => (seen.has(e.id) ? false : (seen.add(e.id), true)));
}

/**
 * The keyset position a jump to `letter` starts after: rows sort strictly
 * after it, so `y` starts at the first `y…` id, and `{` at the templated
 * hosts that end the catalog (digits come with the head's first page).
 */
export function jumpStartKey(letter: RailLetter): string {
	return letter === '#' ? '{' : letter.toLowerCase();
}

/**
 * The jump start one letter before `startKey` (to grow a jumped range
 * backward), or null at A — the head feed covers the digits and capitals.
 */
export function previousJumpStartKey(startKey: string): string | null {
	const i = RAIL_LETTERS.indexOf(jumpLetterOf(startKey));
	return i > 0 ? jumpStartKey(RAIL_LETTERS[i - 1]) : null;
}

/** The rail letter a jump segment starting at `startKey` begins at. */
function jumpLetterOf(startKey: string): RailLetter {
	return startKey === '{' ? '#' : railLetterOf(startKey);
}

interface VendorRun {
	vendor: string;
	key: string;
	letter: RailLetter;
	apis: DiscoveryEntity[];
}

export function buildCatalogLedger(
	entities: readonly DiscoveryEntity[],
	{
		searching,
		hasNextPage,
		expandedVendors = new Set(),
		vendorTotals = PLACEHOLDER_VENDOR_API_COUNTS,
		letterTotals = PLACEHOLDER_LETTER_VENDOR_COUNTS,
		workspaceEntities = [],
		jump,
	}: BuildCatalogLedgerOptions,
): CatalogLedgerModel {
	if (searching) {
		return {
			mode: 'flat',
			rail: [],
			frontierLetter: null,
			gap: null,
			jumpMerged: false,
			items: entities.map((entity) => ({
				kind: 'api',
				key: `api:${entity.id}`,
				entity,
				vendor: vendorOf(entity),
				child: false,
			})),
		};
	}

	const items: LedgerItem[] = [];

	// Loaded rows win over the separate fetch (they're the fresher poll).
	const wsById = new Map<string, DiscoveryEntity>();
	for (const e of workspaceEntities) if (e.registered) wsById.set(e.id, e);
	for (const e of entities) if (e.registered) wsById.set(e.id, e);
	const inWorkspace = [...wsById.values()].sort((a, b) => a.summary.localeCompare(b.summary));
	if (inWorkspace.length > 0) {
		items.push({
			kind: 'group',
			key: 'group:workspace',
			label: 'In your workspace',
			detail: `${inWorkspace.length} from the catalog`,
		});
		for (const entity of inWorkspace) {
			items.push({
				kind: 'api',
				key: `api:${entity.id}`,
				entity,
				vendor: vendorOf(entity),
				child: false,
			});
		}
	}

	// The loaded catalog is one or two contiguous ranges of the server order:
	// the head feed (from the start) and, after a rail jump, a segment from
	// the jumped-to letter — until the head reaches it and they merge.
	const headFrontier = frontierKeyOf(entities);
	const merged = !jump || !hasNextPage || headFrontier >= jump.startKey;
	const parts: FeedPart[] = !jump
		? [{ rows: entities, start: '', hasMore: hasNextPage }]
		: merged
			? [
					{
						rows: dedupe([...entities, ...jump.entities]),
						start: '',
						hasMore: hasNextPage && jump.hasNextPage,
					},
				]
			: [
					{ rows: entities, start: '', hasMore: true },
					{ rows: jump.entities, start: jump.startKey, hasMore: jump.hasNextPage },
				];
	const loaded = jump ? dedupe([...entities, ...jump.entities]) : entities;
	const complete = parts.length === 1 && !parts[0].hasMore;
	const byKey = (a: DiscoveryEntity, b: DiscoveryEntity) => {
		const ka = sortKey(a);
		const kb = sortKey(b);
		if (ka !== kb) return ka < kb ? -1 : 1;
		// A case-folded tie: the in-order row is already shown, so the held one follows.
		return Number(!inOrderRun(a)) - Number(!inOrderRun(b));
	};
	// Capitalised ids arrive with page 1, out of order; each lands in the
	// part whose loaded range covers it (else waits) — never above shown rows.
	const held = loaded.filter(
		(e) => !e.registered && !inOrderRun(e) && railLetterOf(e.apiId) !== '#',
	);
	const placed = parts.map((part) => {
		const frontier = frontierKeyOf(part.rows);
		const rows = part.rows.filter(
			(e) => !e.registered && inOrderRun(e) && sortKey(e) >= part.start,
		);
		for (const e of held) {
			const k = sortKey(e);
			if (k >= part.start && (!part.hasMore || (frontier !== '' && k <= frontier)))
				rows.push(e);
		}
		rows.sort(byKey);
		return { ...part, rows, frontier };
	});
	// Digit/symbol ids sit at both ends of the server order: shown (after Z)
	// once the head has passed the digits and the last range reaches the end.
	const last = placed[placed.length - 1];
	const symbolsReady = !last.hasMore && (placed[0].frontier !== '' || !placed[0].hasMore);
	const symbols = symbolsReady
		? loaded
				.filter((e) => !e.registered && railLetterOf(e.apiId) === '#')
				.sort((a, b) => (a.apiId < b.apiId ? -1 : a.apiId > b.apiId ? 1 : 0))
		: [];

	// Consecutive rows of one vendor form a run (the server keeps them together).
	const runKeys = new Set<string>();
	const runsOf = (
		rows: readonly DiscoveryEntity[],
		letterOf: (e: DiscoveryEntity) => RailLetter,
	) => {
		const runs: VendorRun[] = [];
		for (const entity of rows) {
			const vendor = vendorOf(entity);
			const letter = letterOf(entity);
			const prev = runs[runs.length - 1];
			if (
				prev &&
				prev.letter === letter &&
				prev.vendor.toLowerCase() === vendor.toLowerCase()
			) {
				prev.apis.push(entity);
				continue;
			}
			let key = `vendor:${vendor}`;
			for (let n = 2; runKeys.has(key); n++) key = `vendor:${vendor}~${n}`;
			runKeys.add(key);
			runs.push({ vendor, key, letter, apis: [entity] });
		}
		return runs;
	};

	const railVendors = new Map<RailLetter, string[]>();
	const anchors = new Map<RailLetter, string>();
	let currentLetter: RailLetter | null = null;

	const emitRuns = (runs: VendorRun[], growingVendor: string | null) => {
		const lastRun = runs[runs.length - 1];
		for (const run of runs) {
			const { vendor, apis, letter } = run;
			const growing = run === lastRun && growingVendor === vendor.toLowerCase();
			// A placeholder total only helps a vendor still loading (to fold a big
			// one early, with an honest "+N more"); once its rows are all in, the
			// loaded count is the truth — the placeholder table may be stale.
			const known = growing ? vendorTotals[vendor] : undefined;
			const total: VendorCount =
				known != null && known >= apis.length && known > VENDOR_EXPAND_LIMIT
					? { count: known, atLeast: false }
					: { count: apis.length, atLeast: growing };
			// Its shape (plain row / small group / summary) isn't settled until we
			// know whether the next page continues it: hold it back for now — it
			// then appears at the bottom already in its final shape.
			if (total.atLeast && total.count <= VENDOR_EXPAND_LIMIT) continue;

			if (letter !== currentLetter) {
				currentLetter = letter;
				const key = `letter:${letter}`;
				anchors.set(letter, key);
				items.push({ kind: 'group', key, label: letterHeading(letter), letter });
			}
			railVendors.set(letter, [...(railVendors.get(letter) ?? []), vendor]);

			if (apis.length === 1 && total.count === 1) {
				items.push({
					kind: 'api',
					key: `api:${apis[0].id}`,
					entity: apis[0],
					vendor,
					child: false,
				});
				continue;
			}

			const big = total.count > VENDOR_EXPAND_LIMIT;
			if (big && !expandedVendors.has(run.key)) {
				const names = apis.slice(0, VENDOR_PREVIEW_NAMES).map((e) => e.summary);
				items.push({
					kind: 'vendor-summary',
					key: run.key,
					vendor,
					total,
					names,
					more: Math.max(0, total.count - names.length),
				});
				continue;
			}

			items.push({ kind: 'vendor', key: run.key, vendor, total, collapsible: big });
			apis.forEach((entity, i) => {
				items.push({
					kind: 'api',
					key: `api:${entity.id}`,
					entity,
					vendor,
					child: true,
					lastChild: i === apis.length - 1,
				});
			});
		}
	};

	let gap: CatalogLedgerModel['gap'] = null;
	placed.forEach((part, i) => {
		// Only the vendor at a part's frontier can continue on its next page.
		const growingVendor = part.hasMore && part.frontier ? part.frontier.split('/')[0] : null;
		emitRuns(
			runsOf(part.rows, (e) => railLetterOf(e.apiId)),
			growingVendor,
		);
		const next = placed[i + 1];
		if (next) {
			const from = part.frontier ? railLetterOf(part.frontier) : 'A';
			// Up to the letter before the jumped-to one.
			const to =
				RAIL_LETTERS[Math.max(0, RAIL_LETTERS.indexOf(jumpLetterOf(next.start)) - 1)];
			gap = { from, to };
			items.push({ kind: 'gap', key: 'gap', from, to });
		}
	});
	emitRuns(
		runsOf(symbols, () => '#'),
		null,
	);

	const rail: RailEntry[] = RAIL_LETTERS.map((letter) => ({
		letter,
		vendors: railVendors.get(letter) ?? [],
		catalogVendors: letterTotals[letter] ?? null,
		anchorKey: anchors.get(letter) ?? null,
	}));

	return {
		mode: 'grouped',
		items,
		rail,
		frontierLetter: complete ? '#' : headFrontier ? railLetterOf(headFrontier) : null,
		gap,
		jumpMerged: !!jump && merged,
	};
}

/**
 * Whether a rail letter can be jumped to: it has loaded vendors, or (while
 * more pages exist) the catalog says it has vendors that haven't loaded yet.
 */
export function railLetterReachable(entry: RailEntry, hasNextPage: boolean): boolean {
	if (entry.anchorKey) return true;
	return hasNextPage && (entry.catalogVendors ?? 0) > 0;
}

/**
 * Where a seek for `letter` stands: `ready` (its heading is loaded),
 * `passed` (the feed is beyond it but it has no rows — jump to the next
 * loaded letter instead), or `load` (keep paging).
 */
export function seekStatus(
	model: CatalogLedgerModel,
	letter: RailLetter,
	hasNextPage: boolean,
): 'ready' | 'passed' | 'load' {
	if (model.rail.find((r) => r.letter === letter)?.anchorKey) return 'ready';
	if (!hasNextPage) return 'passed';
	const at = model.frontierLetter;
	if (letter !== '#' && at != null && at !== '#' && at > letter) return 'passed';
	return 'load';
}

/** Tooltip/label for a rail letter: "A — 312 vendors · acme.com … azure.com". */
export function railLetterLabel(entry: RailEntry, hasNextPage: boolean): string {
	const heading = letterHeading(entry.letter);
	const loaded = entry.vendors.length;
	const total = entry.catalogVendors ?? (hasNextPage && loaded > 0 ? null : loaded);
	const countText =
		total != null
			? `${total.toLocaleString()} vendor${total === 1 ? '' : 's'}`
			: `${loaded}+ vendors`;
	if (loaded === 0) {
		return hasNextPage && (entry.catalogVendors ?? 0) > 0
			? `${heading} — ${countText} (not loaded yet)`
			: `${heading} — no vendors`;
	}
	const span =
		loaded === 1 ? entry.vendors[0] : `${entry.vendors[0]} … ${entry.vendors[loaded - 1]}`;
	return `${heading} — ${countText} · ${span}`;
}

/** Case-insensitive match ranges of `query` in `text`, for highlighting. */
export function matchRanges(text: string, query: string): [number, number][] {
	const q = query.trim().toLowerCase();
	if (!q) return [];
	const hay = text.toLowerCase();
	const out: [number, number][] = [];
	let from = 0;
	while (from <= hay.length) {
		const at = hay.indexOf(q, from);
		if (at === -1) break;
		out.push([at, at + q.length]);
		from = at + q.length;
	}
	return out;
}
