import { useEffect, useRef, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { PageHeader } from '@/components/ui/PageHeader';
import { PageShell } from '@/components/layout/PageShell';
import { Button } from '@/components/ui/Button';
import { Dialog } from '@/components/ui/Dialog';
import { LoadingState } from '@/components/ui/LoadingState';
import { ErrorAlert } from '@/components/ui/ErrorAlert';
import { DataTable, type Column } from '@/components/ui/DataTable';
import { parseApiError } from '@/lib/apiError';
import { apiUrl } from '@/api/client';

const DEFAULT_TOOLKIT_ID = 'default';

type ToolkitRow = {
	id: string;
	name: string;
	description: string | null;
	disabled: boolean;
};

type AgentRow = {
	client_id: string;
	client_name: string;
	status: string;
	created_at: number;
	approved_at: number | null;
	disabled_at: number | null;
	denied_at?: number | null;
	deleted_at?: number | null;
};

type AgentDetail = {
	client_id: string;
	client_name: string;
	status: string;
	jwks: { keys?: { kid?: string; crv?: string; kty?: string }[] };
	created_at: number;
	approved_at: number | null;
	approved_by: string | null;
	denied_at: number | null;
	disabled_at: number | null;
	deleted_at: number | null;
};

type ToolkitCredentialRow = {
	credential_id: string;
	label?: string | null;
	api_id?: string | null;
};

type MergedCredentialRow = {
	credential_id: string;
	label: string | null;
	api_id: string | null;
	via_toolkits: string[];
};

function formatTs(ts: number | null): string {
	if (ts == null || ts <= 0) return '—';
	try {
		return new Date(ts * 1000).toLocaleString(undefined, {
			dateStyle: 'medium',
			timeStyle: 'short',
		});
	} catch {
		return '—';
	}
}

function stopRowClick(e: React.MouseEvent) {
	e.stopPropagation();
}

export default function AgentsPage() {
	const queryClient = useQueryClient();
	const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
	const [grantAgentId, setGrantAgentId] = useState<string | null>(null);
	const [selectedToolkitIds, setSelectedToolkitIds] = useState<Set<string>>(new Set());
	const grantSelectionInitRef = useRef<string | null>(null);
	const [deregisterClientId, setDeregisterClientId] = useState<string | null>(null);
	const [killswitchAgentId, setKillswitchAgentId] = useState<string | null>(null);
	const [declineAgentId, setDeclineAgentId] = useState<string | null>(null);

	const activeAgentsQuery = useQuery({
		queryKey: ['agents', 'list', 'active'],
		queryFn: async () => {
			const r = await fetch(apiUrl('/agents?view=active'), { credentials: 'include' });
			if (!r.ok) throw new Error(`Failed to load agents (${r.status})`);
			return r.json() as Promise<{ agents: AgentRow[] }>;
		},
	});

	const declinedAgentsQuery = useQuery({
		queryKey: ['agents', 'list', 'declined'],
		queryFn: async () => {
			const r = await fetch(apiUrl('/agents?view=declined'), { credentials: 'include' });
			if (!r.ok) throw new Error(`Failed to load declined (${r.status})`);
			return r.json() as Promise<{ agents: AgentRow[] }>;
		},
	});

	const removedAgentsQuery = useQuery({
		queryKey: ['agents', 'list', 'removed'],
		queryFn: async () => {
			const r = await fetch(apiUrl('/agents?view=removed'), { credentials: 'include' });
			if (!r.ok) throw new Error(`Failed to load removed (${r.status})`);
			return r.json() as Promise<{ agents: AgentRow[] }>;
		},
	});

	const agentDetailQuery = useQuery({
		queryKey: ['agents', selectedAgentId, 'detail'],
		queryFn: async () => {
			const aid = selectedAgentId;
			if (!aid) throw new Error('Missing agent');
			const r = await fetch(apiUrl(`/agents/${encodeURIComponent(aid)}`), {
				credentials: 'include',
			});
			if (!r.ok) throw await parseApiError(r);
			return r.json() as Promise<AgentDetail>;
		},
		enabled: selectedAgentId !== null,
	});

	const grantsForSelectedQuery = useQuery({
		queryKey: ['agents', selectedAgentId, 'grants'],
		queryFn: async () => {
			const aid = selectedAgentId;
			if (!aid) throw new Error('Missing agent');
			const r = await fetch(apiUrl(`/agents/${encodeURIComponent(aid)}/grants`), {
				credentials: 'include',
			});
			if (!r.ok) throw await parseApiError(r);
			return r.json() as Promise<{
				grants: { toolkit_id: string; granted_at: number; granted_by: string | null }[];
			}>;
		},
		enabled: selectedAgentId !== null,
	});

	const credentialRollupQuery = useQuery({
		queryKey: ['agents', selectedAgentId, 'credential-rollup'],
		queryFn: async () => {
			const aid = selectedAgentId;
			if (!aid) throw new Error('Missing agent');
			const gr = await fetch(apiUrl(`/agents/${encodeURIComponent(aid)}/grants`), {
				credentials: 'include',
			});
			if (!gr.ok) throw await parseApiError(gr);
			const { grants } = (await gr.json()) as { grants: { toolkit_id: string }[] };
			const toolkitResults = await Promise.all(
				grants.map(async ({ toolkit_id }) => {
					const tr = await fetch(apiUrl(`/toolkits/${encodeURIComponent(toolkit_id)}`), {
						credentials: 'include',
						headers: { Accept: 'application/json' },
					});
					if (!tr.ok) return { toolkit_id, credentials: [] as ToolkitCredentialRow[] };
					const data = (await tr.json()) as { credentials?: ToolkitCredentialRow[] };
					return { toolkit_id, credentials: data.credentials ?? [] };
				}),
			);
			const byCred = new Map<string, MergedCredentialRow>();
			for (const { toolkit_id, credentials } of toolkitResults) {
				for (const c of credentials) {
					const id = c.credential_id;
					if (!id) continue;
					if (!byCred.has(id)) {
						byCred.set(id, {
							credential_id: id,
							label: c.label ?? null,
							api_id: c.api_id ?? null,
							via_toolkits: [],
						});
					}
					const row = byCred.get(id);
					if (row && !row.via_toolkits.includes(toolkit_id))
						row.via_toolkits.push(toolkit_id);
				}
			}
			return [...byCred.values()].sort((a, b) =>
				(a.label || a.credential_id).localeCompare(b.label || b.credential_id, undefined, {
					sensitivity: 'base',
				}),
			);
		},
		enabled: selectedAgentId !== null,
	});

	const approveMutation = useMutation({
		mutationFn: async (clientId: string) => {
			const r = await fetch(apiUrl(`/agents/${encodeURIComponent(clientId)}/approve`), {
				method: 'POST',
				credentials: 'include',
			});
			if (!r.ok) throw await parseApiError(r);
		},
		onSuccess: () => {
			void queryClient.invalidateQueries({ queryKey: ['agents'] });
		},
	});

	const denyMutation = useMutation({
		mutationFn: async (clientId: string) => {
			const r = await fetch(apiUrl(`/agents/${encodeURIComponent(clientId)}/deny`), {
				method: 'POST',
				credentials: 'include',
			});
			if (!r.ok) throw await parseApiError(r);
		},
		onSuccess: () => {
			setDeclineAgentId(null);
			void queryClient.invalidateQueries({ queryKey: ['agents'] });
		},
	});

	const disableMutation = useMutation({
		mutationFn: async (clientId: string) => {
			const r = await fetch(apiUrl(`/agents/${encodeURIComponent(clientId)}/disable`), {
				method: 'POST',
				credentials: 'include',
			});
			if (!r.ok) throw await parseApiError(r);
		},
		onSuccess: (_data, clientId) => {
			setKillswitchAgentId(null);
			void queryClient.invalidateQueries({ queryKey: ['agents'] });
			void queryClient.invalidateQueries({ queryKey: ['agents', clientId] });
		},
	});

	const enableMutation = useMutation({
		mutationFn: async (clientId: string) => {
			const r = await fetch(apiUrl(`/agents/${encodeURIComponent(clientId)}/enable`), {
				method: 'POST',
				credentials: 'include',
			});
			if (!r.ok) throw await parseApiError(r);
		},
		onSuccess: (_data, clientId) => {
			void queryClient.invalidateQueries({ queryKey: ['agents'] });
			void queryClient.invalidateQueries({ queryKey: ['agents', clientId] });
		},
	});

	const deregisterMutation = useMutation({
		mutationFn: async (clientId: string) => {
			const r = await fetch(apiUrl(`/agents/${encodeURIComponent(clientId)}`), {
				method: 'DELETE',
				credentials: 'include',
			});
			if (!r.ok) throw await parseApiError(r);
		},
		onSuccess: (_data, clientId) => {
			setDeregisterClientId(null);
			if (grantAgentId === clientId) setGrantAgentId(null);
			if (selectedAgentId === clientId) setSelectedAgentId(null);
			void queryClient.invalidateQueries({ queryKey: ['agents'] });
		},
	});

	const toolkitsForGrantQuery = useQuery({
		queryKey: ['toolkits'],
		queryFn: async () => {
			const r = await fetch(apiUrl('/toolkits'), { credentials: 'include' });
			if (!r.ok) throw await parseApiError(r);
			return r.json() as Promise<ToolkitRow[]>;
		},
		enabled: grantAgentId !== null,
	});

	const grantsForAgentQuery = useQuery({
		queryKey: ['agents', grantAgentId, 'grants-edit'],
		queryFn: async () => {
			const gid = grantAgentId;
			if (!gid) throw new Error('Missing agent');
			const r = await fetch(apiUrl(`/agents/${encodeURIComponent(gid)}/grants`), {
				credentials: 'include',
			});
			if (!r.ok) throw await parseApiError(r);
			return r.json() as Promise<{ grants: { toolkit_id: string }[] }>;
		},
		enabled: grantAgentId !== null,
	});

	useEffect(() => {
		if (!grantAgentId) {
			grantSelectionInitRef.current = null;
			return;
		}
		if (!grantsForAgentQuery.isSuccess || !toolkitsForGrantQuery.isSuccess) return;
		if (grantSelectionInitRef.current === grantAgentId) return;
		grantSelectionInitRef.current = grantAgentId;
		const next = new Set(grantsForAgentQuery.data.grants.map((g) => g.toolkit_id));
		next.add(DEFAULT_TOOLKIT_ID);
		setSelectedToolkitIds(next);
	}, [
		grantAgentId,
		grantsForAgentQuery.isSuccess,
		grantsForAgentQuery.data,
		toolkitsForGrantQuery.isSuccess,
		toolkitsForGrantQuery.data,
	]);

	const saveGrantsMutation = useMutation({
		mutationFn: async ({ clientId, desired }: { clientId: string; desired: Set<string> }) => {
			const r = await fetch(apiUrl(`/agents/${encodeURIComponent(clientId)}/grants`), {
				method: 'PUT',
				credentials: 'include',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ toolkit_ids: [...desired] }),
			});
			if (!r.ok) throw await parseApiError(r);
		},
		onSuccess: () => {
			const id = grantAgentId;
			setGrantAgentId(null);
			grantSelectionInitRef.current = null;
			void queryClient.invalidateQueries({ queryKey: ['agents'] });
			if (id) void queryClient.invalidateQueries({ queryKey: ['agents', id] });
		},
	});

	function toggleToolkitSelection(toolkitId: string, toolkitDisabled: boolean) {
		setSelectedToolkitIds((prev: Set<string>) => {
			const next = new Set(prev);
			if (next.has(toolkitId)) {
				next.delete(toolkitId);
				return next;
			}
			if (toolkitDisabled) return prev;
			next.add(toolkitId);
			return next;
		});
	}

	function showKillswitchForStatus(status: string) {
		return status === 'approved';
	}

	const columns: Column<AgentRow>[] = [
		{ key: 'client_id', header: 'Client ID' },
		{ key: 'client_name', header: 'Name' },
		{ key: 'status', header: 'Status' },
		{
			key: 'actions',
			header: 'Actions',
			render: (row) => (
				// eslint-disable-next-line jsx-a11y/no-static-element-interactions, jsx-a11y/click-events-have-key-events -- absorb pointer events so row onClick does not fire
				<div className="flex flex-wrap gap-2" onClick={stopRowClick}>
					{row.status === 'pending' && (
						<>
							<Button
								size="sm"
								variant="secondary"
								loading={
									approveMutation.isPending &&
									approveMutation.variables === row.client_id
								}
								disabled={
									(approveMutation.isPending || denyMutation.isPending) &&
									(approveMutation.variables === row.client_id ||
										denyMutation.variables === row.client_id)
								}
								onClick={() => approveMutation.mutate(row.client_id)}
							>
								Approve
							</Button>
							<Button
								size="sm"
								variant="outline"
								loading={
									denyMutation.isPending &&
									denyMutation.variables === row.client_id
								}
								disabled={
									(approveMutation.isPending || denyMutation.isPending) &&
									(approveMutation.variables === row.client_id ||
										denyMutation.variables === row.client_id)
								}
								onClick={() => setDeclineAgentId(row.client_id)}
							>
								Decline
							</Button>
						</>
					)}
					{row.status === 'approved' && (
						<Button
							size="sm"
							variant="outline"
							onClick={() => setGrantAgentId(row.client_id)}
						>
							Toolkits
						</Button>
					)}
					{showKillswitchForStatus(row.status) && (
						<Button
							size="sm"
							variant="danger"
							loading={
								disableMutation.isPending &&
								disableMutation.variables === row.client_id
							}
							onClick={() => setKillswitchAgentId(row.client_id)}
						>
							Killswitch
						</Button>
					)}
					{row.status === 'disabled' && (
						<Button
							size="sm"
							variant="outline"
							loading={
								enableMutation.isPending &&
								enableMutation.variables === row.client_id
							}
							onClick={() => enableMutation.mutate(row.client_id)}
						>
							Re-enable
						</Button>
					)}
					{(row.status === 'approved' || row.status === 'disabled') && (
						<Button
							size="sm"
							variant="danger"
							onClick={() => setDeregisterClientId(row.client_id)}
						>
							Deregister
						</Button>
					)}
				</div>
			),
		},
	];

	const declinedColumns: Column<AgentRow>[] = [
		{ key: 'client_id', header: 'Client ID' },
		{ key: 'client_name', header: 'Name' },
		{ key: 'status', header: 'Status' },
		{
			key: 'denied_at',
			header: 'Declined',
			render: (row) => formatTs(row.denied_at ?? null),
		},
	];

	const removedColumns: Column<AgentRow>[] = [
		{ key: 'client_id', header: 'Client ID' },
		{ key: 'client_name', header: 'Name' },
		{ key: 'status', header: 'Status' },
		{
			key: 'deleted_at',
			header: 'Removed',
			render: (row) => formatTs(row.deleted_at ?? null),
		},
	];

	if (
		activeAgentsQuery.isLoading ||
		declinedAgentsQuery.isLoading ||
		removedAgentsQuery.isLoading
	) {
		return (
			<PageShell>
				<LoadingState message="Loading agents…" />
			</PageShell>
		);
	}
	if (activeAgentsQuery.isError || declinedAgentsQuery.isError || removedAgentsQuery.isError) {
		return (
			<PageShell>
				<ErrorAlert message="Could not load agents. Log in as admin and try again." />
			</PageShell>
		);
	}

	const agents = activeAgentsQuery.data?.agents ?? [];
	const declinedAgents = declinedAgentsQuery.data?.agents ?? [];
	const removedAgents = removedAgentsQuery.data?.agents ?? [];
	const detail = agentDetailQuery.data;
	const jwk0 = detail?.jwks?.keys?.[0];
	const detailIsRemoved = detail != null && detail.deleted_at != null;
	const detailIsArchive = detail != null && (detailIsRemoved || detail.status === 'denied');

	return (
		<PageShell>
			<PageHeader
				title="Agents"
				subtitle="Click an agent for details, toolkit access, and reachable credentials."
			/>

			<div className="mt-6">
				<DataTable<AgentRow>
					columns={columns}
					data={agents}
					getRowKey={(r) => r.client_id}
					emptyMessage="No agents registered yet."
					onRowClick={(row) => setSelectedAgentId(row.client_id)}
				/>
			</div>

			<details className="border-border bg-muted/20 mt-8 rounded-lg border">
				<summary className="text-foreground cursor-pointer list-none px-4 py-3 text-sm font-medium [&::-webkit-details-marker]:hidden">
					Declined registrations ({declinedAgents.length})
				</summary>
				<div className="border-border border-t px-2 pt-2 pb-4">
					{declinedAgents.length === 0 ? (
						<p className="text-muted-foreground px-4 py-2 text-sm">None.</p>
					) : (
						<DataTable<AgentRow>
							columns={declinedColumns}
							data={declinedAgents}
							getRowKey={(r) => r.client_id}
							emptyMessage="None."
							onRowClick={(row) => setSelectedAgentId(row.client_id)}
						/>
					)}
				</div>
			</details>

			<details className="border-border bg-muted/20 mt-4 rounded-lg border">
				<summary className="text-foreground cursor-pointer list-none px-4 py-3 text-sm font-medium [&::-webkit-details-marker]:hidden">
					Removed agents ({removedAgents.length})
				</summary>
				<div className="border-border border-t px-2 pt-2 pb-4">
					{removedAgents.length === 0 ? (
						<p className="text-muted-foreground px-4 py-2 text-sm">None.</p>
					) : (
						<DataTable<AgentRow>
							columns={removedColumns}
							data={removedAgents}
							getRowKey={(r) => r.client_id}
							emptyMessage="None."
							onRowClick={(row) => setSelectedAgentId(row.client_id)}
						/>
					)}
				</div>
			</details>

			<Dialog
				open={selectedAgentId !== null}
				onClose={() => setSelectedAgentId(null)}
				title={detail ? `${detail.client_name}` : 'Agent'}
				size="lg"
				footer={
					<>
						<Button variant="ghost" onClick={() => setSelectedAgentId(null)}>
							Close
						</Button>
						{detail && !detailIsRemoved && showKillswitchForStatus(detail.status) && (
							<Button
								variant="danger"
								onClick={() => setKillswitchAgentId(detail.client_id)}
							>
								Killswitch
							</Button>
						)}
						{detail && !detailIsRemoved && detail.status === 'disabled' && (
							<Button
								variant="secondary"
								loading={
									enableMutation.isPending &&
									enableMutation.variables === detail.client_id
								}
								onClick={() => enableMutation.mutate(detail.client_id)}
							>
								Re-enable
							</Button>
						)}
						{detail &&
							!detailIsRemoved &&
							(detail.status === 'approved' || detail.status === 'disabled') && (
								<Button
									variant="danger"
									onClick={() => setDeregisterClientId(detail.client_id)}
								>
									Deregister
								</Button>
							)}
					</>
				}
			>
				{agentDetailQuery.isLoading ? (
					<LoadingState message="Loading agent…" />
				) : agentDetailQuery.isError ? (
					<ErrorAlert message="Could not load this agent." />
				) : detail ? (
					<div className="space-y-6">
						<div>
							<p className="text-muted-foreground font-mono text-xs">
								{detail.client_id}
							</p>
							<p className="text-foreground mt-1">
								<span className="font-semibold">Status:</span> {detail.status}
							</p>
						</div>
						<dl className="border-border text-foreground grid gap-2 border-t border-b py-3 text-sm sm:grid-cols-2">
							<dt className="font-medium">Registered</dt>
							<dd>{formatTs(detail.created_at)}</dd>
							<dt className="font-medium">Approved</dt>
							<dd>{formatTs(detail.approved_at)}</dd>
							{detail.approved_by ? (
								<>
									<dt className="font-medium">Approved by</dt>
									<dd>{detail.approved_by}</dd>
								</>
							) : null}
							<dt className="font-medium">Denied</dt>
							<dd>{formatTs(detail.denied_at)}</dd>
							<dt className="font-medium">Disabled</dt>
							<dd>{formatTs(detail.disabled_at)}</dd>
							{detailIsRemoved ? (
								<>
									<dt className="font-medium">Removed</dt>
									<dd>{formatTs(detail.deleted_at)}</dd>
								</>
							) : null}
						</dl>
						<div>
							<h3 className="text-foreground mb-2 text-sm font-semibold">
								Public key (JWKS)
							</h3>
							<p className="text-muted-foreground text-xs">
								{jwk0
									? `${jwk0.kty ?? 'OKP'} / ${jwk0.crv ?? '—'}${jwk0.kid ? ` · kid: ${jwk0.kid}` : ''}`
									: detailIsArchive
										? 'Public key removed (read-only archive).'
										: 'No key metadata'}
							</p>
						</div>
						<div>
							<div className="mb-2 flex flex-wrap items-center justify-between gap-2">
								<h3 className="text-foreground text-sm font-semibold">
									Toolkit grants
								</h3>
								{detail.status === 'approved' && !detailIsRemoved && (
									<Button
										size="sm"
										variant="outline"
										onClick={() => setGrantAgentId(detail.client_id)}
									>
										Manage toolkit access
									</Button>
								)}
							</div>
							{grantsForSelectedQuery.isLoading ? (
								<p className="text-muted-foreground text-sm">Loading grants…</p>
							) : grantsForSelectedQuery.isError ? (
								<ErrorAlert message="Could not load grants." />
							) : (grantsForSelectedQuery.data?.grants.length ?? 0) === 0 ? (
								<p className="text-muted-foreground text-sm">
									{detailIsArchive
										? 'No toolkit grants (cleared when declined or removed).'
										: 'No toolkit grants yet.'}
								</p>
							) : (
								<ul className="border-border max-h-40 overflow-y-auto rounded-md border text-sm">
									{(grantsForSelectedQuery.data?.grants ?? []).map((g) => (
										<li
											key={g.toolkit_id}
											className="border-border flex justify-between border-b px-3 py-2 last:border-0"
										>
											<code className="text-foreground">{g.toolkit_id}</code>
											<span className="text-muted-foreground text-xs">
												{formatTs(g.granted_at)}
												{g.granted_by ? ` · ${g.granted_by}` : ''}
											</span>
										</li>
									))}
								</ul>
							)}
						</div>
						<div>
							<h3 className="text-foreground mb-2 text-sm font-semibold">
								Reachable credentials
							</h3>
							<p className="text-muted-foreground mb-3 text-xs">
								Combined in the browser from each granted toolkit (
								<code className="bg-background rounded px-1">
									GET /toolkits/&lt;id&gt;
								</code>
								). Duplicate credentials across toolkits are merged.
							</p>
							{credentialRollupQuery.isLoading ? (
								<p className="text-muted-foreground text-sm">
									Loading credentials…
								</p>
							) : credentialRollupQuery.isError ? (
								<ErrorAlert message="Could not build credential list from toolkits." />
							) : (credentialRollupQuery.data?.length ?? 0) === 0 ? (
								<p className="text-muted-foreground text-sm">
									{detailIsArchive
										? 'No credentials roll-up for archived agents.'
										: 'No credentials (add toolkit grants that include bound credentials).'}
								</p>
							) : (
								<div className="border-border max-h-56 overflow-auto rounded-md border">
									<table className="w-full text-left text-sm">
										<thead>
											<tr className="border-border bg-muted/40 border-b">
												<th className="text-muted-foreground px-3 py-2 text-xs font-medium uppercase">
													Label
												</th>
												<th className="text-muted-foreground px-3 py-2 text-xs font-medium uppercase">
													API
												</th>
												<th className="text-muted-foreground px-3 py-2 text-xs font-medium uppercase">
													Via toolkits
												</th>
											</tr>
										</thead>
										<tbody>
											{(credentialRollupQuery.data ?? []).map((c) => (
												<tr
													key={c.credential_id}
													className="border-border border-b last:border-0"
												>
													<td className="px-3 py-2">
														<span className="text-foreground">
															{c.label || '—'}
														</span>
														<div className="text-muted-foreground font-mono text-[10px]">
															{c.credential_id}
														</div>
													</td>
													<td className="text-muted-foreground px-3 py-2 font-mono text-xs">
														{c.api_id || '—'}
													</td>
													<td className="text-muted-foreground px-3 py-2 text-xs">
														{c.via_toolkits.join(', ')}
													</td>
												</tr>
											))}
										</tbody>
									</table>
								</div>
							)}
						</div>
					</div>
				) : null}
			</Dialog>

			<Dialog
				open={killswitchAgentId !== null}
				onClose={() => {
					if (!disableMutation.isPending) setKillswitchAgentId(null);
				}}
				title="Killswitch this agent?"
				size="sm"
				footer={
					<>
						<Button
							variant="ghost"
							disabled={disableMutation.isPending}
							onClick={() => setKillswitchAgentId(null)}
						>
							Cancel
						</Button>
						<Button
							variant="danger"
							loading={disableMutation.isPending}
							onClick={() => {
								if (killswitchAgentId)
									void disableMutation.mutateAsync(killswitchAgentId);
							}}
						>
							Disable &amp; revoke tokens
						</Button>
					</>
				}
			>
				<p className="text-foreground text-sm">
					This sets the agent to <strong>disabled</strong>, revokes all issued{' '}
					<code className="bg-background rounded px-1 text-xs">at_</code> /{' '}
					<code className="bg-background rounded px-1 text-xs">rt_</code> tokens, and
					blocks broker access until you re-enable them.
				</p>
				{killswitchAgentId && (
					<p className="text-muted-foreground mt-2 font-mono text-xs">
						{killswitchAgentId}
					</p>
				)}
				{disableMutation.isError && (
					<ErrorAlert className="mt-3" message="Killswitch failed. Try again." />
				)}
			</Dialog>

			<Dialog
				open={declineAgentId !== null}
				onClose={() => {
					if (!denyMutation.isPending) setDeclineAgentId(null);
				}}
				title="Decline this registration?"
				size="sm"
				footer={
					<>
						<Button
							variant="ghost"
							disabled={denyMutation.isPending}
							onClick={() => setDeclineAgentId(null)}
						>
							Cancel
						</Button>
						<Button
							variant="danger"
							loading={denyMutation.isPending}
							onClick={() => {
								if (declineAgentId) void denyMutation.mutateAsync(declineAgentId);
							}}
						>
							Decline
						</Button>
					</>
				}
			>
				<p className="text-foreground text-sm">
					Declining is <strong>terminal</strong> — the agent record is archived and the
					registration cannot be re-approved. The agent must register again from scratch
					with a fresh client_id.
				</p>
				{declineAgentId && (
					<p className="text-muted-foreground mt-2 font-mono text-xs">{declineAgentId}</p>
				)}
				{denyMutation.isError && (
					<ErrorAlert
						className="mt-3"
						message={
							(denyMutation.error as Error).message ?? 'Decline failed. Try again.'
						}
					/>
				)}
			</Dialog>

			<Dialog
				open={deregisterClientId !== null}
				onClose={() => {
					if (!deregisterMutation.isPending) setDeregisterClientId(null);
				}}
				title="Deregister agent?"
				size="sm"
				footer={
					<>
						<Button
							variant="ghost"
							disabled={deregisterMutation.isPending}
							onClick={() => setDeregisterClientId(null)}
						>
							Cancel
						</Button>
						<Button
							variant="danger"
							loading={deregisterMutation.isPending}
							onClick={() => {
								if (deregisterClientId)
									void deregisterMutation.mutateAsync(deregisterClientId);
							}}
						>
							Deregister
						</Button>
					</>
				}
			>
				<p className="text-foreground text-sm">
					This deregisters{' '}
					<code className="bg-background rounded px-1 py-0.5 font-mono text-xs">
						{deregisterClientId}
					</code>
					: tokens are revoked, the public key and toolkit grants are removed, and the row
					is kept as a read-only archive. The agent must register again with a new key to
					use Jentic.
				</p>
				{deregisterMutation.isError && (
					<ErrorAlert
						className="mt-3"
						message="Deregister failed. Try again or check the server response."
					/>
				)}
			</Dialog>

			<Dialog
				open={grantAgentId !== null}
				onClose={() => {
					if (!saveGrantsMutation.isPending) {
						setGrantAgentId(null);
						grantSelectionInitRef.current = null;
					}
				}}
				title={grantAgentId ? `Toolkit access — ${grantAgentId}` : 'Toolkit access'}
				size="md"
				footer={
					<>
						<Button
							variant="ghost"
							disabled={saveGrantsMutation.isPending}
							onClick={() => {
								setGrantAgentId(null);
								grantSelectionInitRef.current = null;
							}}
						>
							Cancel
						</Button>
						<Button
							variant="primary"
							loading={saveGrantsMutation.isPending}
							disabled={
								!grantAgentId ||
								!grantsForAgentQuery.isSuccess ||
								!toolkitsForGrantQuery.isSuccess
							}
							onClick={() => {
								if (!grantAgentId || !grantsForAgentQuery.data) return;
								void saveGrantsMutation.mutateAsync({
									clientId: grantAgentId,
									desired: selectedToolkitIds,
								});
							}}
						>
							Save access
						</Button>
					</>
				}
			>
				{toolkitsForGrantQuery.isLoading || grantsForAgentQuery.isLoading ? (
					<LoadingState message="Loading toolkits…" />
				) : toolkitsForGrantQuery.isError || grantsForAgentQuery.isError ? (
					<ErrorAlert message="Could not load toolkits or current access. Try again." />
				) : (
					<>
						<p className="text-muted-foreground mb-4 text-sm">
							Choose which toolkits this agent may use. The default toolkit is
							selected automatically; you can add or remove access before saving.
						</p>
						<ul className="border-border max-h-[min(24rem,55vh)] space-y-2 overflow-y-auto rounded-md border p-3">
							{[...(toolkitsForGrantQuery.data ?? [])]
								.sort((a, b) => {
									if (a.id === DEFAULT_TOOLKIT_ID) return -1;
									if (b.id === DEFAULT_TOOLKIT_ID) return 1;
									return a.name.localeCompare(b.name, undefined, {
										sensitivity: 'base',
									});
								})
								.map((tk) => {
									const checked = selectedToolkitIds.has(tk.id);
									const blockCheck = tk.disabled && !checked;
									return (
										<li key={tk.id}>
											<label
												className={`hover:bg-muted/60 flex cursor-pointer items-start gap-3 rounded-md px-2 py-2 ${tk.disabled ? 'opacity-80' : ''}`}
											>
												{/* eslint-disable-next-line no-restricted-syntax -- No Checkbox primitive yet */}
												<input
													type="checkbox"
													className="text-primary mt-1 size-4 shrink-0"
													checked={checked}
													disabled={blockCheck}
													onChange={() =>
														toggleToolkitSelection(tk.id, tk.disabled)
													}
												/>
												<span className="min-w-0 flex-1">
													<span className="text-foreground font-medium">
														{tk.name}
													</span>
													<code className="text-muted-foreground ml-2 text-xs">
														{tk.id}
													</code>
													{tk.disabled && (
														<span className="text-danger ml-2 text-xs">
															(disabled)
														</span>
													)}
													{tk.description ? (
														<span className="text-muted-foreground mt-0.5 block text-xs">
															{tk.description}
														</span>
													) : null}
												</span>
											</label>
										</li>
									);
								})}
						</ul>
						{saveGrantsMutation.isError && (
							<ErrorAlert
								className="mt-3"
								message="Save failed. You cannot grant disabled toolkits; check the server response."
							/>
						)}
					</>
				)}
			</Dialog>
		</PageShell>
	);
}
