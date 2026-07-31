import { describe, it, expect, afterEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { renderWithProviders, screen, waitFor, userEvent } from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { Toaster, clearAllToasts } from '@/shared/ui';
import { OverviewStrip } from '@/modules/workspace/components/OverviewStrip';
import type { WorkspaceApi } from '@/modules/workspace/api';

function makeApi(overrides: Partial<WorkspaceApi> = {}): WorkspaceApi {
	return {
		api: { vendor: 'stripe.com', name: 'stripe-api', version: '1', host: 'api.stripe.com' },
		displayName: 'Stripe',
		description: null,
		iconUrl: null,
		currentRevisionId: 'rev_1',
		revisionCount: 2,
		operationCount: 3,
		securitySchemes: ['bearer'],
		createdAt: '2026-01-01T00:00:00Z',
		updatedAt: '2026-01-02T00:00:00Z',
		...overrides,
	};
}

describe('OverviewStrip — update-available re-import', () => {
	afterEach(() => {
		clearAllToasts();
	});

	it('shows no update banner when updateAvailable is absent', () => {
		renderWithProviders(<OverviewStrip api={makeApi()} />);
		expect(screen.queryByTestId('workspace-update-available')).not.toBeInTheDocument();
	});

	it('shows the indicator + an enabled Re-import button for catalog-origin APIs', () => {
		renderWithProviders(
			<OverviewStrip api={makeApi({ updateAvailable: true, origin: 'catalog' })} />,
		);
		expect(screen.getByTestId('workspace-update-available')).toBeInTheDocument();
		expect(screen.getByTestId('workspace-reimport')).toBeEnabled();
	});

	it('disables the Re-import button for overlay-origin APIs', () => {
		renderWithProviders(
			<OverviewStrip api={makeApi({ updateAvailable: true, origin: 'overlay' })} />,
		);
		expect(screen.getByTestId('workspace-update-available')).toBeInTheDocument();
		expect(screen.getByTestId('workspace-reimport')).toBeDisabled();
	});

	it('queues a re-import and toasts on click (catalog origin)', async () => {
		const user = userEvent.setup();
		let importedApiId: string | null = null;
		worker.use(
			http.post('/catalog/*', ({ request }) => {
				const url = new URL(request.url);
				importedApiId = decodeURIComponent(
					url.pathname.replace(/^\/catalog\//, ''),
				).replace(/:import$/, '');
				return HttpResponse.json(
					{
						job_id: 'job_reimport',
						status: 'queued',
						_links: { self: '/jobs/job_reimport' },
					},
					{ status: 202 },
				);
			}),
		);

		renderWithProviders(
			<>
				<OverviewStrip api={makeApi({ updateAvailable: true, origin: 'catalog' })} />
				<Toaster />
			</>,
		);

		await user.click(screen.getByTestId('workspace-reimport'));

		// The catalog api_id threaded is the API's vendor (manifest domain).
		await waitFor(() => expect(importedApiId).toBe('stripe.com'));
		expect(await screen.findByText('Re-import started')).toBeInTheDocument();
	});
});
