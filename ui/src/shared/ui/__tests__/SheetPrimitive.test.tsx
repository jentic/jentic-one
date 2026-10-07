import { useRef, useState } from 'react';
import { vi } from 'vitest';
// The real (CDP-driven) keyboard: a native <dialog>'s Escape close request
// only fires for TRUSTED key events, which @testing-library/user-event's
// synthetic dispatch cannot produce.
import { userEvent as browserUser } from 'vitest/browser';
import { renderWithProviders, screen, userEvent, waitFor, checkA11y } from '@/__tests__/test-utils';
import {
	SHEET_EXIT_MS,
	SheetBody,
	SheetFooter,
	SheetHeader,
	SheetPrimitive,
} from '@/shared/ui/SheetPrimitive';

function SheetHarness({ initialOpen = true }: { initialOpen?: boolean }) {
	const [open, setOpen] = useState(initialOpen);
	return (
		<SheetPrimitive open={open} onClose={() => setOpen(false)} ariaLabel="Details">
			<div>
				<h2>Sheet content</h2>
				<button type="button">Inside action</button>
			</div>
		</SheetPrimitive>
	);
}

/** A sheet with a native modal <dialog> stacked above it (top layer). */
function NestedDialogHarness() {
	const [open, setOpen] = useState(true);
	const dialogRef = useRef<HTMLDialogElement>(null);
	return (
		<SheetPrimitive open={open} onClose={() => setOpen(false)} ariaLabel="Details">
			<div>
				<h2>Sheet content</h2>
				<button type="button" onClick={() => dialogRef.current?.showModal()}>
					Open confirm
				</button>
				<dialog ref={dialogRef} aria-label="Confirm" data-testid="nested-dialog">
					<p>Confirm content</p>
					<button type="button" onClick={() => dialogRef.current?.close()}>
						Cancel
					</button>
				</dialog>
			</div>
		</SheetPrimitive>
	);
}

