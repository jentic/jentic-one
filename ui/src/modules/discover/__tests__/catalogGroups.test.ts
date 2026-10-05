import { describe, expect, it } from 'vitest';
import type { DiscoveryEntity } from '@/modules/discover/api';
import {
	VENDOR_EXPAND_LIMIT,
	buildCatalogLedger,
	formatVendorCount,
	jumpStartKey,
	matchRanges,
	railLetterLabel,
	railLetterOf,
	railLetterReachable,
	seekStatus,
	vendorOf,
	RAIL_LETTERS,
	type LedgerItem,
} from '@/modules/discover/lib/catalogGroups';

function api(apiId: string, extra: Partial<DiscoveryEntity> = {}): DiscoveryEntity {
	const sub = apiId.split('/')[1];
	return {
		id: apiId,
		apiId,
		summary: sub ? sub.charAt(0).toUpperCase() + sub.slice(1) : apiId,
		registered: false,
		updateAvailable: false,
		vendor: apiId.split('/')[0],
		...extra,
	};
}

const kinds = (items: LedgerItem[]) =>
	items.map((i) =>
		i.kind === 'group'
			? `group:${i.label}`
			: i.kind === 'api'
				? `${i.child ? 'child' : 'api'}:${i.entity.id}`
				: i.kind === 'gap'
					? `gap:${i.from}-${i.to}`
					: `${i.kind}:${i.vendor}`,
	);

