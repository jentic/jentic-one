/**
 * The agent card's pinned header: it pins under the strip (driven by an
 * IntersectionObserver sentinel), folds its stats into the grabber lip, and
 * the lip's grabber unfolds them over the content.
 */
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
import { commandChordLabel } from '@/shared/lib/keyboard';
import type { AgentEntity } from '@/modules/agents/api';
import { AgentCard } from '@/modules/agents/components/flat/AgentCard';
import { FOLD_MOTION } from '@/modules/agents/components/flat/useStickyFold';

/** A controllable IntersectionObserver: every instance is recorded, and
 * `fire` hands them an entry for the sentinel. */
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

/** Where the sentinel sits against the pin line (0 = the line). */
function fire(position: 'above' | 'straddling' | 'below') {
	const sentinel = screen.getByTestId('agent-card-sentinel');
	const lineTop = 60;
	const bottom =
		position === 'above' ? lineTop - 20 : position === 'straddling' ? lineTop + 4 : 400;
	const entry = {
		target: sentinel,
		isIntersecting: position !== 'above',
		intersectionRatio: position === 'below' ? 1 : position === 'straddling' ? 0.5 : 0,
		boundingClientRect: { top: bottom - 8, bottom } as DOMRectReadOnly,
		rootBounds: { top: lineTop } as DOMRectReadOnly,
		intersectionRect: {} as DOMRectReadOnly,
		time: 0,
	} as IntersectionObserverEntry;
	act(() => {
		for (const obs of FakeObserver.instances) {
			if (obs.targets.has(sentinel)) {
				obs.callback([entry], obs as unknown as IntersectionObserver);
			}
		}
	});
}

const agent = (over: Partial<AgentEntity> = {}): AgentEntity => ({
	id: 'agnt_sticky_1',
	name: 'sticky-agent',
	description: 'Answers support tickets.',
	status: 'active',
	ownerId: null,
	parentAgentId: null,
	denialReason: null,
	createdAt: '2026-10-01T10:00:00Z',
	approvedAt: '2026-10-01T10:00:00Z',
	attribution: { registeredBy: 'self', approvedBy: null, deniedBy: null },
	hasApiKey: true,
	...over,
});

function Stats() {
	return (
		<dl aria-label="sticky-agent stats" data-testid="agent-stat-strip" className="h-16">
			<div>
				<dt>APIs</dt>
				<dd data-testid="stat-apis-value">4</dd>
			</div>
		</dl>
	);
}

function renderCard(
	props: {
		a?: AgentEntity;
		banner?: React.ReactNode;
		reduced?: boolean;
		stickyTop?: number;
		stuckTop?: number;
		onSwitchAgent?: () => void;
		onStuckChange?: (stuck: boolean) => void;
	} = {},
) {
	const card = (a: AgentEntity) => (
		<MotionConfig reducedMotion={props.reduced ? 'always' : 'never'}>
			<AgentCard
				key={a.id}
				agent={a}
				lastActivity={{ at: null }}
				description={a.description}
				banner={props.banner}
				kpis={<Stats />}
				stickyTop={props.stickyTop ?? 60}
				stuckTop={props.stuckTop}
				onSwitchAgent={props.onSwitchAgent}
				onStuckChange={props.onStuckChange}
			/>
		</MotionConfig>
	);
	const view = renderWithProviders(card(props.a ?? agent()));
	return { ...view, switchTo: (a: AgentEntity) => view.rerender(card(a)) };
}

const card = () => screen.getByTestId('agent-card');
const toggle = () => screen.getByRole('button', { name: 'Show agent stats', hidden: true });
/** The fold's content wrapper: what folds, clips and turns. */
const statsBody = () => screen.getByTestId('agent-stats-fold').firstElementChild as HTMLElement;

