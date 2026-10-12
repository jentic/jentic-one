/**
 * The agent strip ("pinned + linked stack"): the pinned selection and its
 * notch, the circles linked to what is off-screen right (driven through a
 * controllable IntersectionObserver), the `‹ N` chip, the hover card, the
 * keyboard map and the ⌘K picker.
 */
import { useState } from 'react';
import { page } from 'vitest/browser';
import { MotionConfig } from 'framer-motion';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
	act,
	renderWithProviders,
	screen,
	userEvent,
	waitFor,
	within,
} from '@/__tests__/test-utils';
import { isApplePlatform } from '@/shared/lib/keyboard';
import { smartInitials } from '@/shared/lib/smartInitials';
import { AgentInitialsProvider } from '@/shared/ui';
import type { AgentEntity } from '@/modules/agents/api';
import { AgentStrip, compensatedScrollLeft } from '@/modules/agents/components/flat/AgentStrip';
import { FOLD_MOTION } from '@/modules/agents/components/flat/useStickyFold';

/** Every instance recorded; `fireTabs` hands the rail's one entries for tabs. */
class FakeObserver {
	static instances: FakeObserver[] = [];
	readonly callback: IntersectionObserverCallback;
	readonly options: IntersectionObserverInit | undefined;
	readonly targets = new Set<Element>();
	constructor(callback: IntersectionObserverCallback, options?: IntersectionObserverInit) {
		this.callback = callback;
		this.options = options;
		FakeObserver.instances.push(this);
	}
	observe(el: Element) {
		this.targets.add(el);
	}
	unobserve(el: Element) {
		this.targets.delete(el);
	}
	disconnect() {
		this.targets.clear();
	}
	takeRecords() {
		return [];
	}
	root = null;
	rootMargin = '';
	thresholds = [];
}

type Side = 'in' | 'L' | 'R';

/** The rail's observer: the one rooted at the scroller. */
function railObserver(): FakeObserver {
	const scroller = screen.getByTestId('strip-scroller');
	const live = FakeObserver.instances.filter(
		(o) => o.options?.root === scroller && o.targets.size,
	);
	const obs = live[live.length - 1];
	if (!obs) throw new Error('no rail observer');
	return obs;
}

/** Report sides for the named tabs (by agent id), as the browser would. */
async function fireTabs(sides: Record<string, Side>) {
	const obs = railObserver();
	const entries = Object.entries(sides).map(([id, side]) => {
		const target = [...obs.targets].find((t) => t.getAttribute('data-strip-tab') === id);
		if (!target) throw new Error(`no observed tab ${id}`);
		const left = side === 'L' ? -500 : side === 'R' ? 5000 : 400;
		return {
			target,
			intersectionRatio: side === 'in' ? 1 : 0,
			isIntersecting: side === 'in',
			boundingClientRect: { left, width: 100 } as DOMRectReadOnly,
			rootBounds: { left: 0, right: 1000 } as DOMRectReadOnly,
		} as unknown as IntersectionObserverEntry;
	});
	await act(async () => {
		obs.callback(entries, obs as unknown as IntersectionObserver);
		// The hook batches to one write per animation frame.
		await new Promise((r) => requestAnimationFrame(() => r(null)));
	});
}

const agent = (
	id: string,
	name: string,
	status: AgentEntity['status'] = 'active',
): AgentEntity => ({
	id,
	name,
	description: null,
	status,
	ownerId: null,
	parentAgentId: null,
	denialReason: null,
	createdAt: '2026-01-01T00:00:00Z',
	approvedAt: null,
	attribution: { registeredBy: null, approvedBy: null, deniedBy: null },
	hasApiKey: false,
});

const FLEET: AgentEntity[] = [
	agent('w1', 'waiting-one', 'pending'),
	...[
		'alpha',
		'bravo',
		'charlie',
		'delta',
		'echo',
		'foxtrot',
		'golf',
		'hotel',
		'india',
		'juliet',
	].map((n) => agent(n, n)),
];

