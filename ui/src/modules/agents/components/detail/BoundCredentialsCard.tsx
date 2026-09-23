/**
 * BoundCredentialsCard — the "Bound credentials" card on the agent detail
 * Access tab: the agent's DIRECT credential bindings (theme 5 phase 5a,
 * `GET /agents/{id}/credentials`) with the transplanted bind wizard, per-row
 * rule editor + broker dry-run tester, and the suspend / resume / permanent
 * unbind lifecycle.
 *
 * Unbind semantics (phase-1 contract): the default DELETE SUSPENDS the
 * binding — reversible, the rules survive, `:resume` restores access — while
 * `purge=true` deletes the binding and its rules outright. The two actions
 * render with proportionate confirms (inline vs. the stronger copy).
 *
 * Binding is gated to ACTIVE agents in the UI: the approval queue is the
 * moment a human vouches for an agent, so a pending/rejected/disabled agent
 * must not accumulate capabilities beforehand. Suspend/resume/unbind stay available in every
 * status: removing capability is always safe.
 */
import { useMemo, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import {
	AlertTriangle,
	ChevronDown,
	Edit2,
	Key,
	KeyRound,
	PauseCircle,
	PlayCircle,
	ShieldCheck,
	Unlink,
} from 'lucide-react';
import { Badge, Button, DetailSection, EmptyRow, ErrorAlert, LoadingState } from '@/shared/ui';
import { OperationsSummary } from '@/shared/app';
import { apiIdentityTuple } from '@/shared/lib';
import { formatTimestamp, timeAgo } from '@/shared/lib/utils';
import {
	useAgentCredentialBindings,
	useAgentBindingPermissions,
	useResumeAgentCredentialBinding,
	useUnbindAgentCredential,
	type AgentEntity,
	type BindingPermissionRule,
	type CredentialBindingEntity,
} from '@/modules/agents/api';
import { AgentBindingPermissionsEditor } from '@/modules/agents/components/detail/AgentBindingPermissionsEditor';
import { CreateCredentialDialog } from '@/shared/credentials/components/CreateCredentialDialog';
import { PostConnectBindMore } from '@/shared/credentials/components/PostConnectBindMore';
import { OperationImpactPreview } from '@/shared/credentials/components/OperationImpactPreview';
import { useCredential } from '@/shared/credentials/api';
import type { PermissionRule as PreviewPermissionRule } from '@/shared/credentials/api/vendors-types';

/**
 * Adapt a stored ``BindingPermissionRule`` (agent-module wire type) into
 * the ``PermissionRule`` shape ``OperationImpactPreview`` consumes.
 * Structurally identical for our fields; explicit narrow makes a future
 * divergence in either type a build-time error.
 */
function bindingRuleToPreview(rule: BindingPermissionRule): PreviewPermissionRule {
	return {
		effect: rule.effect === 'deny' ? 'deny' : 'allow',
		methods: rule.methods ?? null,
		path: rule.path ?? null,
		match_mode:
			rule.match_mode === 'prefix' ||
			rule.match_mode === 'exact' ||
			rule.match_mode === 'regex'
				? rule.match_mode
				: undefined,
		operations: rule.operations ?? null,
	};
}
import { InlineConfirm } from '@/modules/agents/components/InlineConfirm';
import { panelMotion, rowMotion, toDisplayRules } from '@/modules/agents/components/detail/shared';

/**
 * One binding row. The binding list response carries no rules inline, so each
 * row reads its own rule list here
 * (`useAgentBindingPermissions`) — the hook is called at the top of THIS
 * component (not in the parent's map callback) to keep the hooks-per-row
 * contract valid.
 */
function BindingRow({
	agentId,
	binding,
	editing,
	onToggleEdit,
	suspendPending,
	resumePending,
	onSuspend,
	onResume,
	onPurge,
}: {
	agentId: string;
	binding: CredentialBindingEntity;
	editing: boolean;
	onToggleEdit: () => void;
	suspendPending: boolean;
	resumePending: boolean;
	onSuspend: () => void;
	onResume: () => void;
	onPurge: () => void;
}) {
	const permissions = useAgentBindingPermissions(agentId, binding.credentialId);
	const displayRules = toDisplayRules(permissions.data);
	// Resolve the vendor's OpenAPI version from the credential — the
	// binding's ``serves`` entry has ``(vendor, name)`` but rarely a
	// version, whereas the credential's ``api`` field always carries
	// a full ``(vendor, name, version)`` tuple. Feed either into the
	// ops preview; the preview shows a skeleton while this is loading.
	const credential = useCredential(binding.credentialId);
	const apiReference = useMemo(() => {
		const api = credential.data?.api;
		if (!api) return null;
		return { vendor: api.vendor, name: api.name, version: api.version };
	}, [credential.data]);

	// Heading = the credential's human name (control-DB enrichment) with the
	// id as the never-blank fallback; subtitle = the served API's machine
	// identity, else the id — unless the heading already IS the id, in which
	// case repeating it is pure noise.
	const heading = binding.name || binding.credentialId;
	const serves = binding.serves[0];
	const subtitle =
		apiIdentityTuple({
			catalogApiId: null,
			vendor: serves?.vendor ?? null,
			name: serves?.name ?? null,
		}) || (heading === binding.credentialId ? '' : binding.credentialId);

	return (
		<motion.div
			{...rowMotion}
			layout
			data-testid="binding-row"
			className={
				binding.suspended
					? 'border-warning/40 bg-muted/30 overflow-hidden rounded-lg border transition-colors'
					: 'bg-muted/30 border-border/60 hover:border-border overflow-hidden rounded-lg border transition-colors'
			}
		>
			<div className="flex flex-wrap items-center gap-3 px-4 py-3">
				<div className="min-w-0 flex-1 basis-40">
					<span className="flex flex-wrap items-center gap-2">
						<span
							className={
								binding.suspended
									? 'text-muted-foreground text-sm font-medium'
									: 'text-foreground text-sm font-medium'
							}
						>
							{heading}
						</span>
						{binding.suspended && (
							<Badge variant="warning" data-testid="binding-suspended">
								Suspended
							</Badge>
						)}
					</span>
					{subtitle && (
						<p className="text-muted-foreground truncate font-mono text-xs">
							{subtitle}
						</p>
					)}
				</div>
				<span
					className="text-muted-foreground/70 shrink-0 text-[11px]"
					title={formatTimestamp(binding.boundAt)}
				>
					bound {timeAgo(binding.boundAt)}
				</span>
				<div className="ml-auto flex w-full shrink-0 items-center justify-end gap-1.5 sm:w-auto">
					<Button
						variant="secondary"
						size="sm"
						onClick={onToggleEdit}
						aria-expanded={editing}
						className="inline-flex items-center gap-1 px-2 py-1 text-xs"
					>
						<Edit2 className="h-3 w-3" /> Edit rules
						<motion.span
							animate={{ rotate: editing ? 180 : 0 }}
							transition={{ duration: 0.18 }}
							className="flex"
						>
							<ChevronDown className="h-3 w-3" />
						</motion.span>
					</Button>
					{binding.suspended ? (
						<Button
							variant="secondary"
							size="sm"
							loading={resumePending}
							onClick={onResume}
							className="inline-flex items-center gap-1 px-2 py-1 text-xs"
							aria-label={`Resume binding for ${heading}`}
						>
							<PlayCircle className="h-3 w-3" /> Resume
						</Button>
					) : (
						<InlineConfirm
							onConfirm={onSuspend}
							message="Suspend this binding? Rules survive; resume restores access."
							confirmLabel="Suspend"
							disabled={suspendPending}
						>
							<Button
								variant="secondary"
								size="sm"
								className="inline-flex items-center gap-1 px-2 py-1 text-xs"
								aria-label={`Suspend binding for ${heading}`}
							>
								<PauseCircle className="h-3 w-3" /> Suspend
							</Button>
						</InlineConfirm>
					)}
					<InlineConfirm
						onConfirm={onPurge}
						message="Permanently unbind? The binding and its rules are deleted."
						confirmLabel="Unbind permanently"
						disabled={suspendPending}
					>
						<Button
							variant="danger"
							size="sm"
							className="inline-flex items-center gap-1 px-2 py-1 text-xs"
							aria-label={`Permanently unbind ${heading}`}
						>
							<Unlink className="h-3 w-3" /> Unbind
						</Button>
					</InlineConfirm>
				</div>
				{/* The grant, in the platform's one operations grammar (effect
				    chips + bounded preview + full-view dialog). Zero agent rules
				    ⇒ the broker default-denies; say so in a warning voice. */}
				<div className="w-full">
					{permissions.isPending ? (
						<p className="text-muted-foreground text-xs">Loading rules…</p>
					) : permissions.isError ? (
						<p className="text-danger text-xs">Failed to load this binding's rules.</p>
					) : displayRules.length > 0 ? (
						<OperationsSummary rules={displayRules} targetLabel={heading} />
					) : (
						<p
							className="text-warning flex items-center gap-1.5 text-xs"
							data-testid="binding-warning"
						>
							<AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
							No rules — all operations blocked (default deny). Add an allow rule to
							grant access.
						</p>
					)}
				</div>
				{/*
				 * Effective-access preview is always visible on the row (not
				 * gated on the Edit-rules toggle) so users can see the
				 * binding's real surface at a glance. Uses the resolved
				 * ``apiReference`` sourced from the credential's ``api``
				 * field — the binding's ``serves`` entry only has
				 * ``(vendor, name)``, but the credential always carries a
				 * concrete version. Renders a skeleton while the credential
				 * query resolves.
				 */}
				{apiReference && !permissions.isPending && (
					<div className="w-full pt-1">
						<OperationImpactPreview
							api={apiReference}
							rules={(permissions.data ?? [])
								.filter((r) => !r._system)
								.map(bindingRuleToPreview)}
							label="Effective access for this binding"
						/>
					</div>
				)}
			</div>
			<AnimatePresence initial={false}>
				{editing && !permissions.isPending && !permissions.isError && (
					<motion.div {...panelMotion} className="overflow-hidden">
						<AgentBindingPermissionsEditor
							agentId={agentId}
							credentialId={binding.credentialId}
							credentialLabel={heading}
							initialRules={permissions.data ?? []}
							onClose={onToggleEdit}
							apiReference={
								// Credential-driven version (see above) — the
								// binding's ``serves`` entry rarely carries a
								// version, whereas the credential's ``api``
								// field always does. Fall back to serves only
								// when the credential query hasn't resolved.
								apiReference ??
								(serves && serves.vendor && serves.name && serves.version
									? {
											vendor: serves.vendor,
											name: serves.name,
											version: serves.version,
										}
									: null)
							}
						/>
					</motion.div>
				)}
			</AnimatePresence>
		</motion.div>
	);
}

export function BoundCredentialsCard({
	agentId,
	agentStatus,
}: {
	agentId: string;
	agentStatus: AgentEntity['status'];
}) {
	const bindings = useAgentCredentialBindings(agentId);
	const unbind = useUnbindAgentCredential(agentId);
	const resume = useResumeAgentCredentialBinding(agentId);

	const [connectOpen, setConnectOpen] = useState(false);
	const [editingCredId, setEditingCredId] = useState<string | null>(null);

	// Approval gate: only a vouched-for (active) agent may gain capabilities.
	const canBind = agentStatus === 'active';

	const rows = useMemo(() => bindings.data ?? [], [bindings.data]);

	return (
		<>
			<DetailSection
				title={`Bound credentials (${rows.length})`}
				icon={<ShieldCheck className="h-4 w-4" />}
				action={
					// Single entry point: the connect wizard runs the vendor
					// OAuth flow with this agent pre-selected AND handles the
					// pick-existing-credential path via its API picker. Gated
					// on the agent being active (approval vouches for the
					// identity before capability accrues).
					canBind
						? {
								label: (
									<>
										<KeyRound className="h-4 w-4" /> Connect integration
									</>
								),
								onClick: () => setConnectOpen(true),
							}
						: undefined
				}
			>
				{bindings.isPending ? (
					<LoadingState size="sm" />
				) : bindings.isError ? (
					<ErrorAlert message="Failed to load bound credentials." />
				) : rows.length === 0 ? (
					<EmptyRow icon={<Key />}>
						{canBind ? (
							<>
								No credentials bound directly to this agent.{' '}
								<Button
									variant="ghost"
									size="sm"
									onClick={() => setConnectOpen(true)}
									className="text-primary h-auto px-1 py-0 text-xs font-medium"
								>
									<KeyRound className="h-3 w-3" /> Connect an integration
								</Button>{' '}
								to grant it API access.
							</>
						) : agentStatus === 'pending' ? (
							'No credentials bound. Approve this agent first — credentials can only be bound to active agents.'
						) : (
							'No credentials bound. Credentials can only be bound to active agents.'
						)}
					</EmptyRow>
				) : (
					<AnimatePresence initial={false}>
						{rows.map((binding) => (
							<BindingRow
								key={binding.credentialId}
								agentId={agentId}
								binding={binding}
								editing={editingCredId === binding.credentialId}
								onToggleEdit={() =>
									setEditingCredId(
										editingCredId === binding.credentialId
											? null
											: binding.credentialId,
									)
								}
								suspendPending={unbind.isPending}
								resumePending={resume.isPending}
								onSuspend={() =>
									unbind.mutate({ credentialId: binding.credentialId })
								}
								onResume={() => resume.mutate(binding.credentialId)}
								onPurge={() =>
									unbind.mutate({
										credentialId: binding.credentialId,
										purge: true,
									})
								}
							/>
						))}
					</AnimatePresence>
				)}
			</DetailSection>

			{/* Vendor-connect flow with agent locked to this page's agent —
			    dropdown greys out via ``preselectedAgentId``. On success the
			    credential is bound + rules-authored in one shot. Mounted
			    conditionally so its child effects (``:connect`` on mount)
			    don't fire until the user actually clicks the button. */}
			{connectOpen && (
				<CreateCredentialDialog
					open
					onClose={() => setConnectOpen(false)}
					onCreated={() => setConnectOpen(false)}
					preselectedAgentId={agentId}
					renderPostConnect={({ credentialId, boundAgentId }) => (
						<PostConnectBindMore
							credentialId={credentialId}
							boundAgentId={boundAgentId}
						/>
					)}
				/>
			)}
		</>
	);
}
