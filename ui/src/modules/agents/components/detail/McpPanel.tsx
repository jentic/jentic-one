/**
 * McpPanel — the agent detail console's MCP tab (local-MCP 2-E2, #1188).
 *
 * MCP is a TRANSPORT of this agent, not a separate entity (master plan §3.10),
 * so the surface lives here inside the agent console rather than behind any
 * new top-level nav. Two cards:
 *
 *   - McpConfigCard    → the exact copy-paste wiring for THIS agent
 *     (`jentic mcp --context <name>`, pinned — a bare `jentic mcp` follows the
 *     operator's active context and would silently re-point the runtime),
 *     plus the prerequisites and the instance identity the snippet registers
 *     against. The stdio config encodes no base URL: the target instance comes
 *     from the context's environment on the AGENT machine, which is why the
 *     prerequisites lead with `jentic register --url <this instance>`.
 *   - McpSessionsCard  → session history from `mcp.session_started` internal
 *     events plus "last active" from the newest MCP-origin execution. The
 *     label vocabulary is "started / last active" — NEVER "connected":
 *     stdio liveness is unknowable server-side and phase 3's `/mcp` is
 *     stateless by design, so request-level recency is the honest signal.
 *
 * The HTTP variant is unconditionally hidden in phase 2: `server.mcp` config
 * doesn't exist yet (phase 3 creates it and exposes `server.mcp.enabled`).
 * Un-hiding is a phase-3 follow-up; a test pins it hidden until then.
 */
import { Plug, Terminal } from 'lucide-react';
import {
	CodeSnippet,
	DataTable,
	DetailSection,
	ErrorAlert,
	LoadingState,
	type Column,
} from '@/shared/ui';
import { formatTimestamp, timeAgo } from '@/shared/lib/utils';
import {
	mcpClientLabel,
	useInstanceIdentity,
	useLatestMcpActivity,
	useMcpSessions,
	type McpSessionEntity,
} from '@/modules/agents/api';
import { MetaItem } from '@/modules/agents/components/detail/shared';

/**
 * Phase-2 pin: the streamable-HTTP variant stays hidden until phase 3 lands
 * `server.mcp` and exposes `server.mcp.enabled` to the UI. Exported so the
 * test suite can assert the pin still holds (flipping this without the
 * backend capability would advertise a transport that 404s).
 */
export const SHOW_HTTP_VARIANT = false;

interface McpPanelProps {
	agentName: string;
	agentId: string;
}

/** Quote a shell argument when it needs it (agent names may contain spaces). */
function shellArg(value: string): string {
	return /^[A-Za-z0-9._-]+$/.test(value) ? value : `"${value.replace(/"/g, '\\"')}"`;
}

/**
 * Host portion of a URL, or the raw string when it doesn't parse. The backend
 * serves `host: ""` alongside a set-but-unparseable `canonical_base_url`
 * (e.g. a scheme-less "jentic.example.com" — instance_identity.py), and
 * `new URL(...)` throwing during render would take out the whole agent detail
 * page via the error boundary. Degrading to the raw value is the honest
 * fallback — it's still the address the operator configured.
 */
function safeHost(url: string): string {
	try {
		return new URL(url).host;
	} catch {
		return url;
	}
}

// ---------------------------------------------------------------------------
// Config card
// ---------------------------------------------------------------------------

