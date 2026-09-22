import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { PermissionPanel } from '@/modules/docs/components/PermissionPanel';
import type { ReferenceEndpoint } from '@/modules/docs/api/types';

function endpoint(overrides: Partial<ReferenceEndpoint> = {}): ReferenceEndpoint {
	return {
		method: 'GET',
		path: '/agents',
		surface: 'admin',
		summary: 'List agents',
		operation_id: 'list_agents',
		authenticated: true,
		public: false,
		actor_types: [],
		required_permissions: [],
		implied_permissions: {},
		auth_note: null,
		typical_caller: null,
		group: 'Agents',
		...overrides,
	};
}

describe('PermissionPanel', () => {
	it('renders required permissions and the advisory typical caller', () => {
		render(
			<PermissionPanel
				endpoint={endpoint({
					required_permissions: ['agents:read', 'agents:write'],
					typical_caller: 'operator',
				})}
			/>,
		);
		expect(screen.getByText('agents:read')).toBeInTheDocument();
		expect(screen.getByText('agents:write')).toBeInTheDocument();
		expect(screen.getByText(/Human operator/i)).toBeInTheDocument();
		expect(screen.getByText(/advisory/i)).toBeInTheDocument();
	});

	it('renders a public notice when not authenticated', () => {
		render(<PermissionPanel endpoint={endpoint({ authenticated: false, public: true })} />);
		expect(screen.getByText(/no authentication required/i)).toBeInTheDocument();
	});

	it('renders an auth note when present', () => {
		render(
			<PermissionPanel
				endpoint={endpoint({
					required_permissions: ['broker:execute'],
					typical_caller: 'agent',
					auth_note: 'Requires a provisioned upstream credential.',
				})}
			/>,
		);
		expect(screen.getByText(/provisioned upstream credential/i)).toBeInTheDocument();
	});

	it('renders the implied-permission closure when present', () => {
		render(
			<PermissionPanel
				endpoint={endpoint({
					required_permissions: ['admin'],
					implied_permissions: { admin: ['agents:read', 'agents:write'] },
				})}
			/>,
		);
		expect(screen.getByText(/Implies/i)).toBeInTheDocument();
	});

	it('shows the no-specific-permission notice for an authenticated-but-unpermissiond op', () => {
		render(<PermissionPanel endpoint={endpoint({ required_permissions: [] })} />);
		expect(screen.getByText(/no specific permission/i)).toBeInTheDocument();
	});
});
