/**
 * Agents page — operator surface for the agent & service-account lifecycle.
 *
 * Two sections behind a segmented tab:
 *   - Agents: registrations created via RFC-7591 dynamic client registration.
 *     Operators approve/deny pending ones and disable/enable/archive the rest.
 *   - Service accounts: non-human callers; create + the same lifecycle.
 *
 * The lifecycle vocabulary is the backend `Actor*` enums (status + verbs). This
 * view owns the confirm-dialog orchestration and routes each action to the
 * matching hook; it never touches `@/shared/api` directly (ESLint-enforced).
 */
import { useMemo, useState } from 'react';
import { Plus } from 'lucide-react';
import {
	Button,
	CascadeDeleteDialog,
	PageShell,
	PageHeader,
	PageHelp,
	SegmentedToggle,
	ErrorAlert,
} from '@/shared/ui';
import {
	useAgents,
	useApproveAgent,
	useDenyAgent,
	useDisableAgent,
	useEnableAgent,
	useArchiveAgent,
	useServiceAccounts,
	useApproveServiceAccount,
	useDenyServiceAccount,
	useDisableServiceAccount,
	useEnableServiceAccount,
	useArchiveServiceAccount,
	type AgentEntity,
	type ServiceAccountEntity,
	type AgentAction,
} from '@/modules/agents/api';
import { AgentRoster, ServiceAccountRoster } from '@/modules/agents/components/ActorRoster';
import { AgentCreateSheet } from '@/modules/agents/components/AgentCreateSheet';
import { ServiceAccountCreateSheet } from '@/modules/agents/components/ServiceAccountCreateSheet';
import { DenyDialog } from '@/modules/agents/components/confirm/DenyDialog';
import { ConfirmDialog } from '@/modules/agents/components/confirm/ConfirmDialog';

type Tab = 'agents' | 'service-accounts';

/** A pending lifecycle action awaiting confirmation in a dialog. */
type PendingConfirm =
	| { kind: 'deny'; id: string; name: string }
	| { kind: 'disable'; id: string; name: string }
	| { kind: 'archive'; id: string; name: string }
	| null;

export default function AgentsPage() {
	const [tab, setTab] = useState<Tab>('agents');
	const [createOpen, setCreateOpen] = useState(false);
	const [agentCreateOpen, setAgentCreateOpen] = useState(false);

	function selectTab(next: Tab) {
		if (next !== 'service-accounts') setCreateOpen(false);
		if (next !== 'agents') setAgentCreateOpen(false);
		setTab(next);
	}

	return (
		<PageShell>
			<PageHeader
				title="Agents"
				subtitle="Approve, deny, and govern agents and service accounts across their lifecycle."
				actions={
					<PageHelp
						title="About Agents"
						intro={
							<p>
								Agents register themselves via dynamic client registration and land
								here as <strong>pending</strong>. Approve one to make it active, or
								deny it with a reason.
							</p>
						}
						sections={[
							{
								heading: 'Lifecycle',
								body: (
									<p>
										<strong>Pending</strong> → approve (→ active) or deny (→
										rejected). <strong>Active</strong> can be disabled;{' '}
										<strong>disabled</strong> can be re-enabled. Any
										non-archived actor can be archived (terminal).
									</p>
								),
							},
							{
								heading: 'Service accounts',
								body: (
									<p>
										Service accounts represent non-human callers. Create one
										here; it starts pending and follows the same lifecycle.
									</p>
								),
							},
						]}
					/>
				}
			/>

			<div className="flex flex-wrap items-center justify-between gap-3">
				<SegmentedToggle<Tab>
					options={[
						{ value: 'agents', label: 'Agents' },
						{ value: 'service-accounts', label: 'Service accounts' },
					]}
					value={tab}
					onChange={selectTab}
					layoutId="agents-tab"
					className="w-fit"
				/>
				{tab === 'agents' && (
					<Button size="sm" onClick={() => setAgentCreateOpen(true)}>
						<Plus className="h-4 w-4" />
						New agent
					</Button>
				)}
				{tab === 'service-accounts' && (
					<Button size="sm" onClick={() => setCreateOpen(true)}>
						<Plus className="h-4 w-4" />
						New service account
					</Button>
				)}
			</div>

			{tab === 'agents' ? (
				<AgentsSection createOpen={agentCreateOpen} setCreateOpen={setAgentCreateOpen} />
			) : (
				<ServiceAccountsSection createOpen={createOpen} setCreateOpen={setCreateOpen} />
			)}
		</PageShell>
	);
}

// ---------------------------------------------------------------------------
// Agents section
// ---------------------------------------------------------------------------

