import { renderWithProviders, screen, checkA11y } from '@/__tests__/test-utils';
import userEvent from '@testing-library/user-event';
import { vi } from 'vitest';
import { IdentitySettingsCard } from '@/shared/ui/IdentitySettingsCard';

describe('IdentitySettingsCard', () => {
	it('renders the immutable id row and seeds the form from props', () => {
		renderWithProviders(
			<IdentitySettingsCard
				idLabel="Agent ID"
				idValue="agent_1"
				name="support-agent"
				description="Answers tickets"
				onSave={vi.fn()}
			/>,
		);
		expect(screen.getByText('Agent ID')).toBeInTheDocument();
		expect(screen.getByText('agent_1')).toBeInTheDocument();
		expect(screen.getByLabelText('Name')).toHaveValue('support-agent');
		expect(screen.getByLabelText('Description')).toHaveValue('Answers tickets');
	});

	it('keeps Save disabled until something changed, then submits the draft', async () => {
		const user = userEvent.setup();
		const onSave = vi.fn().mockResolvedValue(undefined);
		renderWithProviders(
			<IdentitySettingsCard
				idLabel="Toolkit ID"
				idValue="tk_1"
				name="stripe"
				description={null}
				onSave={onSave}
			/>,
		);
		const save = screen.getByRole('button', { name: 'Save changes' });
		expect(save).toBeDisabled();
		await user.type(screen.getByLabelText('Description'), 'Payments toolkit');
		expect(save).toBeEnabled();
		await user.click(save);
		expect(onSave).toHaveBeenCalledWith({ name: 'stripe', description: 'Payments toolkit' });
		// After a successful save the drafts re-seed and the form reads clean.
		expect(screen.getByRole('button', { name: 'Save changes' })).toBeDisabled();
	});

	it('requires a name and surfaces the inline error instead of saving', async () => {
		const user = userEvent.setup();
		const onSave = vi.fn();
		renderWithProviders(
			<IdentitySettingsCard
				idLabel="Agent ID"
				idValue="agent_1"
				name="support-agent"
				description={null}
				onSave={onSave}
			/>,
		);
		await user.clear(screen.getByLabelText('Name'));
		await user.click(screen.getByRole('button', { name: 'Save changes' }));
		expect(screen.getByText('A name is required.')).toBeInTheDocument();
		expect(onSave).not.toHaveBeenCalled();
	});

	it('keeps the draft when the save rejects (retryable)', async () => {
		const user = userEvent.setup();
		const onSave = vi.fn().mockRejectedValue(new Error('boom'));
		renderWithProviders(
			<IdentitySettingsCard
				idLabel="Agent ID"
				idValue="agent_1"
				name="support-agent"
				description={null}
				onSave={onSave}
			/>,
		);
		await user.type(screen.getByLabelText('Name'), '-2');
		await user.click(screen.getByRole('button', { name: 'Save changes' }));
		expect(screen.getByLabelText('Name')).toHaveValue('support-agent-2');
		expect(screen.getByRole('button', { name: 'Save changes' })).toBeEnabled();
	});

	it('renders read-only (id + note, no form) when there is no update endpoint', () => {
		renderWithProviders(
			<IdentitySettingsCard
				idLabel="Account ID"
				idValue="sa_1"
				name="ci-bot"
				description={null}
				readOnlyNote="Renaming isn’t supported yet."
			/>,
		);
		expect(screen.getByText('Renaming isn’t supported yet.')).toBeInTheDocument();
		expect(screen.queryByLabelText('Name')).not.toBeInTheDocument();
		expect(screen.queryByRole('button', { name: 'Save changes' })).not.toBeInTheDocument();
	});

	it('is accessible', async () => {
		const { container } = renderWithProviders(
			<IdentitySettingsCard
				idLabel="Agent ID"
				idValue="agent_1"
				name="support-agent"
				description="Answers tickets"
				onSave={vi.fn()}
			/>,
		);
		await checkA11y(container);
	});
});
