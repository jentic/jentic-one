/**
 * McpConnectCard — the deployment-level "Connect an MCP client" affordance
 * (#1249: nothing in the product told an operator where this deployment's
 * MCP endpoint lives).
 *
 * Lives on the Settings page's Clients tab: MCP clients that use interactive
 * OAuth register themselves into exactly this roster (DCR), so the pointer
 * belongs next to it. The card advertises only what the SPA can honestly
 * derive from the unauthenticated `GET /instance` identity:
 *
 *   - The `/mcp` URL, from the instance's canonical base URL with the
 *     browser origin as the fallback (the operator is looking at a working
 *     address of this instance — the same posture as the agent detail
 *     page's McpPanel).
 *   - Whether the endpoint is actually served (`server.mcp.enabled`),
 *     mirroring McpPanel's gate: advertising the URL unconditionally would
 *     show an endpoint that 404s on default installs, so the disabled arm
 *     says so instead.
 *
 * The broker (data plane) URL — the other half of #1249 — is deliberately
 * NOT shown: no HTTP surface exposes `server.mcp.broker_url`, so the SPA
 * cannot know it; surfacing it needs a backend change first.
 */
import { Plug } from 'lucide-react';
import { Card, CardBody, CardTitle, CodeSnippet } from '@/shared/ui';
import { useInstanceIdentity } from '@/modules/settings/api/hooks';

/**
 * The deployment's Streamable HTTP MCP endpoint. Exported for the test
 * suite; the fallback mirrors McpPanel's `instanceUrl` derivation.
 */
export function mcpEndpointUrl(canonicalBaseUrl: string | undefined): string {
	const base = canonicalBaseUrl || window.location.origin;
	return `${base.replace(/\/+$/, '')}/mcp`;
}

export function McpConnectCard() {
	const identity = useInstanceIdentity();

	// No identity, no claim: rendering a guess while loading (or after a
	// failed fetch) could advertise an endpoint this instance never serves.
	if (!identity.data) return null;

	const enabled = identity.data.mcp_enabled === true;

	return (
		<Card>
			<CardBody className="space-y-3">
				<CardTitle className="flex items-center gap-2">
					<Plug className="text-muted-foreground h-4 w-4" aria-hidden="true" />
					Connect an MCP client
				</CardTitle>
				{enabled ? (
					<>
						<CodeSnippet
							label="Streamable HTTP endpoint"
							code={mcpEndpointUrl(identity.data.canonical_base_url)}
						/>
						<p className="text-muted-foreground text-sm">
							Add this URL to your MCP client's configuration (e.g.{' '}
							<code className="font-mono text-xs">.cursor/mcp.json</code>), with an
							agent API key or interactive OAuth as the credential — an agent's MCP
							tab carries the full per-agent snippet.
						</p>
					</>
				) : (
					<p className="text-muted-foreground text-sm">
						This instance does not serve the HTTP MCP endpoint (
						<code className="font-mono text-xs">server.mcp.enabled</code> is off).
						Agents can still connect over stdio via the{' '}
						<code className="font-mono text-xs">jentic</code> CLI — see an agent's MCP
						tab.
					</p>
				)}
			</CardBody>
		</Card>
	);
}
