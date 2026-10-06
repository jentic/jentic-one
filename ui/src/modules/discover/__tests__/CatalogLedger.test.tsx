import { describe, expect, it, onTestFinished, vi } from 'vitest';
import { useState } from 'react';
import {
	renderWithProviders,
	screen,
	waitFor,
	within,
	userEvent,
	fireEvent,
	checkA11y,
} from '@/__tests__/test-utils';
import type { DiscoveryEntity } from '@/modules/discover/api';
import { CatalogLedger } from '@/modules/discover/components/CatalogLedger';
import { useDragToAdd } from '@/modules/discover/lib/useDragToAdd';

function api(apiId: string, extra: Partial<DiscoveryEntity> = {}): DiscoveryEntity {
	const sub = apiId.split('/')[1];
	return {
		id: apiId,
		apiId,
		summary: sub ? sub.charAt(0).toUpperCase() + sub.slice(1) : apiId,
		registered: false,
		updateAvailable: false,
		vendor: apiId.split('/')[0],
		version: '1.0.0',
		githubUrl: `https://github.com/jentic/x/${apiId}`,
		...extra,
	};
}

/** The ledger row holding the API named `name` (via its "View …" button). */
function rowFor(name: string) {
	return screen
		.getByRole('button', { name: `View ${name}` })
		.closest<HTMLElement>('[role="row"]')!;
}

const GOOGLE = ['gmail', 'drive', 'calendar', 'sheets', 'youtube', 'people', 'storage'].map((n) =>
	api(`example.org/${n}`),
);
const ENTITIES = [
	api('abstractapi.com'),
	api('box.com'),
	...GOOGLE,
	api('stripe.com', { registered: true }),
	api('zoom.us'),
];

function Harness({
	entities = ENTITIES,
	query = '',
	onImport = () => {},
	onOpen = () => {},
	withDrag = false,
}: {
	entities?: DiscoveryEntity[];
	query?: string;
	onImport?: (e: DiscoveryEntity) => void;
	onOpen?: (e: DiscoveryEntity) => void;
	withDrag?: boolean;
}) {
	const [dropped, setDropped] = useState<string[]>([]);
	const drag = useDragToAdd({
		enabled: withDrag,
		onDrop: (e) => {
			setDropped((d) => [...d, e.id]);
			onImport(e);
		},
	});
	return (
		<div style={{ display: 'flex', gap: 16 }}>
			<div style={{ width: 700 }}>
				<CatalogLedger
					entities={entities}
					loading={false}
					error={null}
					activeId={null}
					onOpen={onOpen}
					onImport={onImport}
					pendingApiIds={new Set()}
					query={query}
					hasNextPage={false}
					isFetchingNextPage={false}
					onLoadMore={() => {}}
					onImportOwn={() => {}}
					drag={withDrag ? drag : undefined}
					announcement={drag.announcement}
				/>
			</div>
			{withDrag && (
				<div
					data-testid="workspace-dock-panel"
					data-drag={drag.drop?.phase ?? 'idle'}
					style={{ width: 300, height: 400 }}
				>
					{drag.drop && (
						<div data-testid="workspace-drop-slot">Drop to add {drag.drop.name}</div>
					)}
					<output data-testid="dropped">{dropped.join(',')}</output>
				</div>
			)}
			{drag.ghostElement}
		</div>
	);
}

/** A keyset feed: each `onLoadMore` reveals the next page (records `bulk`). */
function PagedHarness({
	pages,
	onLoadMore,
}: {
	pages: DiscoveryEntity[][];
	onLoadMore: (bulk: boolean) => void;
}) {
	const [loaded, setLoaded] = useState(1);
	const [fetching, setFetching] = useState(false);
	return (
		<div style={{ width: 700 }}>
			<CatalogLedger
				entities={pages.slice(0, loaded).flat()}
				loading={false}
				error={null}
				activeId={null}
				onOpen={() => {}}
				onImport={() => {}}
				pendingApiIds={new Set()}
				query=""
				hasNextPage={loaded < pages.length}
				isFetchingNextPage={fetching}
				onLoadMore={(options) => {
					onLoadMore(options?.bulk ?? false);
					setFetching(true);
					setTimeout(() => {
						setLoaded((n) => n + 1);
						setFetching(false);
					}, 20);
				}}
				onImportOwn={() => {}}
			/>
		</div>
	);
}

/**
 * The ledger inside a shell-like scroller (`#app-scroll`), with a button that
 * appends an entity (a later commit, like an import landing).
 */
