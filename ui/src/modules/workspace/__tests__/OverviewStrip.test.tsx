import { describe, it, expect, afterEach, beforeEach } from 'vitest';
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
	beforeEach(() => {
		// The re-import hook now polls /jobs/{id} to a terminal state before it
		// invalidates (so the "Update available" badge only clears once the new
		// revision has actually landed). Default the poll to a completed job so the
		// background loop resolves cleanly; individual tests can override.
		worker.use(
			http.get('/jobs/:id', ({ params }) =>
				HttpResponse.json({ job_id: params.id, status: 'succeeded' }),
			),
		);
	});

	afterEach(() => {
		clearAllToasts();
		worker.resetHandlers();
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
		// Await the async poll's completion toast so the state update settles inside act().
		expect(await screen.findByText('Re-import complete')).toBeInTheDocument();
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
		expect(await screen.findByText('Re-import complete')).toBeInTheDocument();
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
		expect(await screen.findByText('Re-import complete')).toBeInTheDocument();
	});

	it('polls the re-import job to completion before signalling done (badge clears only after the revision lands)', async () => {
		const user = userEvent.setup();
		let jobPolled = false;
		worker.use(
			http.post('/catalog/*', () =>
				HttpResponse.json({ job_id: 'job_reimport', status: 'queued' }, { status: 202 }),
			),
			// The job stays queued on the first poll and only then succeeds, proving
			// the hook waits for a terminal state rather than acting on the 202.
			http.get('/jobs/:id', ({ params }) => {
				const status = jobPolled ? 'succeeded' : 'queued';
				jobPolled = true;
				return HttpResponse.json({ job_id: params.id, status });
			}),
		);

		renderWithProviders(
			<>
				<OverviewStrip api={makeApi({ updateAvailable: true, origin: 'catalog' })} />
				<Toaster />
			</>,
		);

		await user.click(screen.getByTestId('workspace-reimport'));
		// Queued ack fires immediately…
		expect(await screen.findByText('Re-import started')).toBeInTheDocument();
		// …and the completion toast only after the job reaches a terminal state,
		// which is also when the API/revision caches are invalidated.
		expect(await screen.findByText('Re-import complete')).toBeInTheDocument();
		expect(jobPolled).toBe(true);
	});
});
