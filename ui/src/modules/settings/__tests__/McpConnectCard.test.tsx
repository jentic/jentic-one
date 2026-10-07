/**
 * McpConnectCard — the #1249 deployment-level MCP pointer on the Settings
 * page. Pins the honest-advertising contract: the `/mcp` URL renders only
 * when the instance reports `mcp_enabled`, the disabled arm says so instead
 * of showing an endpoint that 404s, and the URL derivation falls back to the
 * browser origin when no canonical base URL is configured.
 */
import { describe, it, expect } from 'vitest';
import { http, HttpResponse } from 'msw';
import { renderWithProviders, screen, checkA11y } from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { McpConnectCard, mcpEndpointUrl } from '@/modules/settings/components/McpConnectCard';

function useInstanceHandler(body: Record<string, unknown>) {
	worker.use(http.get('/instance', () => HttpResponse.json(body)));
}

describe('McpConnectCard', () => {
	it('advertises the /mcp URL with a copy affordance when the instance serves it', async () => {
		useInstanceHandler({
			backend: 'local',
			canonical_base_url: 'https://jentic.example.test',
			host: 'jentic.example.test',
			mcp_enabled: true,
		});
		const { container } = renderWithProviders(<McpConnectCard />);

		expect(
			await screen.findByRole('heading', { name: 'Connect an MCP client' }),
		).toBeInTheDocument();
		expect(screen.getByText('https://jentic.example.test/mcp')).toBeInTheDocument();
		expect(screen.getByRole('button', { name: /copy/i })).toBeInTheDocument();
		await checkA11y(container);
	});

	it('says the endpoint is off instead of advertising a URL that 404s', async () => {
		// The default global /instance mock carries no mcp_enabled — the
		// backend default (server.mcp.enabled: false).
		const { container } = renderWithProviders(<McpConnectCard />);

		expect(
			await screen.findByRole('heading', { name: 'Connect an MCP client' }),
		).toBeInTheDocument();
		expect(screen.getByText(/does not serve the HTTP MCP endpoint/)).toBeInTheDocument();
		expect(screen.queryByText('https://jentic.example.test/mcp')).not.toBeInTheDocument();
		await checkA11y(container);
	});

	it('treats an explicit mcp_enabled: false exactly like the absent field', async () => {
		// Pinned separately from the absent-field case above so the gate's
		// contract survives the global /instance mock ever growing the field.
		useInstanceHandler({
			backend: 'local',
			canonical_base_url: 'https://jentic.example.test',
			host: 'jentic.example.test',
			mcp_enabled: false,
		});
		renderWithProviders(<McpConnectCard />);

		expect(await screen.findByText(/does not serve the HTTP MCP endpoint/)).toBeInTheDocument();
		expect(screen.queryByText('https://jentic.example.test/mcp')).not.toBeInTheDocument();
	});

	it('falls back to the browser origin when no canonical base URL is configured', async () => {
		useInstanceHandler({
			backend: 'local',
			canonical_base_url: '',
			host: '',
			mcp_enabled: true,
		});
		renderWithProviders(<McpConnectCard />);

		expect(await screen.findByText(`${window.location.origin}/mcp`)).toBeInTheDocument();
	});

	it('shows the broker (data plane) URL when the backend reports one (#1249)', async () => {
		useInstanceHandler({
			backend: 'remote',
			canonical_base_url: 'https://jentic.example.test',
			host: 'jentic.example.test',
			mcp_enabled: true,
			broker_url: 'https://broker.jentic.example.test',
		});
		const { container } = renderWithProviders(<McpConnectCard />);

		expect(await screen.findByText('Broker (data plane) URL')).toBeInTheDocument();
		expect(screen.getByText('https://broker.jentic.example.test')).toBeInTheDocument();
		expect(screen.getByText(/jentic register --broker-url/)).toBeInTheDocument();
		await checkA11y(container);
	});

	it('renders the broker row in the MCP-disabled arm too — the broker serves stdio execute', async () => {
		useInstanceHandler({
			backend: 'remote',
			canonical_base_url: 'https://jentic.example.test',
			host: 'jentic.example.test',
			mcp_enabled: false,
			broker_url: 'https://broker.jentic.example.test',
		});
		renderWithProviders(<McpConnectCard />);

		expect(await screen.findByText(/does not serve the HTTP MCP endpoint/)).toBeInTheDocument();
		expect(screen.getByText('https://broker.jentic.example.test')).toBeInTheDocument();
	});

	it('omits the broker row when the backend reports null or predates the field', async () => {
		// Null = the backend withholds a loopback broker on a remote install;
		// absent = an older backend. Either way, no guess is rendered.
		useInstanceHandler({
			backend: 'remote',
			canonical_base_url: 'https://jentic.example.test',
			host: 'jentic.example.test',
			mcp_enabled: true,
			broker_url: null,
		});
		renderWithProviders(<McpConnectCard />);

		expect(await screen.findByText('https://jentic.example.test/mcp')).toBeInTheDocument();
		expect(screen.queryByText('Broker (data plane) URL')).not.toBeInTheDocument();
	});
});

describe('mcpEndpointUrl', () => {
	it('joins without doubling slashes and falls back to the browser origin', () => {
		expect(mcpEndpointUrl('https://jentic.example.test/')).toBe(
			'https://jentic.example.test/mcp',
		);
		expect(mcpEndpointUrl('')).toBe(`${window.location.origin}/mcp`);
		expect(mcpEndpointUrl(undefined)).toBe(`${window.location.origin}/mcp`);
	});
});
