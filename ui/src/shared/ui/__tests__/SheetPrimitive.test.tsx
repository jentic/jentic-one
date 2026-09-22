import { useRef, useState } from 'react';
// The real (CDP-driven) keyboard: a native <dialog>'s Escape close request
// only fires for TRUSTED key events, which @testing-library/user-event's
// synthetic dispatch cannot produce.
import { userEvent as browserUser } from 'vitest/browser';
import { renderWithProviders, screen, userEvent, waitFor, checkA11y } from '@/__tests__/test-utils';
import { SheetPrimitive } from '@/shared/ui/SheetPrimitive';

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
		// Exit animation runs for 300ms before the sheet unmounts.
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

	it('renders nothing when closed', () => {
		renderWithProviders(<SheetHarness initialOpen={false} />);
		expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
	});

	it('has no critical a11y violations', async () => {
		renderWithProviders(<SheetHarness />);
		// Sheet portals to document.body, so scan the whole document.
		await checkA11y(document.body, { modal: true });
	});
});
