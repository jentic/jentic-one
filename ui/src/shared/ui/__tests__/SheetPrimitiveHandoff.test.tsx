/**
 * SheetPrimitive hand-offs: steps of one flow (`handoffKey`) read as one panel —
 * instant swap, hold expiry, focus-return travel, and an inert unmount snapshot
 * that duplicates no `id`/`data-testid`.
 */
import { useState } from 'react';
import { describe, it, expect, afterEach } from 'vitest';
import { act, renderWithProviders, screen, userEvent, waitFor } from '@/__tests__/test-utils';
import {
	SHEET_EXIT_MS,
	SheetPrimitive,
	endSheetHandoff,
	holdSheetForHandoff,
} from '@/shared/ui/SheetPrimitive';

const FLOW = 'test-flow';

afterEach(() => endSheetHandoff(FLOW));

/** `pause`: step one has left and step two has not opened yet. */
type Step = 'shut' | 'one' | 'pause' | 'two' | 'done';

/** Two steps of one flow, opened from a real control so focus return is real. */
function FlowHarness({
	hold = false,
	unmountFirstOnLeave = false,
}: {
	/** Step one asks to hold its place before closing (its data is still loading). */
	hold?: boolean;
	/** The host drops step one entirely as it closes (a layout swap). */
	unmountFirstOnLeave?: boolean;
}) {
	const [step, setStep] = useState<Step>('shut');
	const firstOpen = step === 'one';
	return (
		<>
			<button type="button" data-testid="opener" onClick={() => setStep('one')}>
				Open the flow
			</button>
			{/* A host that swaps layouts drops step one the moment it closes. */}
			{(!unmountFirstOnLeave || firstOpen) && (
				<SheetPrimitive
					open={firstOpen}
					onClose={() => setStep('done')}
					ariaLabel="Step one"
					handoffKey={FLOW}
				>
					<button
						type="button"
						data-testid="to-two"
						onClick={() => {
							if (hold) holdSheetForHandoff(FLOW);
							setStep(unmountFirstOnLeave ? 'pause' : 'two');
						}}
					>
						Continue
					</button>
				</SheetPrimitive>
			)}
			{/* Step one's data arrives a beat later, as the setup queue's does. */}
			<button type="button" data-testid="arrive" onClick={() => setStep('two')}>
				Arrive
			</button>
			<SheetPrimitive
				open={step === 'two'}
				onClose={() => setStep('done')}
				ariaLabel="Step two"
				handoffKey={FLOW}
			>
				<button type="button" data-testid="finish" onClick={() => setStep('done')}>
					Finish
				</button>
			</SheetPrimitive>
		</>
	);
}

function LoneHoldHarness() {
	const [open, setOpen] = useState(false);
	return (
		<>
			<button type="button" data-testid="opener" onClick={() => setOpen(true)}>
				Open
			</button>
			<SheetPrimitive
				open={open}
				onClose={() => setOpen(false)}
				ariaLabel="Lone step"
				handoffKey={FLOW}
			>
				<button
					type="button"
					data-testid="hold-and-close"
					onClick={() => {
						holdSheetForHandoff(FLOW);
						setOpen(false);
					}}
				>
					Hold then close
				</button>
			</SheetPrimitive>
		</>
	);
}

const panels = () => screen.queryAllByTestId('sheet-primitive');
const sheet = (name: string) => screen.queryByRole('dialog', { name });

/** Wait for OPEN (entrance done, focus placed): a close while `entering` takes the same-commit branch. */
async function settled(name: string, control: string): Promise<void> {
	await waitFor(() => expect(sheet(name)).toBeInTheDocument());
	await waitFor(() => expect(screen.getByTestId(control)).toHaveFocus());
}