describe('AgentCard — pinned header', () => {
	beforeEach(() => {
		FakeObserver.instances = [];
		vi.stubGlobal('IntersectionObserver', FakeObserver);
	});
	afterEach(() => {
		vi.unstubAllGlobals();
	});

	it('observes a sentinel against the line under the strip', () => {
		renderCard();
		const obs = FakeObserver.instances.find((o) =>
			o.targets.has(screen.getByTestId('agent-card-sentinel')),
		);
		expect(obs?.options?.rootMargin).toBe('-60px 0px 0px 0px');
		expect(card()).toHaveStyle({ position: 'sticky', top: '60px' });
	});

	it("pins at the strip's measured height and rides up with a condensed strip", () => {
		const onStuckChange = vi.fn();
		// The strip's reported heights (full 88.5, header row shed: 63.5) — never
		// a constant.
		renderCard({ stickyTop: 88.5, stuckTop: 63.5, onStuckChange });
		const obs = FakeObserver.instances.find((o) =>
			o.targets.has(screen.getByTestId('agent-card-sentinel')),
		);
		// The pin line is the full strip (rounded for the root margin)…
		expect(obs?.options?.rootMargin).toBe('-89px 0px 0px 0px');
		expect(card()).toHaveStyle({ top: '88.5px' });
		expect(card().style.transform).toBe('');
		expect(onStuckChange).toHaveBeenLastCalledWith(false);
		fire('above');
		// …and, pinned, the card rides up by the header row the strip sheds, on
		// the fold's curve, so it stays flush under the strip.
		expect(onStuckChange).toHaveBeenLastCalledWith(true);
		expect(card().style.top).toBe('88.5px');
		expect(card().style.transform).toBe('translateY(-25px)');
		expect(card().style.transition).toContain(
			`transform ${FOLD_MOTION.foldMs}ms ${FOLD_MOTION.ease}`,
		);
		fire('below');
		expect(card().style.transform).toBe('');
	});

	describe('the name: the agent switcher', () => {
		const switcher = () => screen.getByTestId('agent-card-switcher');

		it('is a button inside the heading, labelled with the shortcut, opening a dialog', async () => {
			const user = userEvent.setup();
			const onSwitchAgent = vi.fn();
			renderCard({ onSwitchAgent });
			const heading = screen.getByRole('heading', { level: 2 });
			const button = within(heading).getByRole('button', {
				name: `sticky-agent — switch agent (${commandChordLabel('k')})`,
			});
			expect(button).toBe(switcher());
			expect(button).toHaveAttribute('aria-haspopup', 'dialog');
			expect(button.getAttribute('aria-keyshortcuts')).toMatch(/^(Meta|Control)\+K$/);
			// A muted chevron after the name; the name itself still truncates.
			expect(button.querySelector('svg')).toHaveAttribute('aria-hidden', 'true');
			expect(within(button).getByText('sticky-agent')).toHaveClass('truncate');
			await user.click(button);
			expect(onSwitchAgent).toHaveBeenCalledTimes(1);
			// Focused on click, so the picker can hand focus back to it.
			expect(button).toHaveFocus();
		});

		it('reserves its padding, so the ghost tint never moves the name', () => {
			renderCard({ onSwitchAgent: () => {} });
			const s = getComputedStyle(switcher());
			expect(parseFloat(s.paddingLeft)).toBeGreaterThan(0);
			expect(parseFloat(s.marginLeft)).toBe(-parseFloat(s.paddingLeft));
			expect(parseFloat(s.marginTop)).toBe(-parseFloat(s.paddingTop));
			expect(switcher()).toHaveClass('hover:bg-tint-2');
		});

		it('keeps a long name cut, with its tooltip, inside the button', async () => {
			const user = userEvent.setup();
			const long = `agent-${'x'.repeat(200)}`;
			renderCard({ a: agent({ name: long }), onSwitchAgent: () => {} });
			const text = within(switcher()).getByText(long);
			await waitFor(() => expect(text.scrollWidth).toBeGreaterThan(text.clientWidth));
			// Out of the tab order (the button carries the name), hover shows it.
			expect(text).not.toHaveAttribute('tabindex');
			await user.hover(text);
			expect(await screen.findByRole('tooltip')).toHaveTextContent(long);
		});

		it('still opens the picker while the card is pinned', async () => {
			const user = userEvent.setup();
			const onSwitchAgent = vi.fn();
			renderCard({ onSwitchAgent });
			fire('above');
			expect(card()).toHaveAttribute('data-stuck', 'true');
			await user.click(switcher());
			expect(onSwitchAgent).toHaveBeenCalledTimes(1);
		});

		it('is a plain heading without a picker to open', () => {
			renderCard();
			expect(screen.queryByTestId('agent-card-switcher')).toBeNull();
			expect(screen.getByRole('heading', { level: 2, name: 'sticky-agent' })).toBeVisible();
		});
	});

	it('drops the banner from the pinned clearance when it goes in place (approval)', async () => {
		const onPinnedHeight = vi.fn();
		const pending = agent({ status: 'pending' });
		const view = (banner?: React.ReactNode, a: AgentEntity = pending) => (
			<MotionConfig reducedMotion="never">
				<AgentCard
					key={a.id}
					agent={a}
					lastActivity={{ at: null }}
					banner={banner}
					kpis={<Stats />}
					stickyTop={60}
					onPinnedHeight={onPinnedHeight}
				/>
			</MotionConfig>
		);
		const { rerender } = renderWithProviders(
			view(<p style={{ height: 48, margin: 0 }}>Waiting for approval</p>),
		);
		await waitFor(() => expect(onPinnedHeight).toHaveBeenLastCalledWith(expect.any(Number)));
		await waitFor(() => expect(onPinnedHeight.mock.lastCall?.[0]).toBeGreaterThan(48));
		const withBanner = onPinnedHeight.mock.lastCall?.[0] as number;
		// Approved in place: same card (same key), the banner unmounts.
		const cardBefore = card();
		rerender(view(undefined, agent({ status: 'active' })));
		expect(card()).toBe(cardBefore);
		await waitFor(() =>
			expect(onPinnedHeight.mock.lastCall?.[0]).toBeLessThanOrEqual(withBanner - 48),
		);
	});

	it('pins once the sentinel has left the line, with hysteresis', () => {
		renderCard();
		expect(card()).toHaveAttribute('data-stuck', 'false');
		fire('above');
		expect(card()).toHaveAttribute('data-stuck', 'true');
		// Straddling the band keeps the last answer — no flicker on the line.
		fire('straddling');
		expect(card()).toHaveAttribute('data-stuck', 'true');
		fire('below');
		expect(card()).toHaveAttribute('data-stuck', 'false');
		fire('straddling');
		expect(card()).toHaveAttribute('data-stuck', 'false');
	});

	it('folds the stats and the description when pinned; a banner stays', async () => {
		renderCard({ banner: <p>Waiting for approval</p> });
		await waitFor(() => expect(card()).toHaveAttribute('data-stats', 'open'));
		expect(statsBody()).not.toHaveAttribute('aria-hidden');
		fire('above');
		expect(card()).toHaveAttribute('data-stats', 'folded');
		// The target height (inline): the computed one is mid-transition.
		expect(screen.getByTestId('agent-stats-fold').style.height).toBe('20px');
		// Folded: out of the a11y tree and the tab order, but still labelled.
		expect(statsBody()).toHaveAttribute('aria-hidden', 'true');
		expect(statsBody()).toHaveAttribute('inert');
		expect(screen.getByTestId('agent-stat-strip')).toHaveAttribute(
			'aria-label',
			'sticky-agent stats',
		);
		expect(
			screen.getByText('Answers support tickets.').closest('[aria-hidden]'),
		).not.toBeNull();
		expect(screen.getByText('Waiting for approval')).toBeVisible();
	});

	it('the grabber unfolds and folds the stats, announcing its state', async () => {
		const user = userEvent.setup();
		renderCard();
		// Unpinned, the tab is out of reach.
		expect(toggle().closest('[inert]')).not.toBeNull();
		fire('above');
		const tab = screen.getByRole('button', { name: 'Show agent stats' });
		expect(tab).toHaveAttribute('aria-expanded', 'false');
		expect(tab).toHaveAttribute('aria-controls', screen.getByTestId('agent-stats-fold').id);
		// A ≥24px hit target around the 16px-tall handle.
		expect(getComputedStyle(tab, '::after').content).not.toBe('none');
		await user.click(tab);
		expect(tab).toHaveAttribute('aria-expanded', 'true');
		expect(card()).toHaveAttribute('data-stats', 'open');
		expect(statsBody()).not.toHaveAttribute('aria-hidden');
		await user.click(tab);
		expect(tab).toHaveAttribute('aria-expanded', 'false');
		expect(card()).toHaveAttribute('data-stats', 'folded');
	});

	it('Escape folds the unfolded stats', async () => {
		const user = userEvent.setup();
		renderCard();
		fire('above');
		await user.click(toggle());
		expect(card()).toHaveAttribute('data-stats', 'open');
		await user.keyboard('{Escape}');
		expect(toggle()).toHaveAttribute('aria-expanded', 'false');
		expect(card()).toHaveAttribute('data-stats', 'folded');
	});

	it('unpins at the top with the stats back in place, and the next pin starts folded', async () => {
		const user = userEvent.setup();
		renderCard();
		fire('above');
		await user.click(toggle());
		fire('below');
		expect(card()).toHaveAttribute('data-stuck', 'false');
		expect(card()).toHaveAttribute('data-stats', 'open');
		expect(statsBody()).not.toHaveAttribute('aria-hidden');
		fire('above');
		expect(card()).toHaveAttribute('data-stats', 'folded');
		expect(toggle()).toHaveAttribute('aria-expanded', 'false');
	});

	it('resets to unpinned and unfolded on an agent switch', async () => {
		const user = userEvent.setup();
		const { switchTo } = renderCard();
		fire('above');
		await user.click(toggle());
		switchTo(agent({ id: 'agnt_sticky_2', name: 'other-agent' }));
		expect(card()).toHaveAttribute('data-stuck', 'false');
		expect(card()).toHaveAttribute('data-stats', 'open');
		expect(toggle()).toHaveAttribute('aria-expanded', 'false');
	});

	it('folds instantly under reduced motion', () => {
		renderCard({ reduced: true });
		fire('above');
		const fold = screen.getByTestId('agent-stats-fold');
		expect(fold.style.transition).toBe('none');
		expect(fold.style.height).toBe('20px');
		expect(screen.getByTestId('agent-card-spacer').style.transition).toBe('none');
	});

	describe('the folded strip: the grabber lip', () => {
		const strip = () => screen.getByTestId('agent-stats-strip');
		const figures = () => screen.getByTestId('agent-stats-figures');

		it('folds to a 20px lip, with no figure, and the grabber inside it', () => {
			renderCard();
			fire('above');
			const fold = screen.getByTestId('agent-stats-fold');
			expect(fold.style.height).toBe('20px');
			expect(statsBody().style.clipPath).toMatch(
				/^inset\(0(px)? 1px calc\(100% - 20px\) 1px/,
			);
			// No figure and no summary: the figures are transparent and out of
			// the tree, and the strip carries no text.
			expect(figures().style.opacity).toBe('0');
			expect(statsBody()).toHaveAttribute('aria-hidden', 'true');
			expect(strip()).toHaveTextContent('');
			expect(strip().style.opacity).toBe('1');
			expect(strip().style.height).toBe('20px');
			// The control sits inside the strip: its box is the strip's height,
			// at the card's bottom.
			const box = toggle().parentElement as HTMLElement;
			expect(box.style.height).toBe('20px');
			expect(box.style.transform).toBe('translateX(-50%)');
			// The page background held behind the stack and the band under it.
			expect(screen.getByTestId('agent-card-clearance').style.bottom).toBe('-8px');
			expect(screen.getByTestId('agent-card-clearance').style.opacity).toBe('1');
		});

		it('clicking the strip unfolds it into the full panel; Escape folds it', async () => {
			const user = userEvent.setup();
			renderCard();
			fire('above');
			await user.click(strip());
			expect(toggle()).toHaveAttribute('aria-expanded', 'true');
			expect(card()).toHaveAttribute('data-stats', 'open');
			expect(statsBody().style.clipPath).toMatch(/^inset\(0(px)? 0(px)? 0(px)? 0(px)?/);
			expect(strip().style.opacity).toBe('0');
			expect(figures().style.opacity).toBe('1');
			await user.keyboard('{Escape}');
			expect(toggle()).toHaveAttribute('aria-expanded', 'false');
			expect(card()).toHaveAttribute('data-stats', 'folded');
		});

		it('the open panel ends in a footer for the control, taken back out of the gap', async () => {
			const user = userEvent.setup();
			renderCard();
			fire('above');
			expect(card().style.marginBottom).toBe('12px');
			await user.click(toggle());
			expect(statsBody().style.paddingBottom).toBe('20px');
			// The footer comes out of the card's 12px gap, so the rows stay put.
			expect(card().style.marginBottom).toBe('-8px');
		});

		it('the grabber bends into a chevron on hover, and points up when open', async () => {
			const user = userEvent.setup();
			renderCard();
			fire('above');
			expect(toggle()).toHaveAttribute('data-bend', 'flat');
			await user.hover(strip());
			expect(toggle()).toHaveAttribute('data-bend', 'down');
			await user.unhover(strip());
			expect(toggle()).toHaveAttribute('data-bend', 'flat');
			await user.click(toggle());
			expect(toggle()).toHaveAttribute('data-bend', 'up');
		});

		it('folds in softly but promptly: the figures drift and fade as the height collapses', async () => {
			const {
				ease,
				foldMs,
				foldDelayMs,
				contentOutMs,
				contentLiftPx,
				unfoldMs,
				contentInMs,
				contentInDelayMs,
			} = FOLD_MOTION;
			// The tuning's contract: a ~320ms tuck with the content leaving in its
			// first half and no dead wait; a ~300ms unfold, the content following.
			expect(foldMs).toBeGreaterThanOrEqual(300);
			expect(foldMs).toBeLessThanOrEqual(340);
			expect(foldDelayMs).toBeLessThanOrEqual(30);
			expect(contentOutMs).toBeLessThan(foldMs / 2);
			expect(unfoldMs).toBeGreaterThanOrEqual(280);
			expect(unfoldMs).toBeLessThanOrEqual(320);
			expect(contentInDelayMs).toBeGreaterThan(0);
			expect(contentInDelayMs + contentInMs).toBeLessThanOrEqual(unfoldMs);
			const height = `height ${foldMs}ms ${ease}${foldDelayMs > 0 ? ` ${foldDelayMs}ms` : ''}`;
			const user = userEvent.setup();
			renderCard();
			fire('above');
			const fold = screen.getByTestId('agent-stats-fold');
			expect(fold.style.transition).toBe(height);
			// The spacer holding the room moves in step, so the rows never move.
			expect(screen.getByTestId('agent-card-spacer').style.transition).toBe(height);
			expect(figures().style.transform).toBe(`translateY(-${contentLiftPx}px)`);
			expect(figures().style.transition).toBe(
				`opacity ${contentOutMs}ms ${ease}, transform ${contentOutMs}ms ${ease}`,
			);
			// The lip is over the panel from the start, and the tuck shadow comes
			// in over the height's own window.
			expect(strip().style.transition).toBe(`opacity ${contentOutMs}ms ${ease}`);
			expect(strip()).toHaveClass('z-[1]');
			const tuck = screen.getByTestId('agent-stats-tuck-shadow');
			expect(tuck.style.opacity).toBe('1');
			expect(tuck.style.transition).toBe(height.replace('height', 'opacity'));
			// Unfolding: same curve, the height first, the figures after it.
			await user.click(toggle());
			expect(fold.style.transition).toBe(`height ${unfoldMs}ms ${ease}`);
			expect(figures().style.transition).toBe(
				`opacity ${contentInMs}ms ${ease} ${contentInDelayMs}ms, transform ${contentInMs}ms ${ease} ${contentInDelayMs}ms`,
			);
			expect(tuck.style.opacity).toBe('0');
		});

		it('the band behind the card switches with the pin (never half-faded)', () => {
			renderCard();
			const band = screen.getByTestId('agent-card-clearance');
			expect(band.style.opacity).toBe('0');
			expect(band.style.transition).toBe('');
			fire('above');
			expect(band.style.opacity).toBe('1');
			expect(band.style.transition).toBe('');
			// The drop shadow fades, by opacity alone.
			const shadow = screen.getByTestId('agent-card-drop-shadow');
			expect(shadow.style.opacity).toBe('1');
			expect(shadow.style.transition).toMatch(/^opacity /);
		});

		it('sizes the panel in whole pixels, filling the fold', async () => {
			const user = userEvent.setup();
			renderCard();
			fire('above');
			await user.click(toggle());
			const fold = screen.getByTestId('agent-stats-fold');
			const h = parseFloat(fold.style.height);
			expect(Number.isInteger(h)).toBe(true);
			expect(statsBody().style.minHeight).toBe(`${h}px`);
		});

		it('under reduced motion, unfolds instantly', async () => {
			const user = userEvent.setup();
			renderCard({ reduced: true });
			fire('above');
			await user.click(toggle());
			expect(statsBody().style.transition).toBe('none');
			expect(screen.getByTestId('agent-stats-fold').style.transition).toBe('none');
			expect(strip().style.transition).toBe('none');
			expect(figures().style.transition).toBe('none');
		});
	});
});
