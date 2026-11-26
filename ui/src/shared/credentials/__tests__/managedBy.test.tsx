/**
 * Extension-managed credentials (`managed_by` set): the badge (registered
 * renderer or the generic fallback), the edit sheet and delete confirm that
 * say where the credential is managed, and the bind filter that never offers
 * one.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { checkA11y, renderWithProviders, screen } from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { setToken } from '@/shared/api';
import { AuthProvider } from '@/shared/auth';
import { CredentialType } from '@/shared/credentials/api';
import { makeMockCredential, resetCredentialsStore } from '@/shared/credentials/mocks/handlers';
import { CredentialCard } from '@/shared/credentials/components/CredentialCard';
import { CredentialDeleteDialog } from '@/shared/credentials/components/CredentialDeleteDialog';
import { EditCredentialSheet } from '@/shared/credentials/components/EditCredentialSheet';
import {
	ManagedByBadge,
	isManagedCredential,
	registerManagedByRenderer,
	unregisterManagedByRenderer,
	type ManagedByRendererProps,
} from '@/shared/credentials/components/managedBy';
import { credentialsBindableBy } from '@/shared/credentials/lib/bindAuthority';

const MANAGED = { kind: 'example_connection', ref: 'conn_1' };

function managedCredential() {
	return makeMockCredential({
		credential_id: 'cred_managed_1',
		name: 'Managed key',
		type: CredentialType.API_KEY,
		created_by: 'usr_owner',
		details: { location: 'header', field_name: 'X-Api-Key' },
		managed_by: MANAGED,
	});
}

function seedViewer(id: string, permissions: string[]) {
	worker.use(
		http.get('/users/me', () =>
			HttpResponse.json({
				id,
				email: 'viewer@local',
				first_name: 'View',
				last_name: 'Er',
				active: true,
				permissions,
				must_change_password: false,
				created_at: '2026-01-01T00:00:00Z',
				updated_at: null,
			}),
		),
	);
}

describe('ManagedByBadge', () => {
	afterEach(() => unregisterManagedByRenderer(MANAGED.kind));

	it('renders nothing for an unmanaged credential', () => {
		renderWithProviders(<ManagedByBadge managedBy={null} />);
		expect(screen.queryByTestId('credential-managed-badge')).not.toBeInTheDocument();
	});

	it('falls back to "Managed by <kind>" for an unregistered kind', async () => {
		const { container } = renderWithProviders(<ManagedByBadge managedBy={MANAGED} />);
		expect(screen.getByTestId('credential-managed-badge')).toHaveTextContent(
			'Managed by example connection',
		);
		await checkA11y(container);
	});

	it('uses the renderer registered for the kind', () => {
		function ExampleBadge({ managedBy }: ManagedByRendererProps) {
			return <span data-testid="example-badge">Managed by connection {managedBy.ref}</span>;
		}
		registerManagedByRenderer(MANAGED.kind, ExampleBadge);
		renderWithProviders(<ManagedByBadge managedBy={MANAGED} />);
		expect(screen.getByTestId('example-badge')).toHaveTextContent(
			'Managed by connection conn_1',
		);
		expect(screen.queryByTestId('credential-managed-badge')).not.toBeInTheDocument();
	});

	it('shows on the credential card', () => {
		const noop = vi.fn();
		renderWithProviders(
			<CredentialCard cred={managedCredential()} onEdit={noop} onDelete={noop} />,
		);
		expect(screen.getByTestId('credential-card-badges')).toHaveTextContent(
			'Managed by example connection',
		);
	});
});

describe('credentialsBindableBy', () => {
	const plain = makeMockCredential({ credential_id: 'cred_plain', created_by: 'usr_owner' });
	const managed = managedCredential();

	it('never offers a managed credential, to an owner, an admin or an unknown viewer', () => {
		expect(isManagedCredential(managed)).toBe(true);
		expect(isManagedCredential(plain)).toBe(false);
		for (const viewer of [
			{ id: 'usr_owner', permissions: [] },
			{ id: 'usr_admin', permissions: ['org:admin'] },
			null,
		]) {
			expect(credentialsBindableBy([plain, managed], viewer)).toEqual([plain]);
		}
	});
});

describe('EditCredentialSheet — managed credential', () => {
	beforeEach(() => setToken('test-token'));
	afterEach(() => resetCredentialsStore());

	it('shows where it is managed instead of the form, and cannot save', async () => {
		resetCredentialsStore([managedCredential()]);
		renderWithProviders(
			<EditCredentialSheet credentialId="cred_managed_1" open onClose={() => {}} />,
		);
		expect(await screen.findByTestId('credential-managed-note')).toHaveTextContent(
			/managed by example connection/,
		);
		expect(screen.queryByLabelText(/^Name/)).not.toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Save changes' })).toBeDisabled();
	});
});

describe('CredentialDeleteDialog — managed credential', () => {
	beforeEach(() => setToken('test-token'));
	afterEach(() => resetCredentialsStore());

	function renderDialog(onConfirm: () => void) {
		return renderWithProviders(
			<AuthProvider>
				<CredentialDeleteDialog
					open
					credentialId="cred_managed_1"
					credentialName="Managed key"
					managedBy={MANAGED}
					onClose={() => {}}
					onConfirm={onConfirm}
				/>
			</AuthProvider>,
		);
	}

	it('tells a non-admin where it is managed and offers no delete', async () => {
		seedViewer('usr_owner', ['credentials:write']);
		resetCredentialsStore([managedCredential()]);
		renderDialog(vi.fn());
		expect(await screen.findByTestId('credential-managed-note')).toBeInTheDocument();
		expect(screen.getByText("This credential can't be deleted here")).toBeInTheDocument();
		expect(screen.queryByRole('button', { name: /^Delete/ })).not.toBeInTheDocument();
	});

	it('lets an org:admin confirm the delete', async () => {
		seedViewer('usr_admin', ['org:admin']);
		resetCredentialsStore([managedCredential()]);
		renderDialog(vi.fn());
		expect(await screen.findByRole('button', { name: /^Delete/ })).toBeInTheDocument();
		expect(screen.queryByTestId('credential-managed-note')).not.toBeInTheDocument();
	});
});