describe('SheetPrimitive hand-offs', () => {
	it('the next step takes the leaving sheet’s place at once, with one panel on screen', async () => {
		const user = userEvent.setup();
		renderWithProviders(<FlowHarness hold />);
		await user.click(screen.getByTestId('opener'));
		await settled('Step one', 'to-two');

		await user.click(screen.getByTestId('to-two'));
		await waitFor(() => expect(sheet('Step two')).toBeInTheDocument());
		expect(sheet('Step two')).toHaveAttribute('data-handoff', 'in');
		expect(panels()).toHaveLength(1);
		expect(screen.getAllByTestId('sheet-backdrop')).toHaveLength(1);
		expect(sheet('Step one')).toBeNull();
	});

	it('a close and an open committed together swap with no exit animation', async () => {
		const user = userEvent.setup();
		// No hold: the step flips in one commit, which is the same-commit branch.
		renderWithProviders(<FlowHarness />);
		await user.click(screen.getByTestId('opener'));
		await settled('Step one', 'to-two');

		await user.click(screen.getByTestId('to-two'));
		expect(sheet('Step one')).toBeNull();
		await waitFor(() => expect(sheet('Step two')).toBeInTheDocument());
		expect(panels()).toHaveLength(1);
	});

	it('a hold nobody takes up closes after all, and hands focus back', async () => {
		const user = userEvent.setup();
		renderWithProviders(<LoneHoldHarness />);
		const opener = screen.getByTestId('opener');
		await user.click(opener);
		await settled('Lone step', 'hold-and-close');

		await user.click(screen.getByTestId('hold-and-close'));
		await waitFor(() => expect(sheet('Lone step')).toHaveAttribute('data-handoff', 'holding'));

		await waitFor(() => expect(sheet('Lone step')).toBeNull(), { timeout: 5000 });
		await waitFor(() => expect(opener).toHaveFocus());
	});

	it('a sheet reopened while holding stays open past the hold', async () => {
		const user = userEvent.setup();
		renderWithProviders(<LoneHoldHarness />);
		const opener = screen.getByTestId('opener');
		await user.click(opener);
		await settled('Lone step', 'hold-and-close');
		await user.click(screen.getByTestId('hold-and-close'));
		await waitFor(() => expect(sheet('Lone step')).toHaveAttribute('data-handoff', 'holding'));

		// The scrim covers the opener, so it is clicked programmatically.
		act(() => opener.click());
		await waitFor(() => expect(sheet('Lone step')).not.toHaveAttribute('data-handoff'));
		await new Promise((r) => setTimeout(r, 2000 + SHEET_EXIT_MS + 100));
		expect(sheet('Lone step')).toBeInTheDocument();
	});

	it('the focus-return target travels to the arriving sheet and lands at the end of the flow', async () => {
		const user = userEvent.setup();
		renderWithProviders(<FlowHarness hold />);
		const opener = screen.getByTestId('opener');
		await user.click(opener);
		await settled('Step one', 'to-two');

		await user.click(screen.getByTestId('to-two'));
		await settled('Step two', 'finish');
		expect(opener).not.toHaveFocus();

		await user.click(screen.getByTestId('finish'));
		await waitFor(() => expect(sheet('Step two')).toBeNull(), {
			timeout: SHEET_EXIT_MS + 1500,
		});
		await waitFor(() => expect(opener).toHaveFocus());
	});

	it('a holding sheet unmounted by its host leaves an inert snapshot that duplicates no id or test id', async () => {
		const user = userEvent.setup();
		renderWithProviders(<FlowHarness hold unmountFirstOnLeave />);
		await user.click(screen.getByTestId('opener'));
		await settled('Step one', 'to-two');
		const idsBefore = new Set(
			Array.from(document.querySelectorAll<HTMLElement>('[id]')).map((el) => el.id),
		);

		// Step one is dropped by its host while step two still loads: nothing takes the place yet.
		await user.click(screen.getByTestId('to-two'));
		await waitFor(() => expect(sheet('Step one')).toBeNull());

		const ghost = document.body.querySelector<HTMLElement>('[inert][aria-hidden="true"]');
		expect(ghost).not.toBeNull();
		expect(ghost!.querySelector('[role="dialog"]')).toBeNull();
		for (const el of [ghost!, ...ghost!.querySelectorAll<HTMLElement>('*')]) {
			expect(el.id).toBe('');
			expect(el.getAttributeNames().filter((n) => n.startsWith('data-'))).toEqual([]);
		}
		expect(panels()).toHaveLength(0);
		for (const id of idsBefore) {
			expect(document.querySelectorAll(`[id="${CSS.escape(id)}"]`)).toHaveLength(1);
		}

		await user.click(screen.getByTestId('arrive'));
		await waitFor(() => expect(sheet('Step two')).toBeInTheDocument());
		expect(document.body.querySelector('[inert][aria-hidden="true"]')).toBeNull();
		expect(() => screen.getByTestId('sheet-primitive')).not.toThrow();
	});

	it('a torn-down flow drops its snapshot rather than letting the timer mutate the document later', async () => {
		const user = userEvent.setup();
		renderWithProviders(<FlowHarness hold unmountFirstOnLeave />);
		await user.click(screen.getByTestId('opener'));
		await settled('Step one', 'to-two');
		await user.click(screen.getByTestId('to-two'));
		await waitFor(() =>
			expect(document.body.querySelector('[inert][aria-hidden="true"]')).not.toBeNull(),
		);

		endSheetHandoff(FLOW);
		expect(document.body.querySelector('[inert][aria-hidden="true"]')).toBeNull();
	});

	it('keeps focus inside the dialog when the last reachable control is followed by an inert pane', async () => {
		const user = userEvent.setup();
		renderWithProviders(
			<SheetPrimitive open onClose={() => {}} ariaLabel="Panes">
				<div>
					<button type="button" data-testid="first">
						First
					</button>
					<button type="button" data-testid="last">
						Last
					</button>
					{/* The sheet keeps both panes mounted; this one is inert. */}
					<div inert>
						<button type="button" data-testid="hidden-pane-action">
							Unreachable
						</button>
					</div>
				</div>
			</SheetPrimitive>,
		);
		const first = screen.getByTestId('first');
		await waitFor(() => expect(first).toHaveFocus());

		// Tab wraps past the inert pane, whose control can never hold focus.
		screen.getByTestId('last').focus();
		await user.keyboard('{Tab}');
		expect(first).toHaveFocus();
		expect(screen.getByTestId('hidden-pane-action')).not.toHaveFocus();
	});
});
