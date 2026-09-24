import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderWithProviders, screen, userEvent, waitFor } from '@/__tests__/test-utils';
import { ApiPicker } from '@/shared/credentials/components/ApiPicker';
import {
	makeMockApi,
	makeMockCatalogEntry,
	resetApisStore,
} from '@/shared/credentials/mocks/handlers';
import { seedFormFromSelectedApi } from '@/shared/credentials/lib/formBody';
import { EMPTY_FORM } from '@/shared/credentials/components/CredentialTypeFields';
import type { SelectedApi } from '@/shared/credentials/api';

describe('ApiPicker', () => {
	beforeEach(() => resetApisStore());
	afterEach(() => resetApisStore());

	it('lists workspace APIs and emits a SelectedApi on click', async () => {
		resetApisStore([makeMockApi({ vendor: 'stripe', name: 'main', displayName: 'Stripe' })]);
		const onSelect = vi.fn();
		const onManualEntry = vi.fn();
		renderWithProviders(<ApiPicker onSelect={onSelect} onManualEntry={onManualEntry} />);

		const row = await screen.findByText('Stripe');
		expect(screen.getByText('In your workspace')).toBeInTheDocument();
		await userEvent.click(row);

		expect(onSelect).toHaveBeenCalledTimes(1);
		const selected = onSelect.mock.calls[0][0] as SelectedApi;
		expect(selected.source).toBe('local');
		expect(selected.vendor).toBe('stripe');
		expect(selected.label).toBe('Stripe');
	});

	it('searches the catalog when the user types', async () => {
		resetApisStore(
			[],
			[
				{
					entry: makeMockCatalogEntry({ apiId: 'github.com', vendor: 'github' }).entry,
				},
				{
					entry: makeMockCatalogEntry({ apiId: 'stripe.com', vendor: 'stripe' }).entry,
				},
			],
		);
		const onSelect = vi.fn();
		renderWithProviders(<ApiPicker onSelect={onSelect} onManualEntry={vi.fn()} />);

		await userEvent.type(screen.getByLabelText('Search APIs'), 'github');

		// The catalog row titles from the `api_id` slug exactly like Discover
		// (#910): a bare-domain entry reads `github.com` verbatim, so the same
		// API can't render one way on Discover and another way here. The row
		// shows the slug as both title and mono subtitle, so scope the lookup
		// to the title span.
		const rows = await screen.findAllByText('github.com', {}, { timeout: 3000 });
		expect(screen.getByText('From the Jentic public catalog')).toBeInTheDocument();
		await userEvent.click(rows[0]);

		expect(onSelect).toHaveBeenCalledTimes(1);
		const selected = onSelect.mock.calls[0][0] as SelectedApi;
		expect(selected.source).toBe('catalog');
		// Server-supplied vendor wins over the `api_id` slug so the stored
		// vendor is the canonical `github`, not `github.com`.
		expect(selected.vendor).toBe('github');
		expect(selected.apiId).toBe('github.com');
		expect(selected.specUrl).toContain('mock-spec.test');
	});

	it('locks the saved credential name to the friendly label after a catalog pick', async () => {
		// Regression lock: the create-credential dialog pre-fills its Name field
		// from the picked API's `label` (via `seedFormFromSelectedApi`, when the
		// user hasn't edited the name). The label MUST equal the friendly title
		// the row displays — otherwise the saved credential silently drifts from
		// what the user saw. This asserts the whole chain: pick → SelectedApi →
		// default saved name. A sub-API entry titles from the slug's sub
		// segment (`Article Search`), exactly like Discover.
		resetApisStore(
			[],
			[
				{
					entry: makeMockCatalogEntry({
						apiId: 'nytimes.com/article_search',
						vendor: 'nytimes.com',
					}).entry,
				},
			],
		);
		const onSelect = vi.fn();
		renderWithProviders(<ApiPicker onSelect={onSelect} onManualEntry={vi.fn()} />);

		await userEvent.type(screen.getByLabelText('Search APIs'), 'nytimes');
		const row = await screen.findByText('Article Search', {}, { timeout: 3000 });
		await userEvent.click(row);

		const selected = onSelect.mock.calls[0][0] as SelectedApi;
		expect(selected.label).toBe('Article Search');

		// Seed a fresh form with an un-edited name (nameDirty = false): the
		// default saved name is the friendly label, not the raw slug/vendor —
		// and the picked slug rides along to be stored on the credential (#910).
		const seeded = seedFormFromSelectedApi(EMPTY_FORM, selected, false);
		expect(seeded.name).toBe('Article Search');
		expect(seeded.name).toBe(selected.label);
		expect(seeded.catalogApiId).toBe('nytimes.com/article_search');
	});

	it('stores the server-supplied vendor and dedups against a workspace row', async () => {
		// A catalog entry `{api_id:'github.com', vendor:'github'}` must resolve
		// vendor='github' (not 'github.com') so it dedups against a workspace
		// `github/main` row rather than showing as a duplicate-looking catalog
		// result — and so the persisted vendor doesn't drift to `github.com`.
		resetApisStore(
			[makeMockApi({ vendor: 'github', name: 'main', displayName: 'GitHub' })],
			[{ entry: makeMockCatalogEntry({ apiId: 'github.com', vendor: 'github' }).entry }],
		);
		const onSelect = vi.fn();
		renderWithProviders(<ApiPicker onSelect={onSelect} onManualEntry={vi.fn()} />);

		await userEvent.type(screen.getByLabelText('Search APIs'), 'github');

		// Workspace row shows; the catalog section must NOT list a duplicate
		// github row (deduped on the resolved `github/main` key).
		await screen.findByText('GitHub');
		expect(screen.queryByText('From the Jentic public catalog')).not.toBeInTheDocument();
	});

	it('shows the empty state and a manual-entry escape for unmatched queries', async () => {
		resetApisStore();
		const onManualEntry = vi.fn();
		renderWithProviders(<ApiPicker onSelect={vi.fn()} onManualEntry={onManualEntry} />);

		await userEvent.type(screen.getByLabelText('Search APIs'), 'nothing-matches');
		await screen.findByText('No APIs found');
		await userEvent.click(screen.getByRole('button', { name: /Enter manually/i }));
		await waitFor(() => expect(onManualEntry).toHaveBeenCalled());
	});
});
