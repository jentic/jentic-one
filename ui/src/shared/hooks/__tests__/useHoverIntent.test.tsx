import { describe, it, expect, afterEach, vi } from 'vitest';
import { userEvent as browserUser } from 'vitest/browser';
import { useState } from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { HOVER_INTENT, HOVER_INTENT_IGNORE, useHoverIntent } from '@/shared/hooks/useHoverIntent';
import { usePinStack, type PinStack } from '@/shared/hooks/usePinStack';
import { ConfirmDialog } from '@/shared/ui/ConfirmDialog';

const { openDelayMs, closeGraceMs, scrollQuietMs } = HOVER_INTENT;

/** A row as `ApiRow` wires it: open when pinned or previewed by hover. */
function Row({ name, pins }: { name: string; pins: PinStack }) {
	const pinned = pins.isPinned(name);
	const hover = useHoverIntent<HTMLDivElement>({ pinned });
	return (
		<div
			ref={hover.ref}
			data-testid={name}
			data-open={pinned || hover.open}
			data-preview={hover.open}
			data-pinned={pinned}
			{...hover.handlers}
		>
			<button type="button" onClick={() => pins.toggle(name)}>
				{name}
			</button>
			<span {...HOVER_INTENT_IGNORE} data-testid={`${name}-ignore`}>
				<i data-testid={`${name}-ignore-child`}>action</i>
			</span>
		</div>
	);
}

function List({ names }: { names: string[] }) {
	const pins = usePinStack();
	return (
		<div data-testid="scroller" style={{ height: 100, overflowY: 'auto' }}>
			<span data-testid="stack">{pins.keys.join(',')}</span>
			<button type="button" onClick={pins.clear}>
				clear
			</button>
			{names.map((name) => (
				<Row key={name} name={name} pins={pins} />
			))}
		</div>
	);
}

function renderList(names = ['a', 'b']) {
	render(<List names={names} />);
}

const mouse = { pointerType: 'mouse' } as const;
const isOpen = (name: string) => screen.getByTestId(name).dataset.open === 'true';
const advance = (ms: number) => act(() => void vi.advanceTimersByTime(ms));

describe('useHoverIntent', () => {
	afterEach(() => vi.useRealTimers());
	const fake = () => vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'performance'] });

	it('opens only once the pointer has rested for the delay', () => {
		fake();
		renderList();
		fireEvent.pointerEnter(screen.getByTestId('a'), mouse);
		fireEvent.pointerMove(screen.getByTestId('a'), { ...mouse, movementX: 2 });
		advance(openDelayMs - 10);
		expect(isOpen('a')).toBe(false);
		advance(20);
		expect(isOpen('a')).toBe(true);
	});

	it('moving restarts the wait, and passing over opens nothing', () => {
		fake();
		renderList();
		const a = screen.getByTestId('a');
		fireEvent.pointerEnter(a, mouse);
		fireEvent.pointerMove(a, { ...mouse, movementX: 2 });
		advance(openDelayMs - 50);
		fireEvent.pointerMove(a, { ...mouse, movementX: 4 });
		advance(openDelayMs - 50);
		expect(isOpen('a')).toBe(false);
		fireEvent.pointerLeave(a, mouse);
		advance(openDelayMs * 2);
		expect(isOpen('a')).toBe(false);
	});

	it('does not open a row that slides under a still pointer while scrolling', () => {
		fake();
		renderList();
		const scroller = screen.getByTestId('scroller');
		fireEvent.wheel(scroller);
		fireEvent.scroll(scroller);
		// Content moves under the cursor: the browser reports the new row,
		// with no movement of the pointer itself.
		fireEvent.pointerEnter(screen.getByTestId('b'), mouse);
		fireEvent.pointerMove(screen.getByTestId('b'), mouse);
		advance(openDelayMs + scrollQuietMs);
		expect(isOpen('b')).toBe(false);

		// The pointer really moves once the scroll has gone quiet: hover is back.
		fireEvent.pointerMove(screen.getByTestId('b'), { ...mouse, movementY: 3 });
		advance(openDelayMs + 10);
		expect(isOpen('b')).toBe(true);
	});

	it('a scroll cancels an open that is pending, and holds for the quiet window', () => {
		fake();
		renderList();
		const a = screen.getByTestId('a');
		fireEvent.pointerEnter(a, mouse);
		fireEvent.pointerMove(a, { ...mouse, movementX: 2 });
		advance(openDelayMs / 2);
		fireEvent.wheel(screen.getByTestId('scroller'));
		// Moving during the quiet window does not arm it yet.
		fireEvent.pointerMove(a, { ...mouse, movementX: 2 });
		advance(openDelayMs + 10);
		expect(isOpen('a')).toBe(false);
	});

	it('closes after the leave grace, and stays open if the pointer comes back in time', () => {
		fake();
		renderList();
		const a = screen.getByTestId('a');
		fireEvent.pointerEnter(a, mouse);
		fireEvent.pointerMove(a, { ...mouse, movementX: 2 });
		advance(openDelayMs + 10);
		expect(isOpen('a')).toBe(true);

		fireEvent.pointerLeave(a, mouse);
		advance(closeGraceMs - 50);
		fireEvent.pointerEnter(a, mouse);
		advance(closeGraceMs * 2);
		expect(isOpen('a')).toBe(true);

		fireEvent.pointerLeave(a, mouse);
		advance(closeGraceMs - 10);
		expect(isOpen('a')).toBe(true);
		advance(20);
		expect(isOpen('a')).toBe(false);
	});

	it('a click pins at once, and a row unpinned by click stays shut under the pointer', () => {
		fake();
		renderList();
		const a = screen.getByTestId('a');
		fireEvent.pointerEnter(a, mouse);
		fireEvent.click(screen.getByRole('button', { name: 'a' }));
		expect(isOpen('a')).toBe(true);
		fireEvent.click(screen.getByRole('button', { name: 'a' }));
		expect(isOpen('a')).toBe(false);
		fireEvent.pointerMove(a, { ...mouse, movementX: 5 });
		advance(openDelayMs * 2);
		expect(isOpen('a')).toBe(false);
	});

	it('a row that arrives under a still cursor does not open', () => {
		fake();
		renderList();
		// A row above folded and this one moved up under the pointer: the
		// browser reports an enter (and a zero-movement move), nothing else.
		fireEvent.pointerEnter(screen.getByTestId('b'), mouse);
		fireEvent.pointerMove(screen.getByTestId('b'), mouse);
		advance(openDelayMs * 3);
		expect(isOpen('b')).toBe(false);
	});

	it('ignores touch and pen pointers', () => {
		fake();
		renderList();
		fireEvent.pointerEnter(screen.getByTestId('a'), { pointerType: 'touch' });
		fireEvent.pointerMove(screen.getByTestId('a'), { pointerType: 'touch', movementX: 3 });
		advance(openDelayMs * 2);
		expect(isOpen('a')).toBe(false);
	});
});

