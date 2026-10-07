import { useState } from 'react';
import { vi } from 'vitest';
import { renderWithProviders, screen, userEvent, waitFor, checkA11y } from '@/__tests__/test-utils';
import { Dialog } from '@/shared/ui/Dialog';
import { Button } from '@/shared/ui/Button';

function DialogHarness({ initialOpen = true }: { initialOpen?: boolean }) {
	const [open, setOpen] = useState(initialOpen);
	return (
		<Dialog
			open={open}
			onClose={() => setOpen(false)}
			title="My Dialog"
			footer={<Button onClick={() => setOpen(false)}>Save</Button>}
		>
			<p className="text-foreground">Dialog body</p>
		</Dialog>
	);
}

describe('Dialog', () => {
	it('renders title, body and footer when open', () => {
		renderWithProviders(<DialogHarness />);
		expect(screen.getByRole('heading', { name: 'My Dialog' })).toBeInTheDocument();
		expect(screen.getByText('Dialog body')).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Save' })).toBeInTheDocument();
	});

	it('renders an optional subtitle under the title', () => {
		renderWithProviders(
			<Dialog
				open
				onClose={() => {}}
				title="My Dialog"
				subtitle="Step 1 of 2 · Choose an API"
			>
				<p className="text-foreground">Dialog body</p>
			</Dialog>,
		);
		expect(screen.getByText('Step 1 of 2 · Choose an API')).toBeInTheDocument();
	});

	it('omits the subtitle slot when no subtitle is given', () => {
		renderWithProviders(<DialogHarness />);
		expect(screen.queryByText(/Step 1 of 2/)).not.toBeInTheDocument();
	});

	it('draws a faint hairline edge on the panel', () => {
		renderWithProviders(<DialogHarness />);
		const panel = screen.getByRole('dialog', { name: 'My Dialog' });
		expect(panel).toHaveClass('border', 'border-hairline-field', 'bg-surface-sheet');
		expect(panel).not.toHaveClass('border-0');
	});

	it('closes when the X button is clicked', async () => {
		const user = userEvent.setup();
		renderWithProviders(<DialogHarness />);
		await user.click(screen.getByRole('button', { name: 'Close' }));
		expect(screen.queryByText('Dialog body')).not.toBeVisible();
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderWithProviders(<DialogHarness />);
		// The open dialog IS the component under test, so the audit says so.
		await checkA11y(container, { modal: true });
	});

	it('syncs the owner when the browser closes the modal itself', async () => {
		const onClose = vi.fn();
		function Owner() {
			const [open, setOpen] = useState(true);
			return (
				<Dialog
					open={open}
					onClose={() => {
						onClose();
						setOpen(false);
					}}
					title="My Dialog"
				>
					<p className="text-foreground">Dialog body</p>
				</Dialog>
			);
		}
		renderWithProviders(<Owner />);
		const dialog = screen.getByRole('dialog', { name: 'My Dialog' }) as HTMLDialogElement;
		// A close the owner did not ask for (e.g. an Esc the close watcher
		// handles without a `cancel` event).
		dialog.close();
		await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
		expect(dialog).not.toHaveAttribute('open');
	});

	it('does not call onClose when the owner closes it', async () => {
		const onClose = vi.fn();
		const { rerender } = renderWithProviders(
			<Dialog open onClose={onClose} title="My Dialog">
				<p className="text-foreground">Dialog body</p>
			</Dialog>,
		);
		const dialog = screen.getByRole('dialog', { name: 'My Dialog' });
		rerender(
			<Dialog open={false} onClose={onClose} title="My Dialog">
				<p className="text-foreground">Dialog body</p>
			</Dialog>,
		);
		await waitFor(() => expect(dialog).not.toHaveAttribute('open'));
		// `close` is dispatched asynchronously; give it a turn to land.
		await new Promise((r) => setTimeout(r, 0));
		expect(onClose).not.toHaveBeenCalled();
	});
});
