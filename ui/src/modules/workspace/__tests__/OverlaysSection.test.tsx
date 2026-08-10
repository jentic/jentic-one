import { describe, it, expect, afterEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import userEvent from '@testing-library/user-event';
import { renderWithProviders, screen, waitFor } from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { OverlaysSection } from '@/modules/workspace/components/OverlaysSection';
import type { ApiKey } from '@/modules/workspace/api';

const KEY: ApiKey = { vendor: 'stripe.com', name: 'stripe-api', version: '1' };

/** Raw overlay wire row (snake_case), as the backend emits it. */
function rawOverlay(overrides: Record<string, unknown> = {}) {
	const base = {
		id: 'ovl_abcdef123456',
		status: 'pending',
		created_by: 'usr_author_1',
		contributed_by: null,
		document: null,
		created_at: '2026-01-01T00:00:00Z',
		confirmed_at: null,
		deprecated_at: null,
		deprecated_reason: null,
		target_revision_id: 'rev_1',
		confirmed_revision_id: null,
		superseded_revision_id: null,
		...overrides,
	};
	// Mirror the backend's state-validity `_links` (see OverlayLinksResponse) so the
	// section's action gating is exercised as it is in production. An explicit
	// `_links` override still wins.
	const self = `/apis/acme/pets/v1/overlays/${base.id}`;
	const isPending = base.status === 'pending';
	const isMaterialized = base.status === 'confirmed' && base.confirmed_revision_id != null;
	const isDeprecated = base.status === 'deprecated';
	return {
		_links: {
			self,
			api: '/apis/acme/pets/v1',
			confirm: isPending ? `${self}:confirm` : null,
			rollback: isMaterialized ? `${self}:rollback` : null,
			deprecate: isDeprecated ? null : self,
		},
		...base,
	};
}

function mockOverlays(rows: Record<string, unknown>[]) {
	worker.use(
		http.get('/apis/:vendor/:name/:version/overlays', () =>
			HttpResponse.json({ data: rows, has_more: false, next_cursor: null }),
		),
	);
}

describe('OverlaysSection', () => {
	afterEach(() => {
		worker.resetHandlers();
	});

	it('renders the empty state when the API has no overlays', async () => {
		mockOverlays([]);
		renderWithProviders(<OverlaysSection apiKey={KEY} />);
		expect(await screen.findByText(/Overlays are reviewed spec fixes/)).toBeInTheDocument();
		expect(screen.getByTestId('overlays-section')).toBeInTheDocument();
	});

	it('lists overlays with a status badge', async () => {
		mockOverlays([
			rawOverlay({ id: 'ovl_pending01', status: 'pending' }),
			rawOverlay({
				id: 'ovl_confirmed',
				status: 'confirmed',
				confirmed_at: '2026-02-01T00:00:00Z',
			}),
		]);
		renderWithProviders(<OverlaysSection apiKey={KEY} />);

		await waitFor(() => expect(screen.getAllByTestId('overlay-row')).toHaveLength(2));
		expect(screen.getByTestId('overlay-status-pending')).toBeInTheDocument();
		expect(screen.getByTestId('overlay-status-confirmed')).toBeInTheDocument();
	});

	it('shows a dated "Superseded" note for a deprecated overlay', async () => {
		mockOverlays([
			rawOverlay({
				id: 'ovl_deprecated',
				status: 'deprecated',
				confirmed_at: '2026-03-01T00:00:00Z',
				deprecated_at: '2026-03-15T09:30:00Z',
			}),
		]);
		renderWithProviders(<OverlaysSection apiKey={KEY} />);

		expect(await screen.findByTestId('overlay-status-deprecated')).toBeInTheDocument();
		expect(screen.getByText(/Superseded/)).toBeInTheDocument();
	});

	it('badges a rollback as "rolled back" when the superseded revision serves again', async () => {
		mockOverlays([
			rawOverlay({
				id: 'ovl_rolledback',
				status: 'deprecated',
				confirmed_revision_id: 'rev_overlay',
				superseded_revision_id: 'rev_base',
				confirmed_at: '2026-03-01T00:00:00Z',
				deprecated_at: '2026-03-15T09:30:00Z',
			}),
		]);
		renderWithProviders(<OverlaysSection apiKey={KEY} currentRevisionId="rev_base" />);

		const badge = await screen.findByTestId('overlay-status-deprecated');
		expect(badge).toHaveAttribute('data-lifecycle', 'rolled-back');
		expect(badge).toHaveTextContent('rolled back');
		expect(screen.getByText(/Rolled back/)).toBeInTheDocument();
	});

	it('badges a serving overlay as "active" (distinct from confirmed)', async () => {
		mockOverlays([
			rawOverlay({
				id: 'ovl_serving',
				status: 'confirmed',
				confirmed_revision_id: 'rev_live',
				confirmed_at: '2026-02-01T00:00:00Z',
			}),
		]);
		renderWithProviders(<OverlaysSection apiKey={KEY} currentRevisionId="rev_live" />);

		const badge = await screen.findByTestId('overlay-status-confirmed');
		expect(badge).toHaveAttribute('data-lifecycle', 'active');
	});

	it('renders unique short ids for KSUIDs sharing a time prefix, with copyable full ids', async () => {
		// These two collided as "ovr_6a75" under the old slice(0, 8) rendering.
		mockOverlays([
			rawOverlay({ id: 'ovr_6a75aa8e6edd9723f71840e8' }),
			rawOverlay({ id: 'ovr_6a75aaf71c1c073e38ab429c' }),
		]);
		renderWithProviders(<OverlaysSection apiKey={KEY} />);

		await waitFor(() => expect(screen.getAllByTestId('overlay-row')).toHaveLength(2));
		// textContent includes the sr-only "(full id …)" suffix; the visible
		// part is the short form, which must be unique per overlay.
		const ids = screen.getAllByTestId('overlay-id').map((el) => el.textContent ?? '');
		expect(ids[0]).toMatch(/^ovr_…1840e8/);
		expect(ids[1]).toMatch(/^ovr_…ab429c/);
		expect(new Set(ids).size).toBe(2);
		// The full id stays reachable: on hover (title) and via the copy button.
		expect(screen.getByTitle('ovr_6a75aa8e6edd9723f71840e8')).toBeInTheDocument();
		expect(screen.getAllByLabelText(/Copy full id of overlay/)).toHaveLength(2);
	});

	it('summarizes the overlay document actions and shows the attribution', async () => {
		mockOverlays([
			rawOverlay({
				id: 'ovl_described',
				contributed_by: 'contribute-spec-fix skill',
				document: {
					overlay: '1.0.0',
					actions: [
						{
							description: 'Remove the US-only servers block.',
							target: '$.servers',
							remove: true,
						},
						{ target: '$.info', update: { title: 'New title' } },
					],
				},
			}),
		]);
		renderWithProviders(<OverlaysSection apiKey={KEY} />);

		const summary = await screen.findByTestId('overlay-summary');
		expect(summary).toHaveTextContent('Remove the US-only servers block.');
		expect(summary).toHaveTextContent('Updated info (title)');
		expect(screen.getByTestId('overlay-meta')).toHaveTextContent(
			/via contribute-spec-fix skill/,
		);
	});

	it('links a materialized overlay to the revision it produced', async () => {
		mockOverlays([
			rawOverlay({
				id: 'ovl_confirmed',
				status: 'confirmed',
				confirmed_at: '2026-02-01T00:00:00Z',
				confirmed_revision_id: 'dc9bcdeb-f652-45eb-b7cb-d56e76a5d8aa',
			}),
		]);
		renderWithProviders(<OverlaysSection apiKey={KEY} />);

		const link = await screen.findByTestId('overlay-produced-revision');
		expect(link).toHaveTextContent('revision dc9bcdeb');
	});

	it('surfaces an error when the list request fails', async () => {
		worker.use(
			http.get('/apis/:vendor/:name/:version/overlays', () =>
				HttpResponse.json({ detail: 'boom' }, { status: 500 }),
			),
		);
		renderWithProviders(<OverlaysSection apiKey={KEY} />);
		expect(await screen.findByText(/Failed to load overlays|boom/)).toBeInTheDocument();
	});

	it('shows confirm + deprecate on a pending overlay and none on a deprecated one', async () => {
		mockOverlays([
			rawOverlay({ id: 'ovl_pending01', status: 'pending' }),
			rawOverlay({
				id: 'ovl_deprecated',
				status: 'deprecated',
				deprecated_at: '2026-03-15T09:30:00Z',
			}),
		]);
		renderWithProviders(<OverlaysSection apiKey={KEY} />);

		await waitFor(() => expect(screen.getAllByTestId('overlay-row')).toHaveLength(2));
		// Exactly one pending overlay → one confirm + one deprecate button; the
		// deprecated row exposes no lifecycle actions.
		expect(screen.getAllByTestId('overlay-confirm')).toHaveLength(1);
		expect(screen.getAllByTestId('overlay-deprecate')).toHaveLength(1);
		expect(screen.queryByTestId('overlay-rollback')).not.toBeInTheDocument();
	});

	it('confirms a pending overlay via POST …:confirm', async () => {
		let confirmed = false;
		mockOverlays([rawOverlay({ id: 'ovl_pending01', status: 'pending' })]);
		worker.use(
			http.post('/apis/:vendor/:name/:version/overlays/:id\\:confirm', () => {
				confirmed = true;
				return new HttpResponse(null, { status: 204 });
			}),
		);
		renderWithProviders(<OverlaysSection apiKey={KEY} />);

		await userEvent.click(await screen.findByTestId('overlay-confirm'));
		await waitFor(() => expect(confirmed).toBe(true));
	});

	it('refetches the overlay list after a confirm (cache invalidation)', async () => {
		let listFetches = 0;
		worker.use(
			http.get('/apis/:vendor/:name/:version/overlays', () => {
				listFetches += 1;
				// Report the overlay as still pending on every fetch so the confirm
				// button stays present; we only care that the list is re-queried.
				return HttpResponse.json({
					data: [rawOverlay({ id: 'ovl_pending01', status: 'pending' })],
					has_more: false,
					next_cursor: null,
				});
			}),
			http.post(
				'/apis/:vendor/:name/:version/overlays/:id\\:confirm',
				() => new HttpResponse(null, { status: 204 }),
			),
		);
		renderWithProviders(<OverlaysSection apiKey={KEY} />);

		await screen.findByTestId('overlay-confirm');
		await waitFor(() => expect(listFetches).toBe(1));

		await userEvent.click(screen.getByTestId('overlay-confirm'));
		// The action's onSuccess invalidates workspaceKeys.overlays(key), which
		// forces the list query to re-run — so we see a second fetch.
		await waitFor(() => expect(listFetches).toBeGreaterThanOrEqual(2));
	});

	it('rolls back a confirmed overlay only after the confirm dialog', async () => {
		let rolledBack = false;
		mockOverlays([
			rawOverlay({
				id: 'ovl_confirmed',
				status: 'confirmed',
				confirmed_at: '2026-02-01T00:00:00Z',
				confirmed_revision_id: 'rev_materialized',
			}),
		]);
		worker.use(
			http.post('/apis/:vendor/:name/:version/overlays/:id\\:rollback', () => {
				rolledBack = true;
				return new HttpResponse(null, { status: 204 });
			}),
		);
		// Roll back is only offered while the overlay's revision is CURRENT.
		renderWithProviders(<OverlaysSection apiKey={KEY} currentRevisionId="rev_materialized" />);

		// Clicking "Roll back" opens the confirm dialog but does NOT fire the request yet.
		await userEvent.click(await screen.findByTestId('overlay-rollback'));
		expect(rolledBack).toBe(false);
		// Confirming in the dialog fires the rollback.
		await userEvent.click(await screen.findByTestId('overlay-rollback-confirm'));
		await waitFor(() => expect(rolledBack).toBe(true));
	});

	it('hides Roll back on a superseded overlay (the backend would 409)', async () => {
		mockOverlays([
			rawOverlay({
				id: 'ovl_superseded',
				status: 'confirmed',
				confirmed_at: '2026-02-01T00:00:00Z',
				confirmed_revision_id: 'rev_materialized',
			}),
		]);
		// A newer revision is current — the backend still advertises the
		// rollback link, but the service refuses unless the overlay is live.
		renderWithProviders(<OverlaysSection apiKey={KEY} currentRevisionId="rev_newer" />);

		const badge = await screen.findByTestId('overlay-status-confirmed');
		expect(badge).toHaveAttribute('data-lifecycle', 'superseded');
		expect(screen.queryByTestId('overlay-rollback')).not.toBeInTheDocument();
	});

	it('flags a deprecated overlay whose revision still serves', async () => {
		mockOverlays([
			rawOverlay({
				id: 'ovl_depserving',
				status: 'deprecated',
				confirmed_at: '2026-02-01T00:00:00Z',
				confirmed_revision_id: 'rev_materialized',
				deprecated_at: '2026-03-15T09:30:00Z',
				deprecated_reason: 'manual',
			}),
		]);
		renderWithProviders(<OverlaysSection apiKey={KEY} currentRevisionId="rev_materialized" />);

		const badge = await screen.findByTestId('overlay-status-deprecated');
		expect(badge).toHaveAttribute('data-lifecycle', 'deprecated-serving');
		expect(screen.getByTestId('overlay-meta')).toHaveTextContent(/still serving/);
	});

	it('caps long action summaries with a "+N more actions" line', async () => {
		mockOverlays([
			rawOverlay({
				id: 'ovl_manyactions',
				document: {
					overlay: '1.0.0',
					actions: Array.from({ length: 6 }, (_, i) => ({
						description: `Action number ${i + 1}.`,
					})),
				},
			}),
		]);
		renderWithProviders(<OverlaysSection apiKey={KEY} />);

		const summary = await screen.findByTestId('overlay-summary');
		expect(summary).toHaveTextContent('Action number 3.');
		expect(summary).not.toHaveTextContent('Action number 4.');
		expect(screen.getByTestId('overlay-summary-more')).toHaveTextContent('+3 more actions');
	});
});