describe('useHoverIntent with pins (multi-pin)', () => {
	afterEach(() => vi.useRealTimers());
	const fake = () => vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'performance'] });
	const click = (name: string) => fireEvent.click(screen.getByRole('button', { name }));
	const isPinned = (name: string) => screen.getByTestId(name).dataset.pinned === 'true';
	const isPreview = (name: string) => screen.getByTestId(name).dataset.preview === 'true';
	const stack = () => screen.getByTestId('stack').textContent;
	const escape = () => fireEvent.keyDown(document.body, { key: 'Escape' });
	/** Past the window in which rows may still be moving, so a leave is taken
	 * at its word. */
	const settle = () => advance(HOVER_INTENT.layoutSettleMs + 20);
	const hoverOpen = (name: string) => {
		const el = screen.getByTestId(name);
		fireEvent.pointerEnter(el, mouse);
		fireEvent.pointerMove(el, { ...mouse, movementX: 2 });
		advance(openDelayMs + 10);
	};
	const pointerOff = (name: string) => {
		settle();
		fireEvent.pointerLeave(screen.getByTestId(name), mouse);
		advance(closeGraceMs + HOVER_INTENT.handoffMaxMs + 20);
	};

	it('a click on a row hover has opened pins it, with no gap in between', () => {
		fake();
		renderList();
		hoverOpen('a');
		expect(isPreview('a')).toBe(true);
		click('a');
		expect(isOpen('a')).toBe(true);
		expect(isPinned('a')).toBe(true);
		// The pin holds it: the preview has gone.
		expect(isPreview('a')).toBe(false);
	});

	it('two rows pin at once: pinning a second keeps the first open', () => {
		fake();
		renderList();
		click('a');
		click('b');
		expect(isPinned('a')).toBe(true);
		expect(isPinned('b')).toBe(true);
		expect(isOpen('a')).toBe(true);
		expect(isOpen('b')).toBe(true);
		expect(stack()).toBe('a,b');
	});

	it('a second click unpins only that row', () => {
		fake();
		renderList();
		click('a');
		click('b');
		click('a');
		expect(isOpen('a')).toBe(false);
		expect(isPinned('a')).toBe(false);
		expect(isOpen('b')).toBe(true);
		expect(isPinned('b')).toBe(true);
	});

	it('leaving never folds a pinned row; a row unpinned under the pointer stays shut', () => {
		fake();
		renderList();
		const a = screen.getByTestId('a');
		fireEvent.pointerEnter(a, mouse);
		click('a');
		pointerOff('a');
		expect(isOpen('a')).toBe(true);

		fireEvent.pointerEnter(a, mouse);
		click('a');
		expect(isOpen('a')).toBe(false);
		fireEvent.pointerMove(a, { ...mouse, movementX: 3 });
		advance(openDelayMs * 2);
		expect(isOpen('a')).toBe(false);
	});

	it('hovering a pinned row does nothing; hover previews only unpinned rows', () => {
		fake();
		renderList(['a', 'b', 'c']);
		click('a');
		click('b');
		// Resting on a pinned row arms nothing.
		hoverOpen('a');
		expect(isPreview('a')).toBe(false);
		pointerOff('a');
		expect(isOpen('a')).toBe(true);

		// The third row previews alongside both pins, and folds on leave.
		hoverOpen('c');
		expect(isPreview('c')).toBe(true);
		expect(isOpen('a')).toBe(true);
		expect(isOpen('b')).toBe(true);
		pointerOff('c');
		expect(isOpen('c')).toBe(false);
		expect(isOpen('a')).toBe(true);
		expect(isOpen('b')).toBe(true);
	});

	it('a click while the open is pending pins at once and the timer is cancelled', () => {
		fake();
		renderList();
		const a = screen.getByTestId('a');
		fireEvent.pointerEnter(a, mouse);
		fireEvent.pointerMove(a, { ...mouse, movementX: 2 });
		advance(openDelayMs / 3);
		click('a');
		expect(isPinned('a')).toBe(true);
		advance(openDelayMs * 2);
		expect(isOpen('a')).toBe(true);
		expect(isPreview('a')).toBe(false);
	});

	it('a preview left for a row being pinned folds as that row opens', () => {
		fake();
		renderList();
		hoverOpen('a');
		settle();
		const b = screen.getByTestId('b');
		fireEvent.pointerLeave(screen.getByTestId('a'), mouse);
		fireEvent.pointerEnter(b, mouse);
		fireEvent.pointerMove(b, { ...mouse, movementY: 2 });
		advance(closeGraceMs + 20);
		// a holds for b's arming; b is pinned instead, and a folds with it.
		expect(isOpen('a')).toBe(true);
		click('b');
		expect(isOpen('b')).toBe(true);
		expect(isOpen('a')).toBe(false);
	});

	it('Escape unpins the latest pin first, one per press', () => {
		fake();
		renderList(['a', 'b', 'c']);
		click('a');
		click('c');
		click('b');
		escape();
		expect(isPinned('b')).toBe(false);
		expect(stack()).toBe('a,c');
		escape();
		expect(isPinned('c')).toBe(false);
		expect(isPinned('a')).toBe(true);
		escape();
		expect(isOpen('a')).toBe(false);
		expect(stack()).toBe('');
	});

	it('Escape folds a hover preview before it takes a pin', () => {
		fake();
		renderList(['a', 'b']);
		click('a');
		hoverOpen('b');
		expect(isPreview('b')).toBe(true);
		escape();
		expect(isOpen('b')).toBe(false);
		expect(isPinned('a')).toBe(true);
		escape();
		expect(isPinned('a')).toBe(false);
	});

	it('Escape inside a dialog leaves the pins alone', () => {
		fake();
		render(
			<>
				<List names={['a']} />
				<div role="dialog">
					<input aria-label="field" />
				</div>
			</>,
		);
		click('a');
		fireEvent.keyDown(screen.getByLabelText('field'), { key: 'Escape' });
		expect(isPinned('a')).toBe(true);
	});

	it('Escape in a ConfirmDialog closes the dialog and leaves the preview and the pin', async () => {
		fake();
		function Page() {
			const [confirm, setConfirm] = useState(true);
			return (
				<>
					<List names={['a', 'b']} />
					<ConfirmDialog
						open={confirm}
						title="Remove?"
						body="Sure?"
						confirmLabel="Remove"
						onConfirm={() => setConfirm(false)}
						onClose={() => setConfirm(false)}
					/>
				</>
			);
		}
		render(<Page />);
		const dialog = document.querySelector('dialog') as HTMLDialogElement;
		// A native `<dialog>`, no `role`: the guard must know it by its tag.
		expect(dialog).not.toHaveAttribute('role');
		click('a');
		hoverOpen('b');
		expect(isPinned('a')).toBe(true);
		expect(isPreview('b')).toBe(true);
		vi.useRealTimers();
		// A trusted key press, so the browser's own Escape closes the modal.
		within(dialog).getByRole('button', { name: 'Cancel' }).focus();
		await browserUser.keyboard('{Escape}');
		await waitFor(() => expect(dialog.open).toBe(false));
		expect(isPinned('a')).toBe(true);
		expect(isPreview('b')).toBe(true);
	});

	it('clear() lets every pin go', () => {
		fake();
		renderList(['a', 'b', 'c']);
		click('a');
		click('b');
		click('c');
		click('clear');
		for (const name of ['a', 'b', 'c']) expect(isOpen(name)).toBe(false);
		expect(stack()).toBe('');
	});
});

