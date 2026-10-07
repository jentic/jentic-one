/**
 * CredentialGroupCard — the header names only what every row shares, and each
 * row states its own version pin ("any version" when unpinned), so a group of
 * one API's revisions never hides a pin behind the first row's identity.
 */
import { describe, expect, it, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { worker } from '@/mocks/browser';
import { renderWithProviders, screen, waitFor, within } from '@/__tests__/test-utils';
import { CredentialsList } from '@/shared/credentials/components/CredentialsList';
import { makeMockCredential } from '@/shared/credentials/mocks/handlers';
import { CredentialGroupCard } from '@/shared/credentials/components/CredentialGroupCard';
import type { Credential } from '@/shared/credentials/api';

function renderGroup(credentials: Credential[]) {
	return renderWithProviders(
		<CredentialGroupCard
			credentials={credentials}
			onEdit={vi.fn()}
			onDelete={vi.fn()}
			onConnect={vi.fn()}
			usageFor={() => ({ usedByAgentCount: null, callsLast7d: null })}
		/>,
	);
}

describe('CredentialGroupCard', () => {
	it('shows each row’s pinned version, or "any version"', () => {
		renderGroup([
			makeMockCredential({
				name: 'Pinned',
				api: { vendor: 'slack.com', name: 'web', version: '2.0.0' },
			}),
			makeMockCredential({
				name: 'Unpinned',
				api: { vendor: 'slack.com', name: 'web', version: '' },
			}),
		]);

		const group = screen.getByTestId('credential-group');
		const rows = within(group).getAllByTestId('credential-card');
		expect(within(rows[0]).getByTestId('credential-row-api')).toHaveTextContent('v2.0.0');
		expect(within(rows[1]).getByTestId('credential-row-api')).toHaveTextContent('any version');
		// The header never borrows the first row's version.
		const header = within(group).getByRole('heading', { level: 3 }).closest('header');
		expect(header).not.toHaveTextContent('2.0.0');
	});

	it('falls back to a neutral vendor header when the rows name different APIs', () => {
		renderGroup([
			makeMockCredential({
				name: 'Payments key',
				catalog_api_id: 'example.co.uk/payments',
				api: { vendor: 'example.co.uk', name: 'payments', version: '' },
			}),
			makeMockCredential({
				name: 'Accounts key',
				catalog_api_id: 'example.co.uk/accounts',
				api: { vendor: 'example.co.uk', name: 'accounts', version: '1' },
			}),
		]);

		const group = screen.getByTestId('credential-group');
		expect(within(group).getByRole('heading', { level: 3 })).toHaveTextContent(
			/^example\.co\.uk$/,
		);
		expect(group).toHaveTextContent('2 APIs');
		// Each row names its own API instead.
		const rows = within(group).getAllByTestId('credential-row-api');
		expect(rows[0]).toHaveTextContent('example.co.uk/payments · any version');
		expect(rows[1]).toHaveTextContent('example.co.uk/accounts · v1');
	});
});

describe('CredentialsList — group titles', () => {
	it('titles a group by the imported API’s display name, as the hub does', async () => {
		worker.use(
			http.get('/apis', () =>
				HttpResponse.json({
					data: [
						{
							api: { vendor: 'github', name: 'github-api', version: '1.1.4' },
							catalog_api_id: 'github.com',
							display_name: 'GitHub',
							description: null,
							icon_url: null,
							current_revision_id: 'rev_1',
							revision_count: 1,
							operation_count: 3,
							security_schemes: ['bearer'],
							created_at: '2026-01-01T00:00:00Z',
							updated_at: '2026-01-01T00:00:00Z',
							_links: {},
						},
					],
					has_more: false,
					next_cursor: null,
				}),
			),
		);
		const api = { vendor: 'github', name: 'github-api', version: '' };
		renderWithProviders(
			<CredentialsList
				credentials={[
					makeMockCredential({ name: 'PAT one', api, catalog_api_id: 'github.com' }),
					makeMockCredential({ name: 'PAT two', api, catalog_api_id: 'github.com' }),
				]}
				isLoading={false}
				onAdd={vi.fn()}
				onEdit={vi.fn()}
				onDelete={vi.fn()}
				onConnect={vi.fn()}
			/>,
		);
		const group = screen.getByTestId('credential-group');
		await waitFor(() =>
			expect(within(group).getByRole('heading', { level: 3 })).toHaveTextContent(/^GitHub$/),
		);
	});

	it('keeps the catalog-derived title when the API isn’t imported', async () => {
		worker.use(
			http.get('/apis', () =>
				HttpResponse.json({ data: [], has_more: false, next_cursor: null }),
			),
		);
		const api = { vendor: 'stripe', name: 'stripe-api', version: '' };
		renderWithProviders(
			<CredentialsList
				credentials={[
					makeMockCredential({ name: 'A', api, catalog_api_id: 'stripe.com' }),
					makeMockCredential({ name: 'B', api, catalog_api_id: 'stripe.com' }),
				]}
				isLoading={false}
				onAdd={vi.fn()}
				onEdit={vi.fn()}
				onDelete={vi.fn()}
				onConnect={vi.fn()}
			/>,
		);
		await new Promise((r) => setTimeout(r, 100));
		expect(screen.getByRole('heading', { level: 3 })).toHaveTextContent('stripe.com');
	});
});