function ScrollerHarness({ entities }: { entities: DiscoveryEntity[] }) {
	const [rows, setRows] = useState(entities);
	return (
		<div id="app-scroll" style={{ height: 400, overflowY: 'auto', width: 760 }}>
			<button
				type="button"
				onClick={() => setRows((r) => [...r, api(`zz${r.length}.example.com`)])}
			>
				Append
			</button>
			<CatalogLedger
				entities={rows}
				loading={false}
				error={null}
				activeId={null}
				onOpen={() => {}}
				onImport={() => {}}
				pendingApiIds={new Set()}
				query=""
				hasNextPage={false}
				isFetchingNextPage={false}
				onLoadMore={() => {}}
				onImportOwn={() => {}}
			/>
		</div>
	);
}

/** A head feed plus rail-jump ranges keyed by start key (`m` → M…). */
function JumpHarness({
	head,
	ranges,
	onHeadLoad,
	onJump,
}: {
	head: DiscoveryEntity[][];
	ranges: Record<string, DiscoveryEntity[]>;
	onHeadLoad: (bulk: boolean) => void;
	onJump: (startKey: string) => void;
}) {
	const [loaded, setLoaded] = useState(1);
	const [starts, setStarts] = useState<string[]>([]);
	const rangeRows = starts
		.slice()
		.reverse()
		.flatMap((k) => ranges[k] ?? []);
	return (
		<div style={{ width: 700 }}>
			<CatalogLedger
				entities={head.slice(0, loaded).flat()}
				loading={false}
				error={null}
				activeId={null}
				onOpen={() => {}}
				onImport={() => {}}
				pendingApiIds={new Set()}
				query=""
				hasNextPage={loaded < head.length}
				isFetchingNextPage={false}
				onLoadMore={(options) => {
					onHeadLoad(options?.bulk ?? false);
					setLoaded((n) => n + 1);
				}}
				onImportOwn={() => {}}
				onJump={(letter) => {
					const k = letter.toLowerCase();
					onJump(k);
					setStarts([k]);
				}}
				jump={
					starts.length
						? {
								startKey: starts[starts.length - 1],
								entities: rangeRows,
								hasNextPage: false,
								isFetchingNextPage: false,
								isPending: false,
								isFetched: true,
								error: null,
								onLoadMore: () => {},
								onLoadEarlier: () =>
									setStarts((s) => [
										...s,
										String.fromCharCode(s[s.length - 1].charCodeAt(0) - 1),
									]),
								isLoadingEarlier: false,
							}
						: undefined
				}
			/>
		</div>
	);
}