describe('useHoverIntent row to row', () => {
	afterEach(() => vi.useRealTimers());
	const fake = () => vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'performance'] });

	it('a row left for an arming sibling folds as that sibling opens, not before', () => {
		fake();
		renderList();
		const a = screen.getByTestId('a');
		const b = screen.getByTestId('b');
		fireEvent.pointerEnter(a, mouse);
		fireEvent.pointerMove(a, { ...mouse, movementX: 2 });
		advance(openDelayMs + 10);
		advance(HOVER_INTENT.layoutSettleMs);

		fireEvent.pointerLeave(a, mouse);
		fireEvent.pointerEnter(b, mouse);
		fireEvent.pointerMove(b, { ...mouse, movementY: 2 });
		// Past the leave grace, b still arming: a holds.
		advance(closeGraceMs + 20);
		expect(isOpen('a')).toBe(true);
		expect(isOpen('b')).toBe(false);
		// b opens, and a folds in the same tick.
		advance(openDelayMs - closeGraceMs);
		expect(isOpen('b')).toBe(true);
		expect(isOpen('a')).toBe(false);
	});

	it('a leave reported while the rows are moving waits for real movement', () => {
		fake();
		renderList();
		const a = screen.getByTestId('a');
		fireEvent.pointerEnter(a, mouse);
		fireEvent.pointerMove(a, { ...mouse, movementX: 2 });
		advance(openDelayMs + 10);
		expect(isOpen('a')).toBe(true);
		// Its own growth moves the layout; the browser reports a leave with the
		// pointer still. Not taken at its word…
		fireEvent.pointerLeave(a, mouse);
		advance(closeGraceMs + 20);
		expect(isOpen('a')).toBe(true);
		// …until the pointer really moves (here, off the row: `:hover` is false).
		fireEvent.pointerMove(window, { ...mouse, movementY: 4 });
		advance(closeGraceMs + 20);
		expect(isOpen('a')).toBe(false);
	});
});

