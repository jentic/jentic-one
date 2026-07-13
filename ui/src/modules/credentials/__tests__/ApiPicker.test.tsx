import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderWithProviders, screen, userEvent, waitFor } from '@/__tests__/test-utils';
import { ApiPicker } from '@/modules/credentials/components/ApiPicker';
import {
	makeMockApi,
	makeMockCatalogEntry,
	resetApisStore,
} from '@/modules/credentials/mocks/handlers';
import type { SelectedApi } from '@/modules/credentials/api';

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

		const row = await screen.findByText('github.com', {}, { timeout: 3000 });
		expect(screen.getByText('From the Jentic public catalog')).toBeInTheDocument();
		await userEvent.click(row);

		expect(onSelect).toHaveBeenCalledTimes(1);
		const selected = onSelect.mock.calls[0][0] as SelectedApi;
		expect(selected.source).toBe('catalog');
		expect(selected.apiId).toBe('github.com');
		expect(selected.specUrl).toContain('mock-spec.test');
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