function AgentsSection({
	createOpen,
	setCreateOpen,
}: {
	createOpen: boolean;
	setCreateOpen: (open: boolean) => void;
}) {
	const [confirm, setConfirm] = useState<PendingConfirm>(null);

	const agents = useAgents({ status: 'all' });
	const approve = useApproveAgent();
	const deny = useDenyAgent();
	const disable = useDisableAgent();
	const enable = useEnableAgent();
	const archive = useArchiveAgent();

	const entities = useMemo(() => agents.data?.entities ?? [], [agents.data]);
	const pendingId = activeId([approve, deny, disable, enable, archive]);

	function handleAction(agent: AgentEntity, action: AgentAction) {
		switch (action) {
			case 'approve':
				approve.mutate(agent.id);
				break;
			case 'enable':
				enable.mutate(agent.id);
				break;
			case 'deny':
				setConfirm({ kind: 'deny', id: agent.id, name: agent.name });
				break;
			case 'disable':
				setConfirm({ kind: 'disable', id: agent.id, name: agent.name });
				break;
			case 'archive':
				setConfirm({ kind: 'archive', id: agent.id, name: agent.name });
				break;
		}
	}

	return (
		<>
			{agents.error ? (
				<ErrorAlert message={agents.error as Error} />
			) : (
				<AgentRoster
					agents={entities}
					isLoading={agents.isPending}
					pendingId={pendingId}
					onAction={handleAction}
				/>
			)}

			<AgentCreateSheet open={createOpen} onClose={() => setCreateOpen(false)} />

			<DenyDialog
				open={confirm?.kind === 'deny'}
				subjectName={confirm?.kind === 'deny' ? confirm.name : null}
				pending={deny.isPending}
				onConfirm={async (reason) => {
					if (confirm?.kind !== 'deny') return;
					try {
						await deny.mutateAsync({ id: confirm.id, reason });
						setConfirm(null);
					} catch {
						// onError toasts; keep the dialog open so the user can retry.
					}
				}}
				onClose={() => setConfirm(null)}
			/>

			<ConfirmDialog
				open={confirm?.kind === 'disable'}
				title={confirm?.kind === 'disable' ? `Disable ${confirm.name}` : 'Disable'}
				body="Disabling immediately revokes this agent's ability to authenticate. You can re-enable it later."
				confirmLabel="Disable"
				pending={disable.isPending}
				onConfirm={async () => {
					if (confirm?.kind !== 'disable') return;
					try {
						await disable.mutateAsync(confirm.id);
						setConfirm(null);
					} catch {
						// onError toasts; keep the dialog open so the user can retry.
					}
				}}
				onClose={() => setConfirm(null)}
			/>

			{confirm?.kind === 'archive' && (
				<CascadeDeleteDialog
					open
					entityType="agent"
					entityName={confirm.name}
					loading={archive.isPending}
					error={archive.error}
					onConfirm={async () => {
						try {
							await archive.mutateAsync(confirm.id);
							setConfirm(null);
						} catch {
							// onError toasts; keep the dialog open so the user can retry.
						}
					}}
					onClose={() => setConfirm(null)}
				/>
			)}
		</>
	);
}

// ---------------------------------------------------------------------------
// Service accounts section
// ---------------------------------------------------------------------------

function ServiceAccountsSection({
	createOpen,
	setCreateOpen,
}: {
	createOpen: boolean;
	setCreateOpen: (open: boolean) => void;
}) {
	const [confirm, setConfirm] = useState<PendingConfirm>(null);

	const accounts = useServiceAccounts({ status: 'all' });
	const approve = useApproveServiceAccount();
	const deny = useDenyServiceAccount();
	const disable = useDisableServiceAccount();
	const enable = useEnableServiceAccount();
	const archive = useArchiveServiceAccount();

	const entities = useMemo(() => accounts.data?.entities ?? [], [accounts.data]);
	const pendingId = activeId([approve, deny, disable, enable, archive]);

	function handleAction(account: ServiceAccountEntity, action: AgentAction) {
		switch (action) {
			case 'approve':
				approve.mutate(account.id);
				break;
			case 'enable':
				enable.mutate(account.id);
				break;
			case 'deny':
				setConfirm({ kind: 'deny', id: account.id, name: account.name });
				break;
			case 'disable':
				setConfirm({ kind: 'disable', id: account.id, name: account.name });
				break;
			case 'archive':
				setConfirm({ kind: 'archive', id: account.id, name: account.name });
				break;
		}
	}

	return (
		<>
			{accounts.error ? (
				<ErrorAlert message={accounts.error as Error} />
			) : (
				<ServiceAccountRoster
					accounts={entities}
					isLoading={accounts.isPending}
					pendingId={pendingId}
					onAction={handleAction}
				/>
			)}

			<ServiceAccountCreateSheet open={createOpen} onClose={() => setCreateOpen(false)} />

			<DenyDialog
				open={confirm?.kind === 'deny'}
				subjectName={confirm?.kind === 'deny' ? confirm.name : null}
				pending={deny.isPending}
				onConfirm={async (reason) => {
					if (confirm?.kind !== 'deny') return;
					try {
						await deny.mutateAsync({ id: confirm.id, reason });
						setConfirm(null);
					} catch {
						// onError toasts; keep the dialog open so the user can retry.
					}
				}}
				onClose={() => setConfirm(null)}
			/>

			<ConfirmDialog
				open={confirm?.kind === 'disable'}
				title={confirm?.kind === 'disable' ? `Disable ${confirm.name}` : 'Disable'}
				body="Disabling immediately revokes this service account's access. You can re-enable it later."
				confirmLabel="Disable"
				pending={disable.isPending}
				onConfirm={async () => {
					if (confirm?.kind !== 'disable') return;
					try {
						await disable.mutateAsync(confirm.id);
						setConfirm(null);
					} catch {
						// onError toasts; keep the dialog open so the user can retry.
					}
				}}
				onClose={() => setConfirm(null)}
			/>

			{confirm?.kind === 'archive' && (
				<CascadeDeleteDialog
					open
					entityType="service-account"
					entityName={confirm.name}
					loading={archive.isPending}
					error={archive.error}
					onConfirm={async () => {
						try {
							await archive.mutateAsync(confirm.id);
							setConfirm(null);
						} catch {
							// onError toasts; keep the dialog open so the user can retry.
						}
					}}
					onClose={() => setConfirm(null)}
				/>
			)}
		</>
	);
}

/** The id currently in flight across a set of single-arg mutations. */
function activeId(mutations: { isPending: boolean; variables?: unknown }[]): string | null {
	const active = mutations.find((m) => m.isPending);
	if (!active) return null;
	const v = active.variables;
	if (typeof v === 'string') return v;
	if (v && typeof v === 'object' && 'id' in v) return String((v as { id: unknown }).id);
	return null;
}
