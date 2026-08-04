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
		catalogApiId: null,
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

	it('enables the Re-import button and routes overlay-origin APIs through a confirm dialog', async () => {
		const user = userEvent.setup();
		let importedApiId: string | null = null;
		worker.use(
			http.post('/catalog/*', ({ request }) => {
				const url = new URL(request.url);
				importedApiId = decodeURIComponent(
					url.pathname.replace(/^\/catalog\//, ''),
				).replace(/:import$/, '');
				return HttpResponse.json(
					{ job_id: 'job_reimport', status: 'queued' },
					{ status: 202 },
				);
			}),
		);

		renderWithProviders(
			<>
				<OverviewStrip
					api={makeApi({
						updateAvailable: true,
						origin: 'overlay',
						catalogApiId: 'stripe.com',
					})}
				/>
				<Toaster />
			</>,
		);

		// Overlay-origin now shows an ENABLED Re-import button (no tooltip lock).
		const reimport = screen.getByTestId('workspace-reimport');
		expect(reimport).toBeEnabled();

		// Clicking it opens the confirm dialog — the re-import must NOT fire yet.
		await user.click(reimport);
		const confirm = await screen.findByTestId('workspace-reimport-confirm');
		expect(importedApiId).toBeNull();

		// Confirming triggers the re-import.
		await user.click(confirm);
		await waitFor(() => expect(importedApiId).toBe('stripe.com'));
		expect(await screen.findByText('Re-import started')).toBeInTheDocument();
	});

	it('mutes update notifications via the snooze endpoint on click', async () => {
		const user = userEvent.setup();
		let snoozedApiId: string | null = null;
		worker.use(
			http.post('/catalog/*', ({ request }) => {
				const url = new URL(request.url);
				const path = decodeURIComponent(url.pathname.replace(/^\/catalog\//, ''));
				if (path.endsWith(':snooze')) {
					snoozedApiId = path.replace(/:snooze$/, '');
					return HttpResponse.json({}, { status: 200 });
				}
				return HttpResponse.json({ detail: 'unexpected' }, { status: 400 });
			}),
		);

		renderWithProviders(
			<>
				<OverviewStrip
					api={makeApi({
						updateAvailable: true,
						origin: 'catalog',
						catalogApiId: 'stripe.com',
					})}
				/>
				<Toaster />
			</>,
		);

		await user.click(screen.getByTestId('workspace-snooze'));
		await waitFor(() => expect(snoozedApiId).toBe('stripe.com'));
		expect(await screen.findByText('Updates muted')).toBeInTheDocument();
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

	it('threads the umbrella catalog_api_id (domain/sub), not the vendor, on re-import', async () => {
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
				<OverviewStrip
					api={makeApi({
						api: {
							vendor: 'nytimes.com',
							name: 'article-search',
							version: '1',
							host: null,
						},
						updateAvailable: true,
						origin: 'catalog',
						catalogApiId: 'nytimes.com/article_search',
					})}
				/>
				<Toaster />
			</>,
		);

		await user.click(screen.getByTestId('workspace-reimport'));

		// The full umbrella id is used — the bare vendor would 404 the re-import.
		await waitFor(() => expect(importedApiId).toBe('nytimes.com/article_search'));
		expect(await screen.findByText('Re-import started')).toBeInTheDocument();
	});
});
