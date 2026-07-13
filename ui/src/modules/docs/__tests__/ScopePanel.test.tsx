import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ScopePanel } from '@/modules/docs/components/ScopePanel';
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
		required_scopes: [],
		implied_scopes: {},
		auth_note: null,
		typical_caller: null,
		group: 'Agents',
		...overrides,
	};
}

describe('ScopePanel', () => {
	it('renders required scopes and the advisory typical caller', () => {
		render(
			<ScopePanel
				endpoint={endpoint({
					required_scopes: ['agents:read', 'agents:write'],
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
		render(<ScopePanel endpoint={endpoint({ authenticated: false, public: true })} />);
		expect(screen.getByText(/no authentication required/i)).toBeInTheDocument();
	});

	it('renders an auth note when present', () => {
		render(
			<ScopePanel
				endpoint={endpoint({
					required_scopes: ['broker:execute'],
					typical_caller: 'agent',
					auth_note: 'Requires a provisioned upstream credential.',
				})}
			/>,
		);
		expect(screen.getByText(/provisioned upstream credential/i)).toBeInTheDocument();
	});

	it('renders the implied-scope closure when present', () => {
		render(
			<ScopePanel
				endpoint={endpoint({
					required_scopes: ['admin'],
					implied_scopes: { admin: ['agents:read', 'agents:write'] },
				})}
			/>,
		);
		expect(screen.getByText(/Implies/i)).toBeInTheDocument();
	});

	it('shows the no-specific-scope notice for an authenticated-but-unscoped op', () => {
		render(<ScopePanel endpoint={endpoint({ required_scopes: [] })} />);
		expect(screen.getByText(/no specific scope/i)).toBeInTheDocument();
	});
});