const EMPTY = new Map<string, number>();
const API_COUNTS = new Map(FLEET.map((a, i) => [a.id, i]));

function Harness({
	initial = 'alpha',
	onSelect,
	filter = '',
	agents = FLEET,
	incomplete,
}: {
	initial?: string;
	onSelect?: (id: string) => void;
	filter?: string;
	agents?: AgentEntity[];
	incomplete?: boolean;
}) {
	const [selected, setSelected] = useState<string | null>(initial);
	// Mirror production: FlatAgentsSection wraps the strip in the fleet-wide
	// AgentInitialsProvider, which is where the badges read their initials from.
	return (
		<AgentInitialsProvider initials={smartInitials(agents)}>
			<AgentStrip
				agents={agents}
				selectedId={selected}
				onSelect={(id) => {
					onSelect?.(id);
					setSelected(id);
				}}
				setupGaps={EMPTY}
				apiCounts={API_COUNTS}
				filter={filter}
				incomplete={incomplete}
			/>
			<button type="button">elsewhere</button>
		</AgentInitialsProvider>
	);
}

function renderStrip(props: Parameters<typeof Harness>[0] = {}, reduced = false) {
	return renderWithProviders(
		<MotionConfig reducedMotion={reduced ? 'always' : 'never'}>
			<Harness {...props} />
		</MotionConfig>,
	);
}

/**
 * The page's wiring, in small: the picker's open state lifted to the parent,
 * shared with a stand-in for the card's name button (remounted per agent, as
 * the card is).
 */
function LiftedHarness({
	onOpenChange,
	onHeight,
	condensed = false,
}: {
	onOpenChange?: (open: boolean) => void;
	onHeight?: (px: number, condensedPx: number) => void;
	condensed?: boolean;
}) {
	const [selected, setSelected] = useState<string | null>('alpha');
	const [open, setOpen] = useState(false);
	return (
		<>
			<AgentStrip
				agents={FLEET}
				selectedId={selected}
				onSelect={setSelected}
				setupGaps={EMPTY}
				apiCounts={API_COUNTS}
				filter=""
				onHeight={onHeight}
				condensed={condensed}
				pickerOpen={open}
				onPickerOpenChange={(next) => {
					onOpenChange?.(next);
					setOpen(next);
				}}
			/>
			<button
				key={selected}
				type="button"
				data-agent-switcher=""
				data-testid="card-name"
				aria-haspopup="dialog"
				onClick={(e) => {
					e.currentTarget.focus();
					setOpen(true);
				}}
			>
				{selected} — switch agent
			</button>
		</>
	);
}

function renderLifted(props: Parameters<typeof LiftedHarness>[0] = {}) {
	return renderWithProviders(
		<MotionConfig reducedMotion="never">
			<LiftedHarness {...props} />
		</MotionConfig>,
	);
}

const circleNames = () =>
	screen.queryAllByTestId('strip-circle').map((c) => c.getAttribute('data-agent-id'));
const cmdK = isApplePlatform() ? '{Meta>}k{/Meta}' : '{Control>}k{/Control}';

/** The rail at rest: three tabs in the bar, the rest off to the right. */
const AT_REST: Record<string, Side> = {
	w1: 'in',
	bravo: 'in',
	charlie: 'in',
	delta: 'R',
	echo: 'R',
	foxtrot: 'R',
	golf: 'R',
	hotel: 'R',
	india: 'R',
	juliet: 'R',
};