describe('catalogGroups', () => {
	it('derives the vendor domain from the api_id', () => {
		expect(vendorOf(api('googleapis.com/gmail'))).toBe('googleapis.com');
		expect(vendorOf(api('stripe.com'))).toBe('stripe.com');
	});

	it('files digits/symbols under # and letters under themselves', () => {
		expect(railLetterOf('100hires.com')).toBe('#');
		expect(railLetterOf('{subdomain}.pinpointhq.com')).toBe('#');
		expect(railLetterOf('abstractapi.com')).toBe('A');
		expect(railLetterOf('Mapbox')).toBe('M');
		expect(railLetterOf('zoom.us')).toBe('Z');
	});

	it('puts # last on the rail, after Z', () => {
		expect(RAIL_LETTERS[0]).toBe('A');
		expect(RAIL_LETTERS[RAIL_LETTERS.length - 2]).toBe('Z');
		expect(RAIL_LETTERS[RAIL_LETTERS.length - 1]).toBe('#');
	});

	it('search shows a flat list in server order — no groups, no nesting, no rail', () => {
		const rows = [
			api('googleapis.com/gmail'),
			api('stripe.com', { registered: true }),
			api('googleapis.com/drive'),
		];
		const model = buildCatalogLedger(rows, {
			searching: true,
			hasNextPage: false,
		});
		expect(model.mode).toBe('flat');
		expect(model.rail).toEqual([]);
		expect(kinds(model.items)).toEqual([
			'api:googleapis.com/gmail',
			'api:stripe.com',
			'api:googleapis.com/drive',
		]);
	});

	it('browsing puts "In your workspace" first, then A–Z groups, then 0–9 last', () => {
		const rows = [
			api('abc.com'),
			api('stripe.com', { registered: true }),
			api('1forge.com'),
			api('box.com'),
		];
		const model = buildCatalogLedger(rows, {
			searching: false,
			hasNextPage: false,
		});
		expect(kinds(model.items)).toEqual([
			'group:In your workspace',
			'api:stripe.com',
			'group:A',
			'api:abc.com',
			'group:B',
			'api:box.com',
			'group:0–9 & symbols',
			'api:1forge.com',
		]);
		const ws = model.items[0];
		expect(ws.kind === 'group' && ws.detail).toBe('1 from the catalog');
	});

	it('includes workspace entries fetched separately (not yet paged in)', () => {
		const model = buildCatalogLedger([api('abc.com')], {
			searching: false,
			hasNextPage: true,
			workspaceEntities: [api('zoom.us', { registered: true })],
		});
		expect(kinds(model.items).slice(0, 2)).toEqual(['group:In your workspace', 'api:zoom.us']);
	});

	it(`expands vendors with ≤ ${VENDOR_EXPAND_LIMIT} APIs inline (header + children)`, () => {
		const rows = ['events', 'connect'].map((n) => api(`1password.com/${n}`));
		const model = buildCatalogLedger(rows, {
			searching: false,
			hasNextPage: false,
		});
		expect(kinds(model.items)).toEqual([
			'group:0–9 & symbols',
			'vendor:1password.com',
			// api_id order (the server's)
			'child:1password.com/connect',
			'child:1password.com/events',
		]);
		const header = model.items[1];
		expect(header.kind === 'vendor' && formatVendorCount(header.total)).toBe('2 APIs');
		expect(header.kind === 'vendor' && header.collapsible).toBe(false);
	});

	it(`collapses vendors with > ${VENDOR_EXPAND_LIMIT} APIs into one summary row`, () => {
		const names = ['gmail', 'drive', 'calendar', 'sheets', 'youtube', 'people'];
		const rows = names.map((n) => api(`googleapis.com/${n}`));
		const model = buildCatalogLedger(rows, {
			searching: false,
			hasNextPage: false,
		});
		expect(kinds(model.items)).toEqual(['group:G', 'vendor-summary:googleapis.com']);
		const summary = model.items[1];
		if (summary.kind !== 'vendor-summary') throw new Error('expected a summary row');
		// api_id order (the server's), not insertion order.
		expect(summary.names).toEqual(['Calendar', 'Drive', 'Gmail', 'People']);
		expect(summary.more).toBe(2);
		expect(formatVendorCount(summary.total)).toBe('6 APIs');
	});

	it('expands a big vendor in place, with a collapsible header', () => {
		const rows = ['a', 'b', 'c', 'd', 'e', 'f'].map((n) => api(`googleapis.com/${n}`));
		const model = buildCatalogLedger(rows, {
			searching: false,
			hasNextPage: false,
			expandedVendors: new Set(['vendor:googleapis.com']),
		});
		expect(model.items[1]).toMatchObject({ kind: 'vendor', collapsible: true });
		expect(model.items.filter((i) => i.kind === 'api' && i.child)).toHaveLength(6);
	});

	it('holds back a small vendor that may continue on the next page', () => {
		const rows = [api('abc.com'), ...['a', 'b'].map((n) => api(`zoom.us/${n}`))];
		const model = buildCatalogLedger(rows, {
			searching: false,
			hasNextPage: true,
		});
		// zoom.us is at the frontier: its shape isn't settled, so it waits.
		expect(kinds(model.items)).toEqual(['group:A', 'api:abc.com']);
	});

	it('shows a big frontier vendor as its summary, with an N+ count', () => {
		const rows = ['a', 'b', 'c', 'd', 'e', 'f'].map((n) => api(`zoom.us/${n}`));
		const model = buildCatalogLedger(rows, {
			searching: false,
			hasNextPage: true,
		});
		const summary = model.items.find((i) => i.kind === 'vendor-summary');
		expect(
			summary && summary.kind === 'vendor-summary' && formatVendorCount(summary.total),
		).toBe('6+ APIs');
	});

	it('builds the A–Z rail by vendor, with reachable letters and tooltips', () => {
		const rows = [
			api('abc.com'),
			api('azure.com/vm'),
			api('azure.com/storage'),
			api('box.com'),
		];
		const model = buildCatalogLedger(rows, { searching: false, hasNextPage: false });
		const a = model.rail.find((r) => r.letter === 'A')!;
		expect(model.rail[model.rail.length - 1].letter).toBe('#');
		expect(a.vendors).toEqual(['abc.com', 'azure.com']);
		expect(railLetterReachable(a, false)).toBe(true);
		expect(railLetterLabel(a, false)).toBe('A — 2 vendors · abc.com … azure.com');
		const q = model.rail.find((r) => r.letter === 'Q')!;
		expect(railLetterReachable(q, false)).toBe(false);
		expect(railLetterLabel(q, false)).toBe('Q — no vendors');
	});

	it('offers an unloaded letter while paging, labelled from loaded rows only', () => {
		// cat.io sits at the frontier with 6 loaded APIs (a summary row, still growing).
		const cats = ['a', 'b', 'c', 'd', 'e', 'f'].map((n) => api(`cat.io/${n}`));
		const model = buildCatalogLedger([api('abc.com'), api('acme.com'), ...cats], {
			searching: false,
			hasNextPage: true,
		});
		// Z hasn't arrived: offered (vendors may still come), with no made-up count.
		const z = model.rail.find((r) => r.letter === 'Z')!;
		expect(z.settled).toBe(false);
		expect(railLetterReachable(z, true)).toBe(true);
		expect(railLetterLabel(z, true)).toBe('Z — not loaded yet');
		expect(railLetterReachable(z, false)).toBe(false);
		// A is behind the frontier (C): settled, its count is exact.
		const a = model.rail.find((r) => r.letter === 'A')!;
		expect(a.settled).toBe(true);
		expect(railLetterLabel(a, true)).toBe('A — 2 vendors · abc.com … acme.com');
		// B was passed with nothing under it: settled and empty, not offered.
		const b = model.rail.find((r) => r.letter === 'B')!;
		expect(railLetterReachable(b, true)).toBe(false);
		expect(railLetterLabel(b, true)).toBe('B — no vendors');
		// The frontier letter may continue: a floor.
		const c = model.rail.find((r) => r.letter === 'C')!;
		expect(c.settled).toBe(false);
		expect(railLetterLabel(c, true)).toBe('C — 1+ vendors so far · cat.io');
	});

	describe('paged loading is append-only', () => {
		/**
		 * A catalog in the backend's real browse order — plain codepoint
		 * `api_id` order (Python `sorted`): digits, then Capitalised ids, then
		 * the lowercase run, then `{templated}` hosts.
		 */
		const CATALOG = [
			'0xerr0r.github.io',
			'100hires.com',
			'1password.com/connect',
			'1password.com/events',
			'Eventbrite',
			'Mapbox',
			'abc.com',
			'acme.com/a',
			'acme.com/b',
			'acme.com/c',
			'box.com',
			'cat.com/a',
			'cat.com/b',
			'cat.com/c',
			'cat.com/d',
			'cat.com/e',
			'cat.com/f',
			'cat.com/g',
			'dog.io',
			'eventbrite.com/x',
			'fox.dev',
			'mapbox.com/a',
			'mapbox.com/b',
			'zoo.us',
			'{subdomain}.pinpointhq.com',
		];
		const rows = CATALOG.map((id) => api(id));

		it('the fixture is in the server order', () => {
			const codepoint = [...CATALOG].sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
			expect(CATALOG).toEqual(codepoint);
		});

		function pagedModels(pageSize: number) {
			const out = [];
			for (let n = pageSize; n < rows.length + pageSize; n += pageSize) {
				const loaded = rows.slice(0, n);
				out.push(
					buildCatalogLedger(loaded, {
						searching: false,
						hasNextPage: n < rows.length,
					}),
				);
			}
			return out;
		}

		it.each([2, 3, 4, 5, 7])('page size %i: every page only appends', (size) => {
			const models = pagedModels(size);
			for (let i = 1; i < models.length; i++) {
				const prev = models[i - 1].items.map((it) => it.key);
				const next = models[i].items.map((it) => it.key);
				expect(next.slice(0, prev.length)).toEqual(prev);
			}
		});

		it.each([2, 3, 5])(
			'page size %i: letter sections keep their order and end with #',
			(size) => {
				const models = pagedModels(size);
				const letters = (m: (typeof models)[number]) =>
					m.items.flatMap((it) => (it.kind === 'group' && it.letter ? [it.letter] : []));
				for (let i = 1; i < models.length; i++) {
					const prev = letters(models[i - 1]);
					expect(letters(models[i]).slice(0, prev.length)).toEqual(prev);
				}
				expect(letters(models[models.length - 1])).toEqual([
					'A',
					'B',
					'C',
					'D',
					'E',
					'F',
					'M',
					'Z',
					'#',
				]);
			},
		);

		it('files Capitalised ids case-insensitively, once the feed reaches them', () => {
			// Page 1 holds the digits and `Eventbrite`/`Mapbox`, then abc.com and
			// the start of acme.com (the frontier vendor, held until it settles).
			const page1 = buildCatalogLedger(rows.slice(0, 8), {
				searching: false,
				hasNextPage: true,
			});
			expect(kinds(page1.items)).toEqual(['group:A', 'api:abc.com']);
			// Once the lowercase run passes "eventbrite", it lands under E.
			const pastE = buildCatalogLedger(rows.slice(0, 21), {
				searching: false,
				hasNextPage: true,
			});
			const e = kinds(pastE.items);
			// (fox.dev is the frontier vendor, still held.)
			expect(e.slice(e.indexOf('group:E'))).toEqual([
				'group:E',
				'api:Eventbrite',
				'api:eventbrite.com/x',
			]);
		});

		it('shows digits and {templated} hosts under # only once fully loaded', () => {
			const partial = buildCatalogLedger(rows.slice(0, 24), {
				searching: false,
				hasNextPage: true,
			});
			expect(partial.items.some((i) => i.key === 'letter:#')).toBe(false);
			const full = buildCatalogLedger(rows, {
				searching: false,
				hasNextPage: false,
			});
			const k = kinds(full.items);
			expect(k.slice(k.indexOf('group:0–9 & symbols'))).toEqual([
				'group:0–9 & symbols',
				'api:0xerr0r.github.io',
				'api:100hires.com',
				'vendor:1password.com',
				'child:1password.com/connect',
				'child:1password.com/events',
				'api:{subdomain}.pinpointhq.com',
			]);
		});

		it('a vendor spanning two pages appears once, already in its final shape', () => {
			// acme.com's 3 APIs straddle the page edge at 8.
			const before = buildCatalogLedger(rows.slice(0, 8), {
				searching: false,
				hasNextPage: true,
			});
			expect(before.items.some((i) => 'vendor' in i && i.vendor === 'acme.com')).toBe(false);
			const after = buildCatalogLedger(rows.slice(0, 11), {
				searching: false,
				hasNextPage: true,
			});
			// (box.com is now the frontier vendor, so it waits for the next page.)
			expect(kinds(after.items)).toEqual([
				'group:A',
				'api:abc.com',
				'vendor:acme.com',
				'child:acme.com/a',
				'child:acme.com/b',
				'child:acme.com/c',
			]);
			const last = after.items.filter((i) => i.kind === 'api' && i.child).pop();
			expect(last && last.kind === 'api' && last.lastChild).toBe(true);
		});

		it('a big vendor growing across pages updates its summary in place', () => {
			const at = (n: number) =>
				buildCatalogLedger(rows.slice(0, n), {
					searching: false,
					hasNextPage: true,
				}).items.find((i) => i.kind === 'vendor-summary');
			// cat.com reaches 6 APIs at row 17, 7 at row 18.
			expect(at(17)).toMatchObject({
				key: 'vendor:cat.com',
				total: { count: 6, atLeast: true },
			});
			expect(at(18)).toMatchObject({
				key: 'vendor:cat.com',
				total: { count: 7, atLeast: true },
			});
			expect(at(19)).toMatchObject({
				key: 'vendor:cat.com',
				total: { count: 7, atLeast: false },
			});
		});

		it('seekStatus: load until the letter arrives; passed when skipped', () => {
			const m = buildCatalogLedger(rows.slice(0, 12), {
				searching: false,
				hasNextPage: true,
			});
			expect(seekStatus(m, 'A', true)).toBe('ready');
			expect(seekStatus(m, 'M', true)).toBe('load');
			expect(seekStatus(m, '#', true)).toBe('load');
			const late = buildCatalogLedger(rows.slice(0, 24), {
				searching: false,
				hasNextPage: true,
			});
			// The feed is at Z: K has nothing, so the seek stops.
			expect(seekStatus(late, 'K', true)).toBe('passed');
		});
	});

	it('finds case-insensitive match ranges for highlighting', () => {
		expect(matchRanges('googleapis.com', 'GOO')).toEqual([[0, 3]]);
		expect(matchRanges('abab', 'ab')).toEqual([
			[0, 2],
			[2, 4],
		]);
		expect(matchRanges('x', '')).toEqual([]);
	});

	describe('rail jump ranges', () => {
		const head = [
			api('1fit.com'),
			api('Zeta.io'),
			api('Beta.io'),
			api('acme.com'),
			api('apex.io'),
		];
		const opts = { searching: false, hasNextPage: true };

		it('starts a jump right after the letter (digits / templated hosts for #)', () => {
			expect(jumpStartKey('Y')).toBe('y');
			expect(jumpStartKey('#')).toBe('{');
		});

		it('shows the jumped range after a gap, holding what it cannot place yet', () => {
			const model = buildCatalogLedger(head, {
				...opts,
				jump: {
					entities: [api('yelp.com'), api('yoti.com'), api('zoom.us')],
					startKey: 'y',
					hasNextPage: true,
				},
			});
			expect(kinds(model.items)).toEqual([
				'group:A',
				'api:acme.com',
				'gap:A-X',
				'group:Y',
				'api:yelp.com',
				'api:yoti.com',
				// Covered by the jump's frontier (zoom.us), so it can't be displaced.
				'group:Z',
				'api:Zeta.io',
				// zoom.us may continue on the jump's next page: held back.
			]);
			expect(model.gap).toEqual({ from: 'A', to: 'X' });
			expect(model.jumpMerged).toBe(false);
			expect(model.rail.find((r) => r.letter === 'Y')?.anchorKey).toBe('letter:Y');
			// Beta.io sits in the gap (B), so it waits for the head.
			expect(kinds(model.items)).not.toContain('api:Beta.io');
		});

		it('places a capitalised id inside the jumped range once that range covers it', () => {
			const model = buildCatalogLedger(head, {
				...opts,
				jump: {
					entities: [api('yelp.com'), api('zoom.us')],
					startKey: 'y',
					hasNextPage: false,
				},
			});
			// The jump reached the end: Z is complete (Zeta.io folds in) and #
			// shows the digits the head brought with page 1.
			expect(kinds(model.items).slice(-7)).toEqual([
				'group:Y',
				'api:yelp.com',
				'group:Z',
				'api:Zeta.io',
				'api:zoom.us',
				'group:0–9 & symbols',
				'api:1fit.com',
			]);
		});

		it('merges the ranges (no gap, no duplicates) once the head reaches the jump', () => {
			const model = buildCatalogLedger([...head, api('beta.io'), api('yelp.com')], {
				...opts,
				jump: {
					entities: [api('yelp.com'), api('yoti.com'), api('zoom.us')],
					startKey: 'y',
					hasNextPage: true,
				},
			});
			expect(model.jumpMerged).toBe(true);
			expect(model.gap).toBeNull();
			const ids = kinds(model.items).filter((k) => /^(api|child):/.test(k));
			expect(ids).toEqual([
				'api:acme.com',
				'api:apex.io',
				'child:beta.io',
				'child:Beta.io',
				'api:yelp.com',
				'api:yoti.com',
				'api:Zeta.io',
			]);
		});

		it('jumps to # with the templated hosts at the end of the catalog', () => {
			const model = buildCatalogLedger(head, {
				...opts,
				jump: {
					entities: [api('{tenant}.example.com')],
					startKey: '{',
					hasNextPage: false,
				},
			});
			expect(model.gap).toEqual({ from: 'A', to: 'Z' });
			expect(kinds(model.items).slice(-3)).toEqual([
				'group:0–9 & symbols',
				'api:1fit.com',
				'api:{tenant}.example.com',
			]);
		});
	});
});