describe('CatalogLedger', () => {
	it('groups the workspace first, then letters; big vendors collapse to one row', () => {
		renderWithProviders(<Harness />);
		const rows = screen.getAllByRole('row');
		// Head row, then the "In your workspace" group heading, then its row.
		expect(rows[1]).toHaveTextContent('In your workspace');
		expect(rows[2]).toHaveTextContent('stripe.com');
		const summary = screen.getByTestId('catalog-vendor-summary');
		expect(summary).toHaveTextContent('example.org');
		expect(summary).toHaveTextContent('Calendar, Drive, Gmail, People'); // api_id order, as the server pages;
		expect(summary).toHaveTextContent('+3 more');
		expect(within(summary).getByTestId('catalog-vendor-toggle')).toHaveAttribute(
			'aria-expanded',
			'false',
		);
		expect(screen.queryByRole('button', { name: 'View Youtube' })).not.toBeInTheDocument();
	});

	it('expands a big vendor in place and collapses it back', async () => {
		const user = userEvent.setup();
		renderWithProviders(<Harness />);
		await user.click(screen.getByTestId('catalog-vendor-summary'));
		expect(screen.getByRole('button', { name: 'View Youtube' })).toBeInTheDocument();
		const header = screen.getByTestId('catalog-vendor-row');
		expect(within(header).getByTestId('catalog-vendor-toggle')).toHaveAttribute(
			'aria-expanded',
			'true',
		);
		expect(header).toHaveTextContent('7 APIs');
		await user.click(header);
		expect(screen.queryByRole('button', { name: 'View Youtube' })).not.toBeInTheDocument();
	});

	it('search is a flat list with highlighted matches and no rail', () => {
		renderWithProviders(<Harness entities={GOOGLE.slice(0, 2)} query="exa" />);
		expect(screen.getByTestId('catalog-ledger')).toHaveAttribute('data-mode', 'flat');
		expect(screen.queryByTestId('alpha-rail')).not.toBeInTheDocument();
		expect(screen.queryByTestId('catalog-vendor-row')).not.toBeInTheDocument();
		const gmail = rowFor('Gmail');
		expect(gmail).toHaveTextContent('example.org · v1.0.0');
		expect(gmail.querySelector('mark')).toHaveTextContent('exa');
	});

	it('has no custom keyboard shortcuts — rows are reached with Tab through real buttons', async () => {
		const onOpen = vi.fn();
		const onImport = vi.fn();
		const user = userEvent.setup();
		renderWithProviders(<Harness onOpen={onOpen} onImport={onImport} />);
		// No roving-tabindex rows and no key-hint footer.
		expect(document.querySelector('[data-ledger-row]')).toBeNull();
		expect(screen.queryByText(/move/)).not.toBeInTheDocument();
		const abstract = screen.getByRole('button', { name: 'View abstractapi.com' });
		abstract.focus();
		await user.keyboard('{ArrowDown}');
		expect(abstract).toHaveFocus();
		await user.keyboard('a');
		expect(onImport).not.toHaveBeenCalled();
		// Enter/Space are native button activation.
		await user.keyboard('{Enter}');
		expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({ id: 'abstractapi.com' }));
		// The row's actions follow in the Tab order.
		await user.tab();
		expect(within(rowFor('abstractapi.com')).getByTestId('catalog-row-github')).toHaveFocus();
		await user.tab();
		expect(within(rowFor('abstractapi.com')).getByTestId('catalog-row-add')).toHaveFocus();
		await user.keyboard(' ');
		expect(onImport).toHaveBeenCalledTimes(1);
		// The vendor summary's Show all is tabbable too.
		expect(
			within(screen.getByTestId('catalog-vendor-summary')).getByTestId(
				'catalog-vendor-toggle',
			),
		).not.toHaveAttribute('tabindex', '-1');
	});

	it('draws the vendor tree below the vendor avatar and joins the children', () => {
		const pair = ['events', 'connect', 'vaults'].map((n) => api(`1password.com/${n}`));
		renderWithProviders(<Harness entities={[api('acme.com'), ...pair]} />);
		const header = screen.getByTestId('catalog-vendor-row');
		// The stub starts 3px under the 24px avatar (row 38px → avatar ends at 31px).
		expect(header.querySelector('[role="cell"]')!.className).toContain('after:top-[34px]');
		const cells = ['Connect', 'Events', 'Vaults'].map((n) =>
			rowFor(n).querySelector('[role="cell"]')!,
		);
		for (const cell of cells) expect(cell.className).toContain('before:top-0');
		// Every child but the last carries the line on to the next.
		expect(cells.map((c) => c.className.includes('after:inset-y-0'))).toEqual([
			true,
			true,
			false,
		]);
		// Geometry (real browser): the header's stub starts below its avatar,
		// and each child's elbow ends at its own avatar's vertical centre.
		const px = (el: Element, pseudo: string, prop: string) =>
			parseFloat(getComputedStyle(el, pseudo).getPropertyValue(prop));
		const headerCell = header.querySelector('[role="cell"]')!;
		const headerAvatar = headerCell.firstElementChild!.getBoundingClientRect();
		const stubTop = headerCell.getBoundingClientRect().top + px(headerCell, '::after', 'top');
		expect(stubTop).toBeGreaterThan(headerAvatar.bottom);
		const first = cells[0];
		const firstBox = first.getBoundingClientRect();
		const childAvatar = first.querySelector('button')!.parentElement!.previousElementSibling!;
		const elbowY = firstBox.top + px(first, '::before', 'height');
		const centre = childAvatar.getBoundingClientRect();
		expect(Math.abs(elbowY - (centre.top + centre.height / 2))).toBeLessThanOrEqual(1);
		// Continuous: the first child's row starts where the header's ends.
		expect(firstBox.top).toBeCloseTo(header.getBoundingClientRect().bottom, 0);
	});

	it('the A–Z rail jumps by vendor and describes each letter', () => {
		// The jump scrolls the shell scroller (the window here) — never
		// `scrollIntoView`, which also scrolls the shell's clipped frame.
		const scrollTo = vi.spyOn(window, 'scrollTo').mockImplementation(() => {});
		const scrollIntoView = vi.spyOn(Element.prototype, 'scrollIntoView');
		onTestFinished(() => {
			scrollTo.mockRestore();
			scrollIntoView.mockRestore();
		});
		renderWithProviders(<Harness />);
		const rail = screen.getByRole('navigation', { name: 'Jump to letter' });
		// # (0–9 & symbols) is the LAST rail entry, after Z.
		const letters = [...rail.querySelectorAll('[data-letter]')].map((el) =>
			el.getAttribute('data-letter'),
		);
		expect(letters.slice(-2)).toEqual(['Z', '#']);
		const g = within(rail).getByRole('button', { name: /^E — / });
		expect(g).toHaveAccessibleName(expect.stringContaining('example.org'));
		fireEvent.click(g);
		expect(scrollTo).toHaveBeenCalled();
		expect(scrollIntoView).not.toHaveBeenCalled();
		expect(g).toHaveAttribute('aria-current', 'true');
	});

	it('the rail is one tab stop with arrow keys, 24px targets, and a jump lands focus on the heading', async () => {
		const scrollTo = vi.spyOn(window, 'scrollTo').mockImplementation(() => {});
		onTestFinished(() => scrollTo.mockRestore());
		const user = userEvent.setup();
		renderWithProviders(<Harness />);
		const rail = screen.getByRole('navigation', { name: 'Jump to letter' });
		const buttons = within(rail).getAllByRole('button');
		// One tab stop for the whole rail.
		expect(buttons.filter((b) => b.tabIndex === 0)).toHaveLength(1);
		for (const b of buttons) {
			const r = b.getBoundingClientRect();
			expect(r.width).toBeGreaterThanOrEqual(24);
			expect(r.height).toBeGreaterThanOrEqual(24);
		}
		const first = buttons.find((b) => b.tabIndex === 0)!;
		first.focus();
		await user.keyboard('{ArrowDown}');
		expect(document.activeElement).toBe(buttons[buttons.indexOf(first) + 1]);
		await user.keyboard('{End}');
		expect(document.activeElement).toBe(buttons[buttons.length - 1]);
		// Tab leaves the rail in one step.
		expect(buttons.filter((b) => b.tabIndex === 0)).toEqual([buttons[buttons.length - 1]]);

		// A jump moves focus to the letter's heading.
		await user.click(within(rail).getByRole('button', { name: /^B — / }));
		expect(document.activeElement).toBe(document.getElementById('catalog-letter-B'));
	});

	it('drags a row onto the workspace panel to add it', async () => {
		const onImport = vi.fn();
		renderWithProviders(<Harness withDrag onImport={onImport} />);
		const row = rowFor('box.com');
		const dock = screen.getByTestId('workspace-dock-panel');
		const r = row.getBoundingClientRect();
		const d = dock.getBoundingClientRect();

		fireEvent.pointerDown(row, {
			button: 0,
			pointerType: 'mouse',
			clientX: r.left + 40,
			clientY: r.top + 10,
		});
		// Under the threshold: no drag yet.
		fireEvent.pointerMove(window, { clientX: r.left + 43, clientY: r.top + 11 });
		expect(screen.queryByTestId('drag-ghost')).not.toBeInTheDocument();
		fireEvent.pointerMove(window, { clientX: r.left + 80, clientY: r.top + 30 });
		expect(await screen.findByTestId('drag-ghost')).toHaveTextContent('box.com');
		expect(dock).toHaveAttribute('data-drag', 'dragging');
		fireEvent.pointerMove(window, { clientX: d.left + 50, clientY: d.top + 50 });
		await waitFor(() => expect(dock).toHaveAttribute('data-drag', 'over'));
		expect(screen.getByTestId('workspace-drop-slot')).toHaveTextContent('Drop to add box.com');
		fireEvent.pointerUp(window, { clientX: d.left + 50, clientY: d.top + 50 });

		expect(onImport).toHaveBeenCalledWith(expect.objectContaining({ id: 'box.com' }));
		expect(await screen.findByTestId('catalog-announcer')).toHaveTextContent(
			'Adding box.com to your workspace',
		);
	});

	it('a drop outside the panel cancels; touch never drags', async () => {
		const onImport = vi.fn();
		renderWithProviders(<Harness withDrag onImport={onImport} />);
		const row = rowFor('box.com');
		const r = row.getBoundingClientRect();
		fireEvent.pointerDown(row, {
			button: 0,
			pointerType: 'mouse',
			clientX: r.left + 40,
			clientY: r.top + 10,
		});
		fireEvent.pointerMove(window, { clientX: r.left + 90, clientY: r.top + 40 });
		fireEvent.pointerUp(window, { clientX: r.left + 90, clientY: r.top + 40 });
		await waitFor(() => expect(screen.queryByTestId('drag-ghost')).not.toBeInTheDocument());

		fireEvent.pointerDown(row, {
			button: 0,
			pointerType: 'touch',
			clientX: r.left + 40,
			clientY: r.top + 10,
		});
		fireEvent.pointerMove(window, { clientX: r.left + 90, clientY: r.top + 40 });
		expect(screen.queryByTestId('drag-ghost')).not.toBeInTheDocument();
		expect(onImport).not.toHaveBeenCalled();
	});

	it('jumping to an unloaded letter pages forward (bulk) with a loading chip, then scrolls', async () => {
		// The jump scrolls the shell scroller (the window here) — never
		// `scrollIntoView`, which also scrolls the shell's clipped frame.
		const scrollTo = vi.spyOn(window, 'scrollTo').mockImplementation(() => {});
		const scrollIntoView = vi.spyOn(Element.prototype, 'scrollIntoView');
		onTestFinished(() => {
			scrollTo.mockRestore();
			scrollIntoView.mockRestore();
		});
		const loads: boolean[] = [];
		const pages = [
			[api('100hires.com'), api('abc.com'), api('acme.com')],
			[api('box.com'), api('cat.com')],
			[api('mapbox.com'), api('nyt.com')],
			[api('zoo.us'), api('{x}.example.com')],
		];
		renderWithProviders(<PagedHarness pages={pages} onLoadMore={(b) => loads.push(b)} />);
		const rail = screen.getByRole('navigation', { name: 'Jump to letter' });
		fireEvent.click(within(rail).getByRole('button', { name: /^M — / }));
		expect(await screen.findByTestId('catalog-seek-status')).toHaveTextContent(
			'Loading APIs under M…',
		);
		await waitFor(() => expect(document.getElementById('catalog-letter-M')).not.toBeNull());
		await waitFor(() => expect(screen.queryByTestId('catalog-seek-status')).toBeNull());
		// Two bulk pages reach M (the sentinel may then page on as usual).
		expect(loads.slice(0, 2)).toEqual([true, true]);
		expect(scrollTo).toHaveBeenCalled();
		expect(scrollIntoView).not.toHaveBeenCalled();
		// Earlier sections were not re-ordered while paging: A, B, C, M.
		const headings = [...document.querySelectorAll('[id^="catalog-letter-"]')].map(
			(el) => el.id,
		);
		expect(headings).toEqual([
			'catalog-letter-A',
			'catalog-letter-B',
			'catalog-letter-C',
			'catalog-letter-M',
		]);
		// # (last on the rail) loads the rest; 0–9 & symbols lands after Z.
		fireEvent.click(within(rail).getByRole('button', { name: /^0–9 & symbols — / }));
		await waitFor(() => expect(document.getElementById('catalog-letter-num')).not.toBeNull());
		const all = [...document.querySelectorAll('[id^="catalog-letter-"]')].map((el) => el.id);
		expect(all.slice(-2)).toEqual(['catalog-letter-Z', 'catalog-letter-num']);
	});

	it('jumps by starting a range at the letter (one page), then fills back a letter at a time', async () => {
		const scrollTo = vi.spyOn(window, 'scrollTo').mockImplementation(() => {});
		onTestFinished(() => scrollTo.mockRestore());
		const headLoads: boolean[] = [];
		const jumps: string[] = [];
		renderWithProviders(
			<JumpHarness
				head={[[api('100hires.com'), api('abc.com'), api('acme.com')], [api('box.com')]]}
				ranges={{
					m: [api('mapbox.com'), api('nyt.com')],
					l: [api('lob.com')],
				}}
				onHeadLoad={(b) => headLoads.push(b)}
				onJump={(k) => jumps.push(k)}
			/>,
		);
		const rail = screen.getByRole('navigation', { name: 'Jump to letter' });
		fireEvent.click(within(rail).getByRole('button', { name: /^M — / }));
		await waitFor(() => expect(document.getElementById('catalog-letter-M')).not.toBeNull());
		// One jump, no paging through the head.
		expect(jumps).toEqual(['m']);
		expect(headLoads).toEqual([]);
		expect(screen.getByTestId('catalog-gap')).toHaveTextContent('A–L not loaded yet');
		const headings = () =>
			[...document.querySelectorAll('[id^="catalog-letter-"]')].map((el) => el.id.slice(15));
		// The jumped range reached the end, so # shows (the head's digits).
		expect(headings()).toEqual(['A', 'M', 'N', 'num']);
		// "Load earlier" grows the range back by a letter (L), above M.
		await userEvent.click(screen.getByRole('button', { name: /Load earlier/ }));
		await waitFor(() => expect(headings()).toEqual(['A', 'L', 'M', 'N', 'num']));
		expect(screen.getByTestId('catalog-gap')).toHaveTextContent('A–K not loaded yet');
		expect(screen.getAllByRole('button', { name: 'View mapbox.com' })).toHaveLength(1);
		// The tail is the jumped range (complete), so there's nothing to page.
		expect(screen.queryByTestId('discovery-load-more')).toBeNull();
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderWithProviders(<Harness />);
		await checkA11y(container);
	});

	it('keeps the sr-only announcer inside the list, so the document never scrolls', () => {
		// A shell-like frame: viewport-tall, clipped, with an unpositioned inner
		// scroller holding a long catalog. An absolutely positioned descendant
		// with no positioned ancestor would make the document scroll.
		const many = Array.from({ length: 120 }, (_, i) =>
			api(`v${String(i).padStart(3, '0')}.example.com`),
		);
		renderWithProviders(
			<div style={{ height: '100dvh', overflow: 'hidden' }}>
				<div style={{ height: '100%', overflowY: 'auto' }}>
					<Harness entities={many} />
				</div>
			</div>,
		);
		const announcer = screen.getByTestId('catalog-announcer');
		expect(getComputedStyle(announcer).position).toBe('absolute');
		const doc = document.scrollingElement!;
		expect(doc.scrollHeight).toBeLessThanOrEqual(window.innerHeight + 1);
	});

	describe('a rail landing never pulls the reader back later', () => {
		const LETTERS = 'abcdefghijklmnopqrstuvwxyz'.split('');
		const lettered = LETTERS.flatMap((l) => [1, 2, 3, 4].map((i) => api(`${l}${l}${i}.com`)));

		function jumpToC() {
			// Reduced motion: the landing is an instant scroll, so the test runs
			// well inside the landing window.
			const real = window.matchMedia;
			window.matchMedia = ((q: string) =>
				q.includes('prefers-reduced-motion')
					? ({ matches: true } as MediaQueryList)
					: real.call(window, q)) as typeof window.matchMedia;
			onTestFinished(() => {
				window.matchMedia = real;
			});
			renderWithProviders(<ScrollerHarness entities={lettered} />);
			const scroller = document.getElementById('app-scroll')!;
			const rail = screen.getByRole('navigation', { name: 'Jump to letter' });
			fireEvent.click(within(rail).getByRole('button', { name: /^C — / }));
			expect(scroller.scrollTop).toBeGreaterThan(0);
			return scroller;
		}

		it('stops re-pinning once the reader scrolls (wheel)', async () => {
			const scroller = jumpToC();
			const landed = scroller.scrollTop;
			// The reader scrolls on by hand, further down the list.
			scroller.dispatchEvent(new WheelEvent('wheel', { deltaY: 600, bubbles: true }));
			scroller.scrollTop = landed + 600;
			const read = scroller.scrollTop;
			// A later commit (an import landing) must leave the position alone.
			fireEvent.click(screen.getByRole('button', { name: 'Append' }));
			await new Promise((r) => requestAnimationFrame(() => r(null)));
			expect(scroller.scrollTop).toBe(read);
		});

		it('stops re-pinning once the reader scrolls (a scroll key)', async () => {
			const scroller = jumpToC();
			fireEvent.keyDown(document.body, { key: 'PageDown' });
			scroller.scrollTop += 400;
			const read = scroller.scrollTop;
			fireEvent.click(screen.getByRole('button', { name: 'Append' }));
			await new Promise((r) => requestAnimationFrame(() => r(null)));
			expect(scroller.scrollTop).toBe(read);
		});

		it('stops re-pinning a second after landing, even with no input', async () => {
			const scroller = jumpToC();
			await new Promise((r) => setTimeout(r, 1100));
			scroller.scrollTop += 500;
			const read = scroller.scrollTop;
			fireEvent.click(screen.getByRole('button', { name: 'Append' }));
			await new Promise((r) => requestAnimationFrame(() => r(null)));
			expect(scroller.scrollTop).toBe(read);
		});
	});
});
