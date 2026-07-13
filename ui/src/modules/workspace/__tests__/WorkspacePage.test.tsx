import { describe, it, expect, beforeEach } from 'vitest';
import {
	renderWithProviders,
	screen,
	waitFor,
	userEvent,
	checkA11y,
	createErrorHandler,
} from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { workspaceHandlers } from '@/modules/workspace/mocks/handlers';
import { setToken } from '@/shared/api';
import WorkspacePage from '@/modules/workspace/pages/WorkspacePage';

/**
 * PageHeader animates in from opacity:0 via framer-motion. axe runs
 * synchronously, so without settling it sees the button mid-fade and reports a
 * false color-contrast failure. Wait until the entrance animation reaches full
 * opacity before auditing.
 */
async function settleAnimations(container: HTMLElement): Promise<void> {
	await waitFor(() => {
		const faded = Array.from(container.querySelectorAll<HTMLElement>('*')).find((el) => {
			if (el.hasAttribute('disabled') || el.getAttribute('aria-disabled') === 'true')
				return false;
			const opacity = Number.parseFloat(getComputedStyle(el).opacity);
			return !Number.isNaN(opacity) && opacity > 0 && opacity < 1;
		});
		expect(faded).toBeUndefined();
	});
}

describe('WorkspacePage', () => {
	beforeEach(() => {
		setToken('test-token');
		// A sibling module (dashboard) also registers `GET /apis` in the global
		// table; MSW resolves first-match, so prepend the workspace handlers to
		// guarantee these tests see the workspace fixture regardless of order.
		worker.use(...workspaceHandlers);
	});

	it('lists the workspace APIs', async () => {
		renderWithProviders(<WorkspacePage />);
		expect(await screen.findByText('Stripe')).toBeInTheDocument();
		// The draft-only API falls back to vendor/name and shows a Draft pill.
		expect(await screen.findByText('adyen/pos-terminal-management-api')).toBeInTheDocument();
		expect(screen.getByText('Draft')).toBeInTheDocument();
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderWithProviders(<WorkspacePage />);
		await screen.findByText('Stripe');
		await settleAnimations(container);
		await checkA11y(container);
	});

	it('filters APIs in-memory', async () => {
		const user = userEvent.setup();
		renderWithProviders(<WorkspacePage />);
		await screen.findByText('Stripe');

		await user.type(screen.getByLabelText('Filter your APIs'), 'stripe');

		await waitFor(() => {
			expect(screen.queryByText('adyen/pos-terminal-management-api')).not.toBeInTheDocument();
		});
		expect(screen.getByText('Stripe')).toBeInTheDocument();
	});

	it('shows an empty state with an import CTA when there are no APIs', async () => {
		worker.use(
			createErrorHandler('get', '/apis', {
				status: 200,
				body: { data: [], has_more: false, next_cursor: null },
			}),
		);
		renderWithProviders(<WorkspacePage />);
		expect(await screen.findByText('No APIs in your workspace yet')).toBeInTheDocument();
		expect(screen.getAllByTestId('workspace-import-open').length).toBeGreaterThanOrEqual(1);
	});

	it('surfaces a load error with a retry affordance', async () => {
		worker.use(createErrorHandler('get', '/apis', { status: 500 }));
		renderWithProviders(<WorkspacePage />);
		expect(await screen.findByTestId('workspace-grid-error')).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
	});

	it('opens the import dialog from the header button', async () => {
		const user = userEvent.setup();
		renderWithProviders(<WorkspacePage />);
		await screen.findByText('Stripe');

		await user.click(screen.getAllByTestId('workspace-import-open')[0]);
		expect(await screen.findByTestId('import-spec-dialog')).toBeInTheDocument();
	});
});
