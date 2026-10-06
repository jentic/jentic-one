import { describe, expect, it, vi } from 'vitest';
import { checkA11y, renderWithProviders, screen, within } from '@/__tests__/test-utils';
import { CredentialType } from '@/shared/credentials/api';
import { makeMockCredential } from '@/shared/credentials/mocks/handlers';
import { CredentialCard } from '@/shared/credentials/components/CredentialCard';
import { CredentialsList } from '@/shared/credentials/components/CredentialsList';
import { credentialEditableBy } from '@/shared/credentials/lib/credentialAuthority';

/**
 * A credential the viewer did not create can still be listed for them — it is
 * shared with them. Only its owner or an `org:admin` can change it, so its card
 * reads "Shared with you" and offers no edit or delete; Connect is untouched.
 */
describe('credentialEditableBy', () => {
	const cred = makeMockCredential({ created_by: 'usr_owner' });

	it('lets the owner edit', () => {
		expect(credentialEditableBy(cred, { id: 'usr_owner', permissions: [] })).toBe(true);
	});

	it('lets an org:admin edit any credential', () => {
		expect(credentialEditableBy(cred, { id: 'usr_other', permissions: ['org:admin'] })).toBe(
			true,
		);
	});

	it('makes a credential someone else created read-only', () => {
		expect(credentialEditableBy(cred, { id: 'usr_other', permissions: [] })).toBe(false);
		expect(credentialEditableBy(cred, { id: 'usr_other', permissions: null })).toBe(false);
	});

	it('makes a credential with no recorded owner read-only for a non-admin', () => {
		const ownerless = makeMockCredential({ created_by: null });
		expect(credentialEditableBy(ownerless, { id: 'usr_other', permissions: [] })).toBe(false);
	});

	it('treats an unknown viewer as able to edit — the server still enforces', () => {
		expect(credentialEditableBy(cred, null)).toBe(true);
		expect(credentialEditableBy(cred, undefined)).toBe(true);
	});
});

describe('CredentialCard — read-only (shared with you)', () => {
	const noop = vi.fn();

	it('badges a shared credential and hides edit and delete', async () => {
		const cred = makeMockCredential({ name: 'Team Slack token' });
		const { container } = renderWithProviders(
			<CredentialCard cred={cred} onEdit={noop} onDelete={noop} onConnect={noop} readOnly />,
		);
		expect(screen.getByTestId('credential-card-badges')).toHaveTextContent('Shared with you');
		expect(
			screen.queryByRole('button', { name: 'Edit credential Team Slack token' }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole('button', { name: 'Delete credential Team Slack token' }),
		).not.toBeInTheDocument();
		// The full-card click target opens the edit sheet, so it goes too.
		expect(screen.queryByTestId('credential-card-overlay')).not.toBeInTheDocument();
		await checkA11y(container);
	});

	it('keeps the Connect button on a shared OAuth credential', () => {
		const cred = makeMockCredential({
			name: 'Shared GitHub',
			type: CredentialType.OAUTH2,
			provider: 'direct_oauth2',
			details: { connected: false },
		});
		renderWithProviders(
			<CredentialCard cred={cred} onEdit={noop} onDelete={noop} onConnect={noop} readOnly />,
		);
		expect(screen.getByRole('button', { name: 'Connect Shared GitHub' })).toBeInTheDocument();
		expect(
			screen.queryByRole('button', { name: 'Edit credential Shared GitHub' }),
		).not.toBeInTheDocument();
	});

	it('an editable card carries no badge and keeps edit and delete', () => {
		const cred = makeMockCredential({ name: 'My token' });
		renderWithProviders(
			<CredentialCard cred={cred} onEdit={noop} onDelete={noop} onConnect={noop} />,
		);
		expect(screen.queryByTestId('credential-shared-badge')).not.toBeInTheDocument();
		expect(
			screen.getByRole('button', { name: 'Edit credential My token' }),
		).toBeInTheDocument();
		expect(
			screen.getByRole('button', { name: 'Delete credential My token' }),
		).toBeInTheDocument();
		expect(screen.getByTestId('credential-card-overlay')).toBeInTheDocument();
	});
});

describe('CredentialsList — readOnlyFor', () => {
	const noop = vi.fn();

	it('marks only the shared rows, on single cards and inside a group card', () => {
		const api = { vendor: 'airlabs.co', name: 'default', version: '1.0.0' };
		const mine = makeMockCredential({ name: 'Mine', api, created_by: 'usr_me' });
		const shared = makeMockCredential({ name: 'Theirs', api, created_by: 'usr_them' });
		const sharedSolo = makeMockCredential({
			name: 'Solo shared',
			api: { vendor: 'stripe.com', name: 'default', version: '1.0.0' },
			created_by: 'usr_them',
		});
		const viewer = { id: 'usr_me', permissions: [] };
		renderWithProviders(
			<CredentialsList
				credentials={[mine, shared, sharedSolo]}
				isLoading={false}
				onAdd={noop}
				onEdit={noop}
				onDelete={noop}
				onConnect={noop}
				readOnlyFor={(c) => !credentialEditableBy(c, viewer)}
			/>,
		);

		const cards = screen.getAllByTestId('credential-card');
		const byName = (name: string): HTMLElement => {
			const card = cards.find((c) => within(c).queryByText(name));
			if (!card) throw new Error(`no card for ${name}`);
			return card;
		};

		expect(within(byName('Mine')).queryByText('Shared with you')).not.toBeInTheDocument();
		expect(
			within(byName('Mine')).getByRole('button', { name: 'Edit credential Mine' }),
		).toBeInTheDocument();

		for (const name of ['Theirs', 'Solo shared']) {
			const card = byName(name);
			expect(within(card).getByText('Shared with you')).toBeInTheDocument();
			expect(
				within(card).queryByRole('button', { name: `Edit credential ${name}` }),
			).not.toBeInTheDocument();
			expect(
				within(card).queryByRole('button', { name: `Delete credential ${name}` }),
			).not.toBeInTheDocument();
		}
	});
});

describe('CredentialCard — whose agents the usage counts', () => {
	const noop = vi.fn();

	it.each([
		[false, 1, 'used by 1 agent'],
		[false, 0, 'used by no agents'],
		[true, 2, 'used by 2 of your agents'],
		[true, 0, 'used by none of your agents'],
	] as const)('usedByYoursOnly=%s, %i bound → "%s"', (yoursOnly, count, copy) => {
		renderWithProviders(
			<CredentialCard
				cred={makeMockCredential({ name: 'Team Slack token' })}
				onEdit={noop}
				onDelete={noop}
				onConnect={noop}
				usedByAgentCount={count}
				usedByYoursOnly={yoursOnly}
			/>,
		);
		expect(screen.getByTestId('cred-used-by')).toHaveTextContent(copy);
	});
});
