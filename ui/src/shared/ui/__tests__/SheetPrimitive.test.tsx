import { useState } from 'react';
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

	it('renders nothing when closed', () => {
		renderWithProviders(<SheetHarness initialOpen={false} />);
		expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
	});

	it('has no critical a11y violations', async () => {
		renderWithProviders(<SheetHarness />);
		// Sheet portals to document.body, so scan the whole document.
		await checkA11y(document.body);
	});
});
