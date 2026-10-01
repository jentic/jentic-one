import { describe, expect, it } from 'vitest';
import { renderWithProviders, screen, checkA11y } from '@/__tests__/test-utils';
import { ApiCard } from '@/modules/workspace/components/ApiCard';
import type { ApiHealth, ApiHealthIndex, WorkspaceApi } from '@/modules/workspace/api';

function makeApi(overrides: Partial<WorkspaceApi> = {}): WorkspaceApi {
	return {
		api: { vendor: 'github.com', name: 'main', version: '1.0.0', host: null },
		catalogApiId: null,
		displayName: null,
		description: null,
		iconUrl: null,
		currentRevisionId: 'rev_1',
		revisionCount: 1,
		operationCount: 3,
		securitySchemes: [],
		createdAt: '2026-05-01T10:00:00Z',
		updatedAt: '2026-05-01T10:00:00Z',
		...overrides,
	};
}

describe('ApiCard', () => {
	it('renders the friendly title as the heading', () => {
		renderWithProviders(<ApiCard api={makeApi()} />);
		expect(screen.getByRole('heading', { name: 'Github.Com' })).toBeInTheDocument();
	});

	it('never renders a blank heading or aria-label when the display name is absent and vendor is empty', () => {
		// `apiRefDisplayName` returns '' here (no display name, empty vendor,
		// generic `main`). The titleFor fallback must keep the heading + the
		// link aria-label non-empty so the card is always identifiable.
		renderWithProviders(
			<ApiCard
				api={makeApi({
					displayName: null,
					api: { vendor: '', name: 'main', version: '1.0.0', host: null },
				})}
			/>,
		);
		const heading = screen.getByRole('heading', { level: 3 });
		expect(heading.textContent?.trim()).not.toBe('');
		// The whole card is a link; its accessible name must not be a bare "Open ".
		const link = screen.getByTestId('workspace-api-card');
		const label = link.getAttribute('aria-label') ?? '';
		expect(label).not.toBe('Open ');
		expect(label.replace(/^Open /, '').trim()).not.toBe('');
	});

	it('shows the "Update available" badge when updateAvailable is true', () => {
		renderWithProviders(<ApiCard api={makeApi({ updateAvailable: true })} />);
		expect(screen.getByTestId('api-state-update')).toHaveTextContent('Update available');
	});

	it('hides the badge when updateAvailable is false/absent', () => {
		renderWithProviders(<ApiCard api={makeApi()} />);
		expect(screen.queryByTestId('api-state-update')).not.toBeInTheDocument();
	});

	it('shows Live via the shared state badge, Draft when there is no live revision', () => {
		const { unmount } = renderWithProviders(<ApiCard api={makeApi()} />);
		expect(screen.getByTestId('api-state-live')).toHaveTextContent('Live');
		unmount();
		renderWithProviders(<ApiCard api={makeApi({ currentRevisionId: null })} />);
		expect(screen.getByTestId('api-state-draft')).toHaveTextContent('Draft');
	});

	it('without a health index shows only registry fields (no agents/usage/warning)', () => {
		renderWithProviders(<ApiCard api={makeApi({ securitySchemes: ['http'] })} />);
		expect(screen.getByTestId('workspace-api-card-metrics')).toHaveTextContent(
			'3 ops1 revision1 scheme',
		);
		expect(screen.queryByTestId('workspace-api-card-agents')).not.toBeInTheDocument();
		expect(screen.queryByTestId('workspace-api-card-usage')).not.toBeInTheDocument();
		expect(
			screen.queryByTestId('workspace-api-card-credential-missing'),
		).not.toBeInTheDocument();
	});
});

function index(
	health: Partial<ApiHealth>,
	extra: Partial<Omit<ApiHealthIndex, 'healthFor'>> = {},
): ApiHealthIndex {
	return {
		healthFor: () => ({
			credentials: [],
			credentialCount: 1,
			usage: null,
			...health,
		}),
		usageAvailable: true,
		usageExhaustive: true,
		usageLoading: false,
		credentialsComplete: true,
		credentialsError: false,
		...extra,
	};
}

