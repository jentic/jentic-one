import { useState } from 'react';
import { renderWithProviders, screen, userEvent, checkA11y } from '@/__tests__/test-utils';
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

	it('closes when the X button is clicked', async () => {
		const user = userEvent.setup();
		renderWithProviders(<DialogHarness />);
		await user.click(screen.getByRole('button', { name: 'Close' }));
		expect(screen.queryByText('Dialog body')).not.toBeVisible();
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderWithProviders(<DialogHarness />);
		await checkA11y(container);
	});
});
