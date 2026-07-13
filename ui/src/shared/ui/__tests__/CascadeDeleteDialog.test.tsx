import { useState } from 'react';
import { renderWithProviders, screen, userEvent, checkA11y } from '@/__tests__/test-utils';
import { CascadeDeleteDialog } from '@/shared/ui/CascadeDeleteDialog';
import type { CascadeDependentGroup, CascadeEntityType } from '@/shared/ui/CascadeDeleteDialog';

function Harness({
	entityType = 'credential',
	entityName = 'Stripe (prod)',
	dependents,
	loading = false,
	error,
	onConfirm = () => {},
}: {
	entityType?: CascadeEntityType;
	entityName?: string;
	dependents?: CascadeDependentGroup[];
	loading?: boolean;
	error?: Error | string | null;
	onConfirm?: () => void;
}) {
	const [open, setOpen] = useState(true);
	return (
		<>
			<button type="button" onClick={() => setOpen(true)}>
				reopen
			</button>
			<CascadeDeleteDialog
				open={open}
				onClose={() => setOpen(false)}
				onConfirm={onConfirm}
				entityType={entityType}
				entityName={entityName}
				dependents={dependents}
				loading={loading}
				error={error}
			/>
		</>
	);
}

describe('CascadeDeleteDialog', () => {
	it('renders a type-specific title and generic warning when no dependents are given', () => {
		renderWithProviders(<Harness entityType="credential" entityName="Stripe (prod)" />);
		expect(screen.getByRole('heading', { name: 'Delete credential' })).toBeInTheDocument();
		expect(
			screen.getByText(/Agents and toolkits that authenticate with this credential/i),
		).toBeInTheDocument();
	});

	it('uses archive wording for agents and service accounts', () => {
		const { unmount } = renderWithProviders(
			<Harness entityType="agent" entityName="Build Bot" />,
		);
		expect(screen.getByRole('heading', { name: 'Archive agent' })).toBeInTheDocument();
		expect(screen.getByText(/Archiving is permanent/i)).toBeInTheDocument();
		unmount();

		renderWithProviders(<Harness entityType="service-account" entityName="CI runner" />);
		expect(
			screen.getByRole('heading', { name: 'Archive service account' }),
		).toBeInTheDocument();
	});

	it('keeps the confirm button disabled until the entity name is typed exactly', async () => {
		const user = userEvent.setup();
		const onConfirm = vi.fn();
		renderWithProviders(<Harness entityName="Stripe (prod)" onConfirm={onConfirm} />);

		const confirm = screen.getByRole('button', { name: /Delete credential/i });
		expect(confirm).toBeDisabled();

		const field = screen.getByLabelText(/Type Stripe \(prod\) to confirm/i);
		await user.type(field, 'wrong');
		expect(confirm).toBeDisabled();

		await user.clear(field);
		await user.type(field, 'Stripe (prod)');
		expect(confirm).toBeEnabled();

		await user.click(confirm);
		expect(onConfirm).toHaveBeenCalledOnce();
	});

	it('renders the grouped blast-radius list with counts and names when dependents are provided', () => {
		renderWithProviders(
			<Harness
				entityType="toolkit"
				entityName="GitHub toolkit"
				dependents={[
					{ label: 'agent grant', count: 2, names: ['Build Bot', 'Deploy Bot'] },
					{ label: 'API key', count: 1, names: ['ci-key'] },
				]}
			/>,
		);

		expect(screen.getByText(/will also remove 3 dependents/i)).toBeInTheDocument();
		expect(screen.getByText('2 agent grants')).toBeInTheDocument();
		expect(screen.getByText('1 API key')).toBeInTheDocument();
		expect(screen.getByText('Build Bot')).toBeInTheDocument();
		expect(screen.getByText('ci-key')).toBeInTheDocument();
	});

	it('falls back to the generic warning when dependents is an empty array', () => {
		renderWithProviders(
			<Harness entityType="toolkit" entityName="Empty toolkit" dependents={[]} />,
		);
		expect(screen.queryByText(/will also remove/i)).not.toBeInTheDocument();
		expect(screen.getByText(/Agents granted this toolkit will fail/i)).toBeInTheDocument();
	});

	it('disables the confirm field and buttons while loading', () => {
		renderWithProviders(<Harness loading />);
		expect(screen.getByLabelText(/to confirm/i)).toBeDisabled();
		expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();
	});

	it('hides the error until the user attempts a confirm, and clears it on reopen', async () => {
		const user = userEvent.setup();
		renderWithProviders(
			<Harness entityName="Stripe (prod)" error="Cascade failed mid-flight" />,
		);

		// Error from a prior session must NOT show before the user acts here.
		expect(screen.queryByText(/cascade failed mid-flight/i)).not.toBeInTheDocument();

		// Type the name and confirm → the in-session attempt surfaces the error.
		await user.type(screen.getByLabelText(/Type Stripe \(prod\) to confirm/i), 'Stripe (prod)');
		await user.click(screen.getByRole('button', { name: /Delete credential/i }));
		expect(screen.getByText(/cascade failed mid-flight/i)).toBeInTheDocument();

		// Dismiss and reopen → the stale error is gone again until the next attempt.
		await user.click(screen.getByRole('button', { name: 'Cancel' }));
		await user.click(screen.getByRole('button', { name: 'reopen' }));
		expect(screen.queryByText(/cascade failed mid-flight/i)).not.toBeInTheDocument();
	});

	it('has no critical a11y violations in generic-warning mode', async () => {
		const { container } = renderWithProviders(<Harness />);
		await checkA11y(container);
	});

	it('has no critical a11y violations in blast-radius mode', async () => {
		const { container } = renderWithProviders(
			<Harness
				entityType="api"
				entityName="httpbin"
				dependents={[{ label: 'toolkit binding', count: 3, names: ['a', 'b', 'c'] }]}
			/>,
		);
		await checkA11y(container);
	});
});