describe('SheetPrimitive', () => {
	it('renders content in a dialog role when open', () => {
		renderWithProviders(<SheetHarness />);
		expect(screen.getByRole('dialog', { name: 'Details' })).toBeInTheDocument();
		expect(screen.getByText('Sheet content')).toBeInTheDocument();
	});

	it('closes on Escape', async () => {
		const user = userEvent.setup();
		renderWithProviders(<SheetHarness />);
		// Wait until the sheet has finished entering: it auto-focuses the
		// first focusable child once `animationState` reaches 'open', which is
		// also when the Escape handler becomes active.
		await waitFor(() => {
			expect(screen.getByRole('button', { name: 'Inside action' })).toHaveFocus();
		});
		await user.keyboard('{Escape}');
		// The exit animation runs for SHEET_EXIT_MS before the sheet unmounts.
		await waitFor(
			() => {
				expect(screen.queryByText('Sheet content')).not.toBeInTheDocument();
			},
			{ timeout: 2000 },
		);
	});

	it('yields Escape to a native modal dialog above it, then closes on the next Escape', async () => {
		const user = userEvent.setup();
		renderWithProviders(<NestedDialogHarness />);
		await waitFor(() => {
			expect(screen.getByRole('button', { name: 'Open confirm' })).toHaveFocus();
		});

		await user.click(screen.getByRole('button', { name: 'Open confirm' }));
		const confirm = screen.getByTestId('nested-dialog') as HTMLDialogElement;
		expect(confirm.open).toBe(true);

		// First Escape: the sheet must neither preventDefault nor close itself — only
		// the dialog closes. Must be a real key press; the native close request ignores
		// synthetic events.
		await browserUser.keyboard('{Escape}');
		await waitFor(() => expect(confirm.open).toBe(false));
		expect(screen.getByText('Sheet content')).toBeInTheDocument();

		// Second Escape (no modal dialog left) closes the sheet.
		await browserUser.keyboard('{Escape}');
		await waitFor(
			() => {
				expect(screen.queryByText('Sheet content')).not.toBeInTheDocument();
			},
			{ timeout: 2000 },
		);
	});

	it('does not pull focus out of a sheet stacked on top of it', async () => {
		// The setup queue opens its credential form as a second sheet, portaled
		// outside the first. Each sheet's delayed initial focus must leave focus
		// where the user already put it — on a slow runner the outer sheet's timer
		// fires while the user is typing in the inner one.
		renderWithProviders(
			<>
				<SheetPrimitive open onClose={() => {}} ariaLabel="Queue">
					<button type="button">Queue action</button>
				</SheetPrimitive>
				<SheetPrimitive open onClose={() => {}} ariaLabel="Form">
					<button type="button">Close</button>
					<label>
						Name
						<input />
					</label>
				</SheetPrimitive>
			</>,
		);
		const input = screen.getByLabelText('Name');
		// Focused before either sheet's initial-focus timer has run.
		input.focus();
		const blurs = vi.fn();
		input.addEventListener('blur', blurs);
		await new Promise((r) => setTimeout(r, 300));
		expect(input).toHaveFocus();
		// Never moved away and back: every keystroke typed meanwhile would be lost.
		expect(blurs).not.toHaveBeenCalled();
	});

	it('stays mounted through the exit animation, then unmounts and calls onAfterClose', async () => {
		const onAfterClose = vi.fn();
		const { rerender } = renderWithProviders(
			<SheetPrimitive open onClose={() => {}} onAfterClose={onAfterClose} ariaLabel="Details">
				<p>Sheet content</p>
			</SheetPrimitive>,
		);
		await waitFor(() =>
			expect(screen.getByTestId('sheet-primitive').className).toContain('translate-x-0'),
		);
		rerender(
			<SheetPrimitive
				open={false}
				onClose={() => {}}
				onAfterClose={onAfterClose}
				ariaLabel="Details"
			>
				<p>Sheet content</p>
			</SheetPrimitive>,
		);
		// Mid-exit: still on screen, sliding out on the faster exit curve.
		const panel = screen.getByTestId('sheet-primitive');
		expect(panel.className).toContain('ease-in-exit');
		expect(panel.className).toContain('translate-x-full');
		expect(onAfterClose).not.toHaveBeenCalled();

		await new Promise((r) => setTimeout(r, SHEET_EXIT_MS + 50));
		expect(screen.queryByText('Sheet content')).not.toBeInTheDocument();
		expect(onAfterClose).toHaveBeenCalledTimes(1);
	});

	it('closes on a backdrop click', async () => {
		const user = userEvent.setup();
		renderWithProviders(<SheetHarness />);
		await waitFor(() => {
			expect(screen.getByRole('button', { name: 'Inside action' })).toHaveFocus();
		});
		await user.click(screen.getByTestId('sheet-backdrop'));
		await waitFor(() => expect(screen.queryByText('Sheet content')).not.toBeInTheDocument());
	});

	it('sizes side panels: sm (480px) by default, md (36rem) for previews, className wins', () => {
		const { rerender } = renderWithProviders(
			<SheetPrimitive open onClose={() => {}} ariaLabel="Details">
				<p>x</p>
			</SheetPrimitive>,
		);
		expect(screen.getByTestId('sheet-primitive').className).toContain('sm:w-[480px]');

		rerender(
			<SheetPrimitive open onClose={() => {}} size="md" ariaLabel="Details">
				<p>x</p>
			</SheetPrimitive>,
		);
		const md = screen.getByTestId('sheet-primitive').className;
		expect(md).toContain('sm:w-[min(36rem,100vw)]');
		expect(md).not.toContain('sm:w-[480px]');

		rerender(
			<SheetPrimitive open onClose={() => {}} className="sm:w-[640px]" ariaLabel="Details">
				<p>x</p>
			</SheetPrimitive>,
		);
		const custom = screen.getByTestId('sheet-primitive').className;
		expect(custom).toContain('sm:w-[640px]');
		expect(custom).not.toContain('sm:w-[480px]');
	});

	it('renders header / body / footer slots inside the panel', () => {
		renderWithProviders(
			<SheetPrimitive open onClose={() => {}} ariaLabel="Details" className="flex flex-col">
				<SheetHeader data-testid="slot-head">Head</SheetHeader>
				<SheetBody data-testid="slot-body">Body</SheetBody>
				<SheetFooter data-testid="slot-foot">Foot</SheetFooter>
			</SheetPrimitive>,
		);
		const panel = screen.getByTestId('sheet-primitive');
		expect(screen.getByTestId('slot-head').tagName).toBe('DIV');
		expect(screen.getByTestId('slot-foot').tagName).toBe('FOOTER');
		expect(screen.getByTestId('slot-foot').className).toContain('bg-surface-sheet-foot');
		expect(screen.getByTestId('slot-body').className).toContain('overflow-y-auto');
		for (const id of ['slot-head', 'slot-body', 'slot-foot']) {
			expect(panel).toContainElement(screen.getByTestId(id));
		}
	});

	it('renders nothing when closed', () => {
		renderWithProviders(<SheetHarness initialOpen={false} />);
		expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
	});

	it('has no critical a11y violations', async () => {
		renderWithProviders(<SheetHarness />);
		// Sheet portals to document.body, so scan the whole document.
		await checkA11y(document.body, { modal: true });
	});

	it('its header band is not a landmark — no second banner inside the sheet', () => {
		renderWithProviders(
			<SheetPrimitive open onClose={() => {}} ariaLabel="Details">
				<SheetHeader>
					<h2>Title</h2>
				</SheetHeader>
			</SheetPrimitive>,
		);
		const dialog = screen.getByRole('dialog', { name: 'Details' });
		expect(dialog.querySelector('header')).toBeNull();
		expect(screen.queryAllByRole('banner')).toHaveLength(0);
		expect(dialog.querySelector('[data-sheet-header]')).toHaveTextContent('Title');
	});
});