describe('ApiCard health rows', () => {
	it('update + credential-missing tile: badges, agents, calls/failed, warning', async () => {
		const { container } = renderWithProviders(
			<ApiCard
				api={makeApi({ updateAvailable: true, securitySchemes: ['http'] })}
				healthIndex={index({
					credentialCount: 0,
					usage: { total: 1204, failed: 3, trend: [1, 5, 2, 8] },
				})}
				agents={{ agentCount: 0, agentsAtLeast: false, agentsLoading: false }}
			/>,
		);
		expect(screen.getByTestId('api-state-live')).toBeInTheDocument();
		expect(screen.getByTestId('api-state-update')).toHaveTextContent('Update available');
		expect(screen.getByTestId('workspace-api-card-agents')).toHaveTextContent('0 agents');
		expect(screen.getByTestId('workspace-api-card-usage')).toHaveTextContent('1,204 calls');
		expect(screen.getByTestId('workspace-api-card-failures')).toHaveTextContent('3 failed');
		expect(screen.getByTestId('workspace-api-card-credential-missing')).toHaveTextContent(
			'No credential — agents can’t call it',
		);
		await checkA11y(container);
	});

	it('healthy tile: agents + calls, no failures, no warning', () => {
		renderWithProviders(
			<ApiCard
				api={makeApi({ securitySchemes: ['apiKey'] })}
				healthIndex={index({
					credentialCount: 1,
					usage: { total: 40, failed: 0, trend: [1, 2] },
				})}
				agents={{ agentCount: 2, agentsAtLeast: false, agentsLoading: false }}
			/>,
		);
		expect(screen.getByTestId('workspace-api-card-agents')).toHaveTextContent('2 agents');
		expect(screen.getByTestId('workspace-api-card-usage')).toHaveTextContent('40 calls');
		expect(screen.queryByTestId('workspace-api-card-failures')).not.toBeInTheDocument();
		expect(
			screen.queryByTestId('workspace-api-card-credential-missing'),
		).not.toBeInTheDocument();
	});

	it('never warns for an API with no security schemes', () => {
		renderWithProviders(
			<ApiCard api={makeApi()} healthIndex={index({ credentialCount: 0 })} />,
		);
		expect(
			screen.queryByTestId('workspace-api-card-credential-missing'),
		).not.toBeInTheDocument();
	});

	it('reserves space with skeletons while credentials / usage are still loading', () => {
		renderWithProviders(
			<ApiCard
				api={makeApi({ securitySchemes: ['http'] })}
				healthIndex={index(
					{ credentialCount: null },
					{
						usageAvailable: false,
						usageExhaustive: false,
						usageLoading: true,
						credentialsComplete: false,
					},
				)}
				agents={{ agentCount: null, agentsAtLeast: false, agentsLoading: true }}
			/>,
		);
		expect(screen.getByTestId('workspace-api-card-agents-loading')).toBeInTheDocument();
		expect(screen.getByTestId('workspace-api-card-usage-loading')).toBeInTheDocument();
		expect(
			screen.queryByTestId('workspace-api-card-credential-missing'),
		).not.toBeInTheDocument();
	});

	it('shows "— agents" (not a forever skeleton) when an agent read failed or was capped', () => {
		renderWithProviders(
			<ApiCard
				api={makeApi()}
				healthIndex={index({})}
				agents={{ agentCount: null, agentsAtLeast: false, agentsLoading: false }}
			/>,
		);
		expect(screen.queryByTestId('workspace-api-card-agents-loading')).not.toBeInTheDocument();
		expect(screen.getByTestId('workspace-api-card-agents')).toHaveTextContent('— agents');
	});

	it('renders a truncated agent count as a floor ("50+ agents")', () => {
		renderWithProviders(
			<ApiCard
				api={makeApi()}
				healthIndex={index({})}
				agents={{ agentCount: 50, agentsAtLeast: true, agentsLoading: false }}
			/>,
		);
		expect(screen.getByTestId('workspace-api-card-agents')).toHaveTextContent('50+ agents');
	});

	it('hides the usage row for users who cannot read usage (non-admin)', () => {
		renderWithProviders(
			<ApiCard
				api={makeApi()}
				healthIndex={index({}, { usageAvailable: false, usageExhaustive: false })}
			/>,
		);
		expect(screen.queryByTestId('workspace-api-card-usage')).not.toBeInTheDocument();
		expect(screen.queryByTestId('workspace-api-card-usage-loading')).not.toBeInTheDocument();
	});

	it('shows "0 calls" only when the usage list is exhaustive', () => {
		const { unmount } = renderWithProviders(
			<ApiCard api={makeApi()} healthIndex={index({ usage: null })} />,
		);
		expect(screen.getByTestId('workspace-api-card-usage')).toHaveTextContent('0 calls');
		unmount();
		renderWithProviders(
			<ApiCard
				api={makeApi()}
				healthIndex={index({ usage: null }, { usageExhaustive: false })}
			/>,
		);
		expect(screen.queryByTestId('workspace-api-card-usage')).not.toBeInTheDocument();
	});
});
