import { describe, it, expect, beforeEach, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { worker } from '@/mocks/browser';
import { renderWithProviders, screen, waitFor } from '@/__tests__/test-utils';
import { setToken } from '@/shared/api';
import { AuthProvider } from '@/shared/auth';
import { AgentCredentialPicker } from '@/modules/agents/components/detail/AgentCredentialPicker';

const ME = 'usr_member_1';

/**
 * Override the org-wide `GET /credentials` surface the picker reads. The
 * credentials mock store starts empty, so we stub a small fixture here.
 */
function seedCredentials(
	creds: Array<{ credential_id: string; name: string; created_by: string }>,
) {
	worker.use(
		http.get('/credentials', () =>
			HttpResponse.json({
				data: creds.map((c) => ({
					credential_id: c.credential_id,
					name: c.name,
					type: 'bearer_token',
					provider: 'manual',
					active: true,
					api: { vendor: 'acme', name: 'default', version: '1.0.0' },
					created_at: '2026-05-01T10:00:00Z',
					created_by: c.created_by,
					updated_at: null,
				})),
				has_more: false,
				next_cursor: null,
			}),
		),
	);
}

function seedMe(permissions: string[]) {
	worker.use(
		http.get('/users/me', () =>
			HttpResponse.json({
				id: ME,
				email: 'member@local',
				first_name: 'Member',
				last_name: 'User',
				active: true,
				permissions,
				must_change_password: false,
				created_at: '2026-01-01T00:00:00Z',
				updated_at: null,
			}),
		),
	);
}

function renderPicker() {
	return renderWithProviders(
		<AuthProvider>
			<AgentCredentialPicker boundIds={new Set()} onSelect={vi.fn()} />
		</AuthProvider>,
	);
}

describe('AgentCredentialPicker', () => {
	beforeEach(() => {
		setToken('test-token');
		seedCredentials([
			{ credential_id: 'cred_mine', name: 'My token', created_by: ME },
			{ credential_id: 'cred_theirs', name: 'Shared token', created_by: 'usr_someone_else' },
		]);
	});

	it('offers a non-admin only the credentials they own', async () => {
		// Binding is ownership-scoped server-side (a non-admin binding a
		// credential they don't own gets a 404), so the picker hides those rows
		// rather than offering a dead end.
		seedMe(['agents:read', 'agents:write', 'credentials:read']);
		renderPicker();

		expect(await screen.findByText('My token')).toBeInTheDocument();
		await waitFor(() => expect(screen.queryByText('Shared token')).not.toBeInTheDocument());
	});

	it('offers an org:admin every credential', async () => {
		seedMe(['org:admin']);
		renderPicker();

		expect(await screen.findByText('My token')).toBeInTheDocument();
		expect(await screen.findByText('Shared token')).toBeInTheDocument();
	});
});