export function McpConfigCard({ agentName }: { agentName: string }) {
	const identity = useInstanceIdentity();

	// The operator is looking at a working address of this instance, so the
	// browser origin is the honest fallback when no canonical base URL is
	// configured (or `GET /instance` failed).
	const instanceUrl = identity.data?.baseUrl || window.location.origin;
	const instanceHost = identity.data?.host || safeHost(instanceUrl);
	// On a remote install the broker lives on its own host and is never derived
	// from the control-plane URL — without --broker-url the environment has no
	// broker and `jentic execute` fail-closes (register.go). The UI can't know
	// the broker URL, so the snippet carries an explicit placeholder.
	const isRemote = identity.data?.backend === 'remote';

	// §3.10 one-agent-per-runtime: the context name is whatever binding the
	// operator created on the agent machine — the agent's name is the
	// suggested (and `jentic setup`-default) convention, so pre-fill it.
	const context = shellArg(agentName);
	const command = `jentic mcp --context ${context}`;
	const registerCommand = `jentic register --url ${shellArg(instanceUrl)}${
		isRemote ? ' --broker-url <broker-url>' : ''
	}`;
	const jsonConfig = JSON.stringify(
		{ mcpServers: { jentic: { command: 'jentic', args: ['mcp', '--context', agentName] } } },
		null,
		2,
	);

	return (
		<DetailSection title="Connect via MCP" icon={<Terminal className="h-4 w-4" />}>
			<p className="text-muted-foreground text-sm">
				Wire an MCP client to this instance as <strong>{agentName}</strong>. Prerequisites:{' '}
				<code className="font-mono text-xs">jentic</code> CLI installed +{' '}
				<code className="font-mono text-xs">{registerCommand}</code> on the{' '}
				<strong>agent machine</strong> — or{' '}
				<code className="font-mono text-xs">jentic setup</code> for the guided path.
				{isRemote && (
					<>
						{' '}
						On a remote install <code className="font-mono text-xs">
							--broker-url
						</code>{' '}
						is required — without it{' '}
						<code className="font-mono text-xs">jentic execute</code> fail-closes. Ask
						your operator for the broker (data plane) URL.
					</>
				)}
			</p>

			<div className="space-y-3">
				<CodeSnippet label="MCP server command (stdio)" code={command} />
				<CodeSnippet
					label=".cursor/mcp.json · claude_desktop_config.json"
					code={jsonConfig}
				/>
				{/* Phase-3 follow-up: the streamable-HTTP variant renders here once
				    `server.mcp.enabled` exists and is true. Test-pinned hidden. */}
				{SHOW_HTTP_VARIANT && <CodeSnippet label="Streamable HTTP" code="" />}
			</div>

			<p className="text-muted-foreground text-xs">
				<code className="font-mono">--context</code> is pinned on purpose: a bare{' '}
				<code className="font-mono">jentic mcp</code> follows the machine's <em>active</em>{' '}
				context, so a later context switch would silently re-point the runtime at a
				different agent or instance. Use the context bound to this agent on the agent
				machine (its name is the suggested convention).
			</p>

			<dl className="border-border/60 grid grid-cols-2 gap-x-4 gap-y-3 border-t pt-3 sm:grid-cols-3">
				<MetaItem
					label="Instance"
					value={<span className="font-mono">{instanceHost}</span>}
				/>
				<MetaItem
					label="Register URL"
					value={<span className="font-mono">{instanceUrl}</span>}
				/>
				{identity.data?.backend && (
					<MetaItem label="Backend" value={identity.data.backend} />
				)}
			</dl>
		</DetailSection>
	);
}

// ---------------------------------------------------------------------------
// Sessions card
// ---------------------------------------------------------------------------

function timeCell(value: string) {
	return (
		<span className="text-muted-foreground text-xs" title={formatTimestamp(value)}>
			{timeAgo(value)}
		</span>
	);
}

export function McpSessionsCard({ agentId }: { agentId: string }) {
	const sessions = useMcpSessions(agentId);
	const lastActivity = useLatestMcpActivity(agentId);

	const columns: Column<McpSessionEntity>[] = [
		{
			key: 'client',
			header: 'Client',
			render: (row) => <span className="text-foreground text-sm">{mcpClientLabel(row)}</span>,
		},
		{
			key: 'transport',
			header: 'Transport',
			className: 'w-28',
			// Rendered verbatim (`stdio` today, `http` when phase 3 lands) so a
			// future transport value degrades gracefully instead of breaking.
			render: (row) => (
				<span className="text-muted-foreground font-mono text-xs">
					{row.transport ?? '—'}
				</span>
			),
		},
		{
			key: 'sessionId',
			header: 'Session',
			className: 'max-w-[160px]',
			render: (row) => (
				<code className="text-muted-foreground block truncate font-mono text-xs">
					{row.sessionId ?? '—'}
				</code>
			),
		},
		{
			key: 'startedAt',
			header: 'Started',
			className: 'w-32 text-right',
			render: (row) => timeCell(row.startedAt),
		},
	];

	return (
		<DetailSection
			title="MCP sessions"
			icon={<Plug className="h-4 w-4" />}
			titleExtra={
				<span className="text-muted-foreground text-xs">started / last active</span>
			}
			trailing={
				// "Last active" = the newest MCP-origin execution — request-level
				// recency, the only liveness signal the server honestly has.
				lastActivity.data ? (
					<span
						className="text-muted-foreground text-xs"
						title={formatTimestamp(lastActivity.data)}
					>
						Last active {timeAgo(lastActivity.data)}
					</span>
				) : undefined
			}
			bodyClassName="px-0 py-0"
		>
			{sessions.isPending ? (
				<LoadingState size="sm" message="Loading MCP sessions…" />
			) : sessions.isError ? (
				// A real failure (5xx / network) must not masquerade as "no
				// sessions yet" — same error passthrough convention as
				// ActorAuditPanel. 401/403 resolve to `null` below, not here.
				<ErrorAlert message="Failed to load MCP sessions." className="m-5" />
			) : sessions.data === null ? (
				<p className="text-muted-foreground px-5 py-6 text-center text-sm">
					MCP session history requires event-read permissions.
				</p>
			) : (
				<DataTable<McpSessionEntity>
					columns={columns}
					data={sessions.data ?? []}
					getRowKey={(row) => row.eventId}
					ariaLabel="MCP sessions"
					emptyMessage="No MCP sessions recorded for this agent yet. Sessions appear when an MCP client first talks to this instance as this agent."
				/>
			)}
		</DetailSection>
	);
}

/** The MCP tab panel: config card + session history. */
export function McpPanel({ agentName, agentId }: McpPanelProps) {
	return (
		<div className="space-y-4">
			<McpConfigCard agentName={agentName} />
			<McpSessionsCard agentId={agentId} />
		</div>
	);
}
