import { describe, expect, it } from 'vitest';
import { renderWithProviders, screen, userEvent, waitFor, within } from '@/__tests__/test-utils';
import AccessRequestShowcasePage from '@/modules/dev/pages/AccessRequestShowcasePage';
import { SHOWCASE_CASES } from '@/modules/dev/fixtures';

describe('AccessRequestShowcasePage (dev showcase)', () => {
	it('renders a card for every access-request possibility', () => {
		renderWithProviders(<AccessRequestShowcasePage />);
		for (const c of SHOWCASE_CASES) {
			expect(screen.getByText(c.title)).toBeInTheDocument();
		}
	});

	it('opens the provisioning-plan case in the setup wizard', async () => {
		const user = userEvent.setup();
		renderWithProviders(<AccessRequestShowcasePage />);

		// The OAuth plan card routes to the wizard; open it.
		const planCard = screen.getByTestId('case-plan-oauth-pending');
		const openBtn = within(planCard).getByRole('button', { name: /open/i });
		await user.click(openBtn);

		// The wizard's title + first step should appear.
		await waitFor(() => expect(screen.getByText('Set up access')).toBeInTheDocument());
		expect(screen.getByText('Create a toolkit')).toBeInTheDocument();
	});

	it('opens a single-item request in the plain approve/deny dialog', async () => {
		const user = userEvent.setup();
		renderWithProviders(<AccessRequestShowcasePage />);

		const scopeCard = screen.getByTestId('case-scope-grant-pending');
		const openBtn = within(scopeCard).getByRole('button', { name: /open/i });
		await user.click(openBtn);

		// Plain dialog banner, not the wizard.
		await waitFor(() =>
			expect(screen.getByText(/Agent is requesting access/i)).toBeInTheDocument(),
		);
		expect(screen.queryByText('Set up access')).not.toBeInTheDocument();
	});
});
