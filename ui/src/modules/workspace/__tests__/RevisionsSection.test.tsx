import { describe, it, expect, afterEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import userEvent from '@testing-library/user-event';
import { renderWithProviders, screen, waitFor } from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { RevisionsSection } from '@/modules/workspace/components/RevisionsSection';
import type { ApiKey } from '@/modules/workspace/api';

const KEY: ApiKey = { vendor: 'stripe.com', name: 'stripe-api', version: '1' };

/** Raw revision wire row (snake_case), as the backend emits it. */
function rawRevision(overrides: Record<string, unknown> = {}) {
	const base = {
		revision_id: 'dc9bcdeb-f652-45eb-b7cb-d56e76a5d8aa',
		api: { vendor: KEY.vendor, name: KEY.name, version: KEY.version, host: null },
		source: { type: 'inline', url: null, submitted_by: null },
		spec_digest: 'digest',
		operation_count: 19,
		state: 'imported',
		origin: null,
		submitted_by: null,
		is_current: false,
		promoted_at: null,
		archived_at: null,
		created_at: '2026-08-07T09:40:39Z',
		...overrides,
	};
	return {
		_links: { self: '/x', api: '/y', promote: null, archive: null },
		...base,
	};
}

function mockRevisions(rows: Record<string, unknown>[], overlays: Record<string, unknown>[] = []) {
	worker.use(
		http.get('/apis/:vendor/:name/:version/revisions', () =>
			HttpResponse.json({ data: rows, has_more: false, next_cursor: null }),
		),
		http.get('/apis/:vendor/:name/:version/overlays', () =>
			HttpResponse.json({ data: overlays, has_more: false, next_cursor: null }),
		),
	);
}

describe('RevisionsSection', () => {
	afterEach(() => {
		worker.resetHandlers();
	});

	it('renders origin, capitalized state, live badge, and the change summary', async () => {
		mockRevisions([
			rawRevision({
				revision_id: 'aaaa1111-0000-4000-8000-000000000001',
				is_current: true,
				operation_count: 21,
				created_at: '2026-08-07T10:00:00Z',
			}),
			rawRevision({
				revision_id: 'bbbb2222-0000-4000-8000-000000000002',
				state: 'archived',
				operation_count: 19,
				created_at: '2026-08-07T09:00:00Z',
			}),
		]);
		renderWithProviders(<RevisionsSection apiKey={KEY} />);

		await waitFor(() => expect(screen.getAllByTestId('revision-row')).toHaveLength(2));
		// Wire enum is displayed capitalized, not raw.
		expect(screen.getByText('Imported')).toBeInTheDocument();
		expect(screen.getByText('Archived')).toBeInTheDocument();
		expect(screen.getByText('Live')).toBeInTheDocument();
		// The live row's summary compares against the row below it.
		const summaries = screen.getAllByTestId('revision-summary');
		expect(summaries[0]).toHaveTextContent('21 operations (+2 vs previous)');
		expect(summaries[1]).toHaveTextContent('19 operations');
	});

	it('says "same count" (not "unchanged") when the operation count is flat', async () => {
		mockRevisions([
			rawRevision({
				revision_id: 'aaaa1111-0000-4000-8000-000000000001',
				operation_count: 19,
				created_at: '2026-08-07T10:00:00Z',
			}),
			rawRevision({
				revision_id: 'bbbb2222-0000-4000-8000-000000000002',
				operation_count: 19,
				created_at: '2026-08-07T09:00:00Z',
			}),
		]);
		renderWithProviders(<RevisionsSection apiKey={KEY} />);

		await waitFor(() => expect(screen.getAllByTestId('revision-row')).toHaveLength(2));
		expect(screen.getAllByTestId('revision-summary')[0]).toHaveTextContent(
			'19 operations (same count)',
		);
	});

	it('cross-links an overlay-origin revision to the producing overlay', async () => {
		mockRevisions(
			[
				rawRevision({
					revision_id: 'cccc3333-0000-4000-8000-000000000003',
					origin: 'overlay',
					created_at: '2026-08-07T10:00:00Z',
				}),
			],
			[
				{
					id: 'ovr_6a75aa8e6edd9723f71840e8',
					status: 'confirmed',
					confirmed_revision_id: 'cccc3333-0000-4000-8000-000000000003',
					created_at: '2026-08-07T09:50:00Z',
					document: null,
					_links: {
						self: '/x',
						api: '/y',
						confirm: null,
						rollback: null,
						deprecate: null,
					},
				},
			],
		);
		renderWithProviders(<RevisionsSection apiKey={KEY} />);

		const link = await screen.findByTestId('revision-origin-overlay');
		expect(link).toHaveTextContent('overlay ovr_…1840e8');
	});

	it('labels the row action "Diff" when a previous revision exists and "View spec" for the first', async () => {
		mockRevisions([
			rawRevision({
				revision_id: 'aaaa1111-0000-4000-8000-000000000001',
				created_at: '2026-08-07T10:00:00Z',
			}),
			rawRevision({
				revision_id: 'bbbb2222-0000-4000-8000-000000000002',
				created_at: '2026-08-07T09:00:00Z',
			}),
		]);
		renderWithProviders(<RevisionsSection apiKey={KEY} />);

		await waitFor(() => expect(screen.getAllByTestId('revision-row')).toHaveLength(2));
		const buttons = screen.getAllByTestId('revision-view-spec');
		// Newest row has a base (the row below) → Diff; the API's first-ever
		// revision has nothing to compare against → honest "View spec" label.
		expect(buttons[0]).toHaveTextContent('Diff');
		expect(buttons[1]).toHaveTextContent('View spec');
	});

	it('opens the spec dialog diffing against the SAME previous revision the summary uses', async () => {
		mockRevisions([
			rawRevision({
				revision_id: 'aaaa1111-0000-4000-8000-000000000001',
				is_current: true,
				created_at: '2026-08-07T10:00:00Z',
			}),
			rawRevision({
				revision_id: 'bbbb2222-0000-4000-8000-000000000002',
				state: 'archived',
				created_at: '2026-08-07T09:00:00Z',
			}),
		]);
		worker.use(
			http.get('/apis/:vendor/:name/:version/revisions/:revisionId/openapi', ({ params }) =>
				HttpResponse.json({
					openapi: '3.1.0',
					servers: [{ url: `https://${params.revisionId as string}.example` }],
				}),
			),
		);
		renderWithProviders(<RevisionsSection apiKey={KEY} />);

		await waitFor(() => expect(screen.getAllByTestId('revision-row')).toHaveLength(2));
		await userEvent.click(screen.getAllByTestId('revision-view-spec')[0]);

		// The toggle names the base: previous · bbbb2222 (not "live").
		expect(
			await screen.findByRole('tab', { name: /Diff vs previous · bbbb2222/ }),
		).toBeVisible();
		// The structural diff surfaces the changed servers block.
		await waitFor(() =>
			expect(screen.getAllByTestId('spec-diff-entry').length).toBeGreaterThan(0),
		);
		expect(screen.getByText('$.servers')).toBeInTheDocument();
	});

	it('surfaces an error when the list request fails', async () => {
		worker.use(
			http.get('/apis/:vendor/:name/:version/revisions', () =>
				HttpResponse.json({ detail: 'boom' }, { status: 500 }),
			),
		);
		renderWithProviders(<RevisionsSection apiKey={KEY} />);
		expect(await screen.findByText(/Failed to load revisions|boom/)).toBeInTheDocument();
	});
});
