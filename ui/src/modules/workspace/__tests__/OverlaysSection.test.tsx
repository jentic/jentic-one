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
	return {
		id: 'ovl_abcdef123456',
		status: 'pending',
		created_by: 'alice@acme.dev',
		created_at: '2026-01-01T00:00:00Z',
		confirmed_at: null,
		deprecated_at: null,
		target_revision_id: 'rev_1',
		confirmed_revision_id: null,
		...overrides,
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
		expect(await screen.findByText('No overlays for this API.')).toBeInTheDocument();
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

	it('shows the "Deprecated by re-import" note for a deprecated overlay', async () => {
		mockOverlays([
			rawOverlay({
				id: 'ovl_deprecated',
				status: 'deprecated',
				deprecated_at: '2026-03-15T09:30:00Z',
			}),
		]);
		renderWithProviders(<OverlaysSection apiKey={KEY} />);

		expect(await screen.findByTestId('overlay-status-deprecated')).toBeInTheDocument();
		expect(screen.getByText(/Deprecated by re-import on/)).toBeInTheDocument();
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

	it('rolls back a confirmed overlay only after the confirm dialog', async () => {
		let rolledBack = false;
		mockOverlays([
			rawOverlay({
				id: 'ovl_confirmed',
				status: 'confirmed',
				confirmed_at: '2026-02-01T00:00:00Z',
			}),
		]);
		worker.use(
			http.post('/apis/:vendor/:name/:version/overlays/:id\\:rollback', () => {
				rolledBack = true;
				return new HttpResponse(null, { status: 204 });
			}),
		);
		renderWithProviders(<OverlaysSection apiKey={KEY} />);

		// Clicking "Roll back" opens the confirm dialog but does NOT fire the request yet.
		await userEvent.click(await screen.findByTestId('overlay-rollback'));
		expect(rolledBack).toBe(false);
		// Confirming in the dialog fires the rollback.
		await userEvent.click(await screen.findByTestId('overlay-rollback-confirm'));
		await waitFor(() => expect(rolledBack).toBe(true));
	});
});
