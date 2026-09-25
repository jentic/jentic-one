/**
 * CredentialGroupCard — the header names only what every row shares, and each
 * row states its own version pin ("any version" when unpinned), so a group of
 * one API's revisions never hides a pin behind the first row's identity.
 */
import { describe, expect, it, vi } from 'vitest';
import { renderWithProviders, screen, within } from '@/__tests__/test-utils';
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