describe('AgentStrip', () => {
	const RealObserver = window.IntersectionObserver;
	beforeEach(async () => {
		// Desktop: five circles and the `‹ N` chip (a phone shows two, no chip).
		await page.viewport(1280, 900);
		FakeObserver.instances = [];
		window.IntersectionObserver = FakeObserver as unknown as typeof IntersectionObserver;
	});
	afterEach(() => {
		window.IntersectionObserver = RealObserver;
		vi.restoreAllMocks();
	});

	it('pins the selected tab out of the scroll list, with the notch under it', () => {
		renderStrip();
		const pin = screen.getByTestId('strip-pin');
		const tab = within(pin).getByRole('tab', { name: /alpha/ });
		expect(tab).toHaveAttribute('aria-selected', 'true');
		// Not duplicated: the rail holds every other agent, never the selection.
		expect(screen.getAllByRole('tab', { name: /alpha/ })).toHaveLength(1);
		// Out of the scroll list: positioned against the rail, not the scroller,
		// and not among the tabs the rail lays out and observes.
		expect(getComputedStyle(pin).position).toBe('absolute');
		expect(pin.offsetParent).toBe(screen.getByTestId('strip-rail'));
		const railTabs = [
			...screen.getByTestId('strip-scroller').querySelectorAll('[data-strip-tab]'),
		];
		expect(railTabs.map((t) => t.getAttribute('data-strip-tab'))).not.toContain('alpha');
		expect(screen.getAllByRole('tab')).toHaveLength(FLEET.length);
		// The notch belongs to the pin, so the rail scrolling never moves it.
		expect(within(pin).getByTestId('strip-notch')).toBeInTheDocument();
		expect(screen.getAllByTestId('strip-notch')).toHaveLength(1);
		// The pinned slot is left out of what the linkage observes.
		expect(tab).not.toHaveAttribute('data-strip-tab');
	});

	it('heads the strip with the fleet count and the waiting count', () => {
		renderStrip();
		const header = screen.getByTestId('strip-header');
		expect(header).toHaveTextContent(/Agents\s*11\s*·\s*1 waiting/);
		expect(within(header).getByText('1 waiting')).toHaveClass('text-warning');
		expect(header).toHaveTextContent(/Switch agent/);
	});

	it('reads every fleet total as a floor while the list is incomplete', async () => {
		const user = userEvent.setup();
		renderStrip({ incomplete: true });
		expect(screen.getByTestId('strip-header')).toHaveTextContent(/Agents\s*11\+/);
		await fireTabs({ w1: 'L', bravo: 'L', charlie: 'L', india: 'in', juliet: 'in' });
		const more = screen.getByTestId('strip-more');
		await waitFor(() =>
			expect(more).toHaveAccessibleName('At least 11 agents — open the agent picker'),
		);
		await user.click(more);
		const picker = await screen.findByRole('dialog', { name: 'Switch agent' });
		expect(picker).toHaveTextContent('At least 11 agents');
	});

	it('links the circles to the next agents off-screen right, before and after a scroll', async () => {
		renderStrip();
		await fireTabs(AT_REST);
		expect(circleNames()).toEqual(['delta', 'echo', 'foxtrot', 'golf', 'hotel']);
		expect(screen.getByTestId('strip-more')).toHaveTextContent('+2');
		expect(screen.getByTestId('strip-more')).toHaveAccessibleName(
			'2 more agents further right — open the agent picker',
		);

		// Scrolling delta + echo into the bar: their circles leave, the next grow in.
		await fireTabs({ w1: 'L', bravo: 'L', delta: 'in', echo: 'in' });
		await waitFor(() =>
			expect(circleNames()).toEqual(['foxtrot', 'golf', 'hotel', 'india', 'juliet']),
		);
		expect(screen.getByTestId('strip-left-chip')).toHaveAccessibleName(
			'2 earlier agents — scroll back',
		);
	});

	it('handles a long jump: tabs never reported again still count as behind the pin', async () => {
		renderStrip();
		await fireTabs(AT_REST);
		// Jump to the end: only the tabs that crossed a threshold report; delta…hotel
		// were "right" and are now behind the pin without a word from the observer.
		await fireTabs({ w1: 'L', bravo: 'L', charlie: 'L', india: 'in', juliet: 'in' });
		await waitFor(() => expect(circleNames()).toEqual([]));
		expect(screen.getByTestId('strip-left-chip')).toHaveAccessibleName(
			'8 earlier agents — scroll back',
		);
	});

	it('turns +N into a search button once nothing is off to the right, at the same width', async () => {
		renderStrip();
		await fireTabs(AT_REST);
		const stack = screen.getByTestId('strip-stack');
		const width = stack.getBoundingClientRect().width;
		await fireTabs({ w1: 'L', bravo: 'L', charlie: 'L', india: 'in', juliet: 'in' });
		const more = screen.getByTestId('strip-more');
		await waitFor(() =>
			expect(more).toHaveAccessibleName('All 11 agents — open the agent picker'),
		);
		expect(more).not.toHaveTextContent(/\+/);
		expect(more.querySelector('svg')).not.toBeNull();
		expect(stack.getBoundingClientRect().width).toBe(width);
	});

	it('selects an agent straight from its circle, moving it into the pin', async () => {
		const user = userEvent.setup();
		const onSelect = vi.fn();
		renderStrip({ onSelect });
		await fireTabs(AT_REST);
		await user.click(screen.getByRole('button', { name: /^echo, 5 APIs — switch to it$/ }));
		expect(onSelect).toHaveBeenCalledWith('echo');
		expect(
			within(screen.getByTestId('strip-pin')).getByRole('tab', { name: /echo/ }),
		).toHaveAttribute('aria-selected', 'true');
	});

	it('pages back with the ‹ N chip', async () => {
		const user = userEvent.setup();
		renderStrip();
		await fireTabs(AT_REST);
		expect(screen.queryByTestId('strip-left-chip')).toBeNull();
		await fireTabs({ w1: 'L', bravo: 'L', delta: 'in' });
		const scroller = screen.getByTestId('strip-scroller');
		const scrollBy = vi.spyOn(scroller, 'scrollBy');
		await user.click(screen.getByTestId('strip-left-chip'));
		expect(scrollBy).toHaveBeenCalledTimes(1);
		const arg = scrollBy.mock.calls[0][0] as ScrollToOptions;
		expect(arg.left).toBeLessThan(0);
		expect(arg.behavior).toBe('smooth');
	});

	it('shows the hover card on keyboard focus', async () => {
		renderStrip();
		act(() => screen.getByRole('tab', { name: /bravo/ }).focus());
		const card = within(await screen.findByRole('tooltip')).getByTestId('agent-hover-card');
		expect(card).toHaveTextContent('bravo');
		expect(card).toHaveTextContent('Active');
		expect(card).toHaveTextContent(/APIs\s*2/);
		expect(card).toHaveTextContent('Click to switch');
		// No native title anywhere on the strip.
		expect(screen.getByTestId('agent-strip').querySelector('[title]')).toBeNull();
	});

	it('moves focus with the arrows and switches on Enter', async () => {
		const user = userEvent.setup();
		const onSelect = vi.fn();
		renderStrip({ onSelect });
		act(() => screen.getByRole('tab', { name: /alpha/ }).focus());
		await user.keyboard('{ArrowRight}');
		const waiting = screen.getByRole('tab', { name: /waiting-one/ });
		expect(waiting).toHaveFocus();
		expect(waiting).toHaveAttribute('tabindex', '0');
		expect(onSelect).not.toHaveBeenCalled();
		await user.keyboard('{End}');
		expect(screen.getByRole('tab', { name: /juliet/ })).toHaveFocus();
		await user.keyboard('{Home}');
		expect(screen.getByRole('tab', { name: /alpha/ })).toHaveFocus();
		await user.keyboard('{ArrowLeft}{Enter}');
		expect(onSelect).toHaveBeenCalledWith('juliet');
		// The switched-to tab, now pinned, keeps focus.
		await waitFor(() =>
			expect(
				within(screen.getByTestId('strip-pin')).getByRole('tab', { name: /juliet/ }),
			).toHaveFocus(),
		);
	});

	it('Enter on the already-selected tab leaves no focus pull for a later switch made elsewhere', async () => {
		const user = userEvent.setup();
		function Outside() {
			const [selected, setSelected] = useState<string | null>('alpha');
			return (
				<AgentInitialsProvider initials={smartInitials(FLEET)}>
					<AgentStrip
						agents={FLEET}
						selectedId={selected}
						onSelect={setSelected}
						setupGaps={EMPTY}
						apiCounts={API_COUNTS}
						filter=""
					/>
					<button type="button" onClick={() => setSelected('echo')}>
						select echo elsewhere
					</button>
				</AgentInitialsProvider>
			);
		}
		renderWithProviders(
			<MotionConfig reducedMotion="never">
				<Outside />
			</MotionConfig>,
		);
		const pinned = screen.getByRole('tab', { name: /alpha/ });
		act(() => pinned.focus());
		// A keyboard press on the tab that is already pinned: nothing to switch.
		await user.keyboard('{Enter}');
		expect(pinned).toHaveFocus();
		await user.keyboard('{Tab}');
		const outside = screen.getByRole('button', { name: 'select echo elsewhere' });
		await user.click(outside);
		await waitFor(() =>
			expect(
				within(screen.getByTestId('strip-pin')).getByRole('tab', { name: /echo/ }),
			).toBeInTheDocument(),
		);
		// Two frames: the pin's refocus (and its retry) would have run by now.
		await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
		expect(outside).toHaveFocus();
	});

	it('opens the picker with ⌘K, filters, selects by keyboard — and leaves / alone', async () => {
		const user = userEvent.setup();
		const onSelect = vi.fn();
		renderStrip({ onSelect });

		await user.keyboard('/');
		expect(screen.queryByRole('dialog', { name: 'Switch agent' })).toBeNull();

		await user.keyboard(cmdK);
		const dialog = await screen.findByRole('dialog', { name: 'Switch agent' });
		const field = within(dialog).getByRole('combobox', { name: 'Find an agent' });
		await waitFor(() => expect(field).toHaveFocus());
		expect(
			within(dialog).getByRole('group', { name: 'Waiting for approval' }),
		).toBeInTheDocument();
		expect(within(dialog).getAllByRole('option')).toHaveLength(FLEET.length);

		await user.type(field, 'o');
		// alpha (current) has no "o"; the first match leads.
		const options = within(dialog).getAllByRole('option');
		expect(options.map((o) => o.textContent)).toEqual(
			expect.arrayContaining([
				expect.stringContaining('waiting-one'),
				expect.stringContaining('bravo'),
			]),
		);
		expect(options.some((o) => o.textContent?.includes('alpha'))).toBe(false);
		await user.clear(field);
		await user.type(field, 'gol');
		expect(within(dialog).getAllByRole('option')).toHaveLength(1);
		expect(field).toHaveAttribute(
			'aria-activedescendant',
			within(dialog).getByRole('option').id,
		);
		await user.keyboard('{Enter}');
		expect(onSelect).toHaveBeenCalledWith('golf');
		await waitFor(() =>
			expect(screen.queryByRole('dialog', { name: 'Switch agent' })).toBeNull(),
		);
	});

	it('closes the picker on Esc and hands focus back to the opener', async () => {
		const user = userEvent.setup();
		renderStrip();
		await fireTabs(AT_REST);
		const more = screen.getByTestId('strip-more');
		await user.click(more);
		const dialog = await screen.findByRole('dialog', { name: 'Switch agent' });
		const field = within(dialog).getByRole('combobox');
		await waitFor(() => expect(field).toHaveFocus());
		await user.keyboard('{ArrowDown}{ArrowDown}');
		expect(field.getAttribute('aria-activedescendant')).toMatch(/charlie/);
		await user.keyboard('{Escape}');
		await waitFor(() =>
			expect(screen.queryByRole('dialog', { name: 'Switch agent' })).toBeNull(),
		);
		await waitFor(() => expect(more).toHaveFocus());
	});

	describe('a parent-owned picker', () => {
		const dialog = () => screen.queryByRole('dialog', { name: 'Switch agent' });

		it('⌘K, +N and the card name all open the same picker', async () => {
			const user = userEvent.setup();
			const onOpenChange = vi.fn();
			renderLifted({ onOpenChange });
			await fireTabs(AT_REST);

			await user.keyboard(cmdK);
			await screen.findByRole('dialog', { name: 'Switch agent' });
			expect(onOpenChange).toHaveBeenLastCalledWith(true);
			// The chord closes what it opened.
			await user.keyboard(cmdK);
			await waitFor(() => expect(dialog()).toBeNull());
			expect(onOpenChange).toHaveBeenLastCalledWith(false);

			await user.click(screen.getByTestId('strip-more'));
			await screen.findByRole('dialog', { name: 'Switch agent' });
			await user.keyboard('{Escape}');
			await waitFor(() => expect(dialog()).toBeNull());

			// The card's name flips the parent's state: the strip's one picker.
			await user.click(screen.getByTestId('card-name'));
			await screen.findByRole('dialog', { name: 'Switch agent' });
			expect(document.querySelectorAll('dialog[open]')).toHaveLength(1);
			// …and ⌘K from there closes that same picker.
			await user.keyboard(cmdK);
			await waitFor(() => expect(dialog()).toBeNull());
		});

		it('hands focus back to the card name on Esc', async () => {
			const user = userEvent.setup();
			renderLifted();
			const name = screen.getByTestId('card-name');
			await user.click(name);
			const picker = await screen.findByRole('dialog', { name: 'Switch agent' });
			await waitFor(() => expect(within(picker).getByRole('combobox')).toHaveFocus());
			await user.keyboard('{Escape}');
			await waitFor(() => expect(dialog()).toBeNull());
			await waitFor(() => expect(name).toHaveFocus());
		});

		it('a pick made from the card name lands focus on the new card name', async () => {
			const user = userEvent.setup();
			renderLifted();
			await user.click(screen.getByTestId('card-name'));
			const picker = await screen.findByRole('dialog', { name: 'Switch agent' });
			const field = within(picker).getByRole('combobox');
			await waitFor(() => expect(field).toHaveFocus());
			await user.type(field, 'gol');
			await user.keyboard('{Enter}');
			await waitFor(() => expect(dialog()).toBeNull());
			// The old name left with its card; the new one takes focus.
			await waitFor(() => expect(screen.getByTestId('card-name')).toHaveFocus());
			expect(screen.getByTestId('card-name')).toHaveTextContent('golf');
		});
	});

	it('reports its measured height, and its height less the header row', async () => {
		const onHeight = vi.fn();
		renderLifted({ onHeight });
		const bar = screen.getByTestId('agent-strip');
		const header = screen.getByTestId('strip-header');
		await waitFor(() => expect(onHeight).toHaveBeenCalled());
		const [full, condensed] = onHeight.mock.calls[onHeight.mock.calls.length - 1];
		expect(full).toBeCloseTo(bar.getBoundingClientRect().height, 3);
		expect(condensed).toBeCloseTo(full - header.getBoundingClientRect().height, 3);
		// The header row is real height, not a rounding error.
		expect(full - condensed).toBeGreaterThan(15);
	});

	it('condensed over a stuck card: rides up by the header row and fades it out', async () => {
		const { rerender } = renderLifted();
		const bar = screen.getByTestId('agent-strip');
		const header = screen.getByTestId('strip-header');
		expect(bar.style.transform).toBe('');
		expect(header.style.opacity).toBe('1');
		rerender(
			<MotionConfig reducedMotion="never">
				<LiftedHarness condensed />
			</MotionConfig>,
		);
		const headerPx = header.getBoundingClientRect().height;
		const lift = () => Number(/^translateY\((-[\d.]+)px\)$/.exec(bar.style.transform)?.[1]);
		await waitFor(() => expect(lift()).toBeCloseTo(-headerPx, 3));
		expect(bar).toHaveAttribute('data-condensed', 'true');
		expect(bar.style.transition).toContain(
			`transform ${FOLD_MOTION.foldMs}ms ${FOLD_MOTION.ease}`,
		);
		expect(header.style.opacity).toBe('0');
		expect(header).toHaveAttribute('aria-hidden', 'true');
		// Its layout height never changes, so the pin line under it holds still.
		expect(bar.getBoundingClientRect().height).toBeGreaterThan(headerPx);
	});

	it('keeps the selection pinned while the filter narrows the rail', () => {
		renderStrip({ filter: 'zzz' });
		expect(
			within(screen.getByTestId('strip-pin')).getByRole('tab', { name: /alpha/ }),
		).toBeInTheDocument();
		expect(screen.getAllByRole('tab')).toHaveLength(1);
		expect(screen.getByText('No agents match your filter.')).toBeInTheDocument();
	});

	it('gives near-identical names distinct initials', () => {
		renderStrip({
			initial: 'a',
			agents: [
				agent('a', 'my-agent-34'),
				agent('b', 'my-agent-34-staging'),
				agent('c', 'support-triage'),
				agent('d', 'support-triage-eu'),
			],
		});
		const letters = (name: string) =>
			within(screen.getByRole('tab', { name: new RegExp(`${name}\\b(?!-)`) })).getByRole(
				'img',
			).textContent;
		expect(letters('my-agent-34')).toBe('M34');
		expect(letters('my-agent-34-staging')).toBe('M34S');
		expect(letters('support-triage')).toBe('ST');
		expect(letters('support-triage-eu')).toBe('STE');
	});

	it('cuts the motion under reduced motion', async () => {
		const user = userEvent.setup();
		renderStrip({}, true);
		await fireTabs(AT_REST);
		await fireTabs({ delta: 'in' });
		// No exit animation: the leaving circle is gone at once.
		expect(circleNames()).toEqual(['echo', 'foxtrot', 'golf', 'hotel', 'india']);
		await fireTabs({ w1: 'L', bravo: 'L' });
		const scroller = screen.getByTestId('strip-scroller');
		const scrollBy = vi.spyOn(scroller, 'scrollBy');
		await user.click(screen.getByTestId('strip-left-chip'));
		expect((scrollBy.mock.calls[0][0] as ScrollToOptions).behavior).toBe('auto');
	});
});