describe('useHoverIntent dead zones', () => {
	afterEach(() => vi.useRealTimers());
	const fake = () => vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'performance'] });

	it('resting on a dead zone never opens the row, however long', () => {
		fake();
		renderList();
		const a = screen.getByTestId('a');
		const zone = screen.getByTestId('a-ignore-child');
		fireEvent.pointerEnter(a, mouse);
		fireEvent.pointerMove(zone, { ...mouse, movementX: 2 });
		advance(openDelayMs * 5);
		expect(isOpen('a')).toBe(false);
	});

	it('moving from the body onto a dead zone cancels a pending open', () => {
		fake();
		renderList();
		const a = screen.getByTestId('a');
		fireEvent.pointerEnter(a, mouse);
		fireEvent.pointerMove(a, { ...mouse, movementX: 2 });
		advance(openDelayMs - 50);
		fireEvent.pointerMove(screen.getByTestId('a-ignore'), { ...mouse, movementX: 2 });
		advance(openDelayMs * 3);
		expect(isOpen('a')).toBe(false);
	});

	it('body → dead zone → body restarts the wait from zero', () => {
		fake();
		renderList();
		const a = screen.getByTestId('a');
		fireEvent.pointerEnter(a, mouse);
		fireEvent.pointerMove(a, { ...mouse, movementX: 2 });
		advance(openDelayMs - 50);
		fireEvent.pointerMove(screen.getByTestId('a-ignore'), { ...mouse, movementX: 2 });
		advance(100);
		fireEvent.pointerMove(a, { ...mouse, movementX: -2 });
		advance(openDelayMs - 10);
		expect(isOpen('a')).toBe(false);
		advance(20);
		expect(isOpen('a')).toBe(true);
	});

	it('a preview already open stays as it is over a dead zone, which is not a leave', () => {
		fake();
		renderList();
		const a = screen.getByTestId('a');
		fireEvent.pointerEnter(a, mouse);
		fireEvent.pointerMove(a, { ...mouse, movementX: 2 });
		advance(openDelayMs + 10);
		expect(isOpen('a')).toBe(true);
		fireEvent.pointerMove(screen.getByTestId('a-ignore'), { ...mouse, movementX: 2 });
		advance(closeGraceMs * 4);
		expect(isOpen('a')).toBe(true);
	});
});