describe('compensatedScrollLeft (a switch keeps the visible tabs still)', () => {
	const base = { scrollLeft: 500, maxScrollLeft: 2000 };

	it('absorbs a tab returning in front of the anchor', () => {
		// The old pin lands before the first visible tab: it moves right 170px.
		expect(compensatedScrollLeft({ ...base, anchorBefore: 700, anchorAfter: 870 })).toBe(670);
	});

	it('absorbs the picked tab leaving from in front of the anchor', () => {
		expect(compensatedScrollLeft({ ...base, anchorBefore: 700, anchorAfter: 530 })).toBe(330);
	});

	it('stays at the start: the returning tab lands in view, not behind the pin', () => {
		expect(
			compensatedScrollLeft({ ...base, scrollLeft: 0, anchorBefore: 200, anchorAfter: 209 }),
		).toBe(0);
	});

	it('clamps to the scroller range and holds without an anchor', () => {
		expect(
			compensatedScrollLeft({
				...base,
				maxScrollLeft: 550,
				anchorBefore: 0,
				anchorAfter: 200,
			}),
		).toBe(550);
		expect(compensatedScrollLeft({ ...base, anchorBefore: 700, anchorAfter: 100 })).toBe(0);
		expect(compensatedScrollLeft({ ...base, anchorBefore: null, anchorAfter: 900 })).toBe(500);
	});
});
