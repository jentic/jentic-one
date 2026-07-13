import { useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import {
	AlertTriangle,
	Ban,
	Bot,
	ChevronDown,
	Edit2,
	Key,
	Link as LinkIcon,
	Plus,
	Settings,
	ShieldOff,
	Trash2,
	Unlink,
} from 'lucide-react';
import {
	ActorStatusBadge,
	AppLink,
	Badge,
	Button,
	CascadeDeleteDialog,
	CopyButton,
	Dialog,
	ErrorAlert,
	Input,
	Label,
	Textarea,
} from '@/shared/ui';
import type { CascadeDependentGroup } from '@/shared/ui';
import {
	useBindCredential,
	useCreateKey,
	useDeleteToolkit,
	useLinkAgentToToolkit,
	useRevokeKey,
	useToolkit,
	useToolkitAgents,
	useToolkitBindings,
	useToolkitKeys,
	useUnbindCredential,
	useUnbindToolkitFromAgent,
	useUpdateToolkit,
} from '@/modules/toolkits/api';
import { ToolkitKillSwitch } from '@/modules/toolkits/components/ToolkitKillSwitch';
import { OneTimeKeyDisplay } from '@/modules/toolkits/components/OneTimeKeyDisplay';
import { InlineConfirm } from '@/modules/toolkits/components/InlineConfirm';
import { CredentialPermissionEditor } from '@/modules/toolkits/components/CredentialPermissionEditor';
import { CredentialPicker } from '@/modules/toolkits/components/CredentialPicker';
import { AgentPicker } from '@/modules/toolkits/components/AgentPicker';
import { ToolkitAuditPanel } from '@/modules/toolkits/components/ToolkitAuditPanel';
import { timeAgo } from '@/modules/toolkits/lib/time';
import { ROUTE_PATHS, ROUTES } from '@/shared/app/routes';

const rowMotion = {
	initial: { opacity: 0, y: -4, height: 0 },
	animate: { opacity: 1, y: 0, height: 'auto' as const },
	exit: { opacity: 0, y: -4, height: 0 },
	transition: { duration: 0.18, ease: 'easeOut' as const },
};

const panelMotion = {
	initial: { opacity: 0, height: 0 },
	animate: { opacity: 1, height: 'auto' as const },
	exit: { opacity: 0, height: 0 },
	transition: { duration: 0.2, ease: 'easeOut' as const },
};

function RowSkeleton() {
	return (
		<div className="bg-muted/30 border-border/60 flex items-center gap-3 rounded-lg border p-3">
			<div className="bg-muted h-8 w-8 shrink-0 animate-pulse rounded-lg" />
			<div className="min-w-0 flex-1 space-y-1.5">
				<div className="bg-muted h-3.5 w-1/3 animate-pulse rounded" />
				<div className="bg-muted h-3 w-1/2 animate-pulse rounded" />
			</div>
		</div>
	);
}

export interface ToolkitDetailBodyProps {
	toolkitId: string;
	/** `page` keeps the identity heading; `sheet` defers identity to the host. */
	layout?: 'page' | 'sheet';
	/** Host close callback (not-found "Back"). Page → navigate; sheet → drop param. */
	onRequestClose: () => void;
}

export function ToolkitDetailBody({
	toolkitId,
	layout = 'page',
	onRequestClose,
}: ToolkitDetailBodyProps) {
	const isSheet = layout === 'sheet';
	const poll = !isSheet;

	const { data: toolkit, isLoading } = useToolkit(toolkitId, { poll });
	const { data: keys = [], isError: keysError } = useToolkitKeys(toolkitId, { poll });
	const { data: bindings = [], isError: bindingsError } = useToolkitBindings(toolkitId, { poll });
	const { data: agents = [], isError: agentsError } = useToolkitAgents(toolkitId, { poll });

	const updateToolkit = useUpdateToolkit(toolkitId);
	const createKey = useCreateKey(toolkitId);
	const revokeKey = useRevokeKey(toolkitId);
	const bindCredential = useBindCredential(toolkitId);
	const unbindCredential = useUnbindCredential(toolkitId);
	const linkAgent = useLinkAgentToToolkit(toolkitId);
	const unlinkAgent = useUnbindToolkitFromAgent(toolkitId);
	const deleteToolkit = useDeleteToolkit();

	const [showKeyCreate, setShowKeyCreate] = useState(false);
	const [keyName, setKeyName] = useState('');
	const [newKey, setNewKey] = useState<string | null>(null);
	const [showSettings, setShowSettings] = useState(false);
	const [editName, setEditName] = useState('');
	const [editDesc, setEditDesc] = useState('');
	const [editingPermForCred, setEditingPermForCred] = useState<string | null>(null);
	const [bindOpen, setBindOpen] = useState(false);
	const [linkAgentOpen, setLinkAgentOpen] = useState(false);
	const [deleteOpen, setDeleteOpen] = useState(false);

	const prevShowSettings = useRef(false);
	useEffect(() => {
		if (showSettings && !prevShowSettings.current && toolkit) {
			setEditName(toolkit.name);
			setEditDesc(toolkit.description ?? '');
		}
		prevShowSettings.current = showSettings;
	}, [toolkit, showSettings]);

	if (isLoading)
		return (
			<div className="space-y-6" data-testid="toolkit-loading">
				<div className="flex items-center justify-between gap-3">
					<div className="bg-muted h-6 w-40 animate-pulse rounded-md" />
					<div className="bg-muted h-8 w-28 animate-pulse rounded" />
				</div>
				<div className="border-border bg-card space-y-3 rounded-xl border p-5">
					<div className="bg-muted h-5 w-32 animate-pulse rounded" />
					<RowSkeleton />
					<RowSkeleton />
				</div>
			</div>
		);

	if (!toolkit)
		return (
			<div className="flex flex-col items-center justify-center gap-3 px-5 py-12 text-center">
				<span className="text-2xl">🔍</span>
				<p className="text-foreground font-medium">Toolkit not found</p>
				<Button variant="secondary" onClick={onRequestClose}>
					Back
				</Button>
			</div>
		);

	const suspended = !toolkit.active;
	const boundIds = new Set(bindings.map((b) => b.credential_id));
	const linkedAgentIds = new Set(agents.map((a) => a.agent_id));

	// Cascade blast radius for the delete dialog — composed from the data
	// already loaded for this page (no extra fetches). Zero-count groups are
	// filtered so the dialog falls back to its generic warning when this
	// toolkit has no dependents. Cheap to rebuild; not memoised because the
	// early returns above forbid a hook here.
	const deleteDependents: CascadeDependentGroup[] = [
		{
			label: 'agent grant',
			count: agents.length,
			names: agents.map((a) => a.agent_name),
		},
		{
			label: 'API key',
			count: keys.length,
			names: keys.map((k) => k.label ?? k.key_preview),
		},
		{
			label: 'credential binding',
			count: bindings.length,
			names: bindings.map((b) => b.label ?? b.credential_id),
		},
	].filter((g) => g.count > 0);

	const submitKey = () => {
		createKey.mutate(
			{ label: keyName || null },
			{
				onSuccess: (res) => {
					setNewKey(res.api_key);
					setShowKeyCreate(false);
					setKeyName('');
				},
			},
		);
	};

	const submitBind = (credentialId: string) => {
		if (!credentialId) return;
		bindCredential.mutate(
			{ credential_id: credentialId },
			{
				onSuccess: () => {
					setBindOpen(false);
				},
			},
		);
	};

	const submitLinkAgent = (agentId: string) => {
		if (!agentId) return;
		linkAgent.mutate(agentId, {
			onSuccess: () => {
				setLinkAgentOpen(false);
			},
		});
	};

	return (
		<div className={isSheet ? 'space-y-5' : 'space-y-6'}>
			{!isSheet && (
				<div className="flex flex-wrap items-center justify-between gap-3">
					<span className="bg-muted text-muted-foreground inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 font-mono text-xs">
						{toolkit.toolkit_id}
						<CopyButton value={toolkit.toolkit_id} size="icon" variant="ghost" />
					</span>
					<div className="flex shrink-0 items-center gap-2">
						<ToolkitKillSwitch toolkitId={toolkitId} active={toolkit.active} />
						<Button variant="secondary" size="sm" onClick={() => setShowSettings(true)}>
							<Settings className="h-4 w-4" /> Edit
						</Button>
						<Button
							variant="danger"
							size="sm"
							onClick={() => setDeleteOpen(true)}
							aria-label={`Delete ${toolkit.name}`}
						>
							<Trash2 className="h-4 w-4" /> Delete
						</Button>
					</div>
				</div>
			)}

			{isSheet && (
				<div className="space-y-3">
					{toolkit.description && (
						<p className="text-muted-foreground max-w-prose text-sm">
							{toolkit.description}
						</p>
					)}
					<div className="flex flex-wrap items-center gap-2">
						<ToolkitKillSwitch toolkitId={toolkitId} active={toolkit.active} />
						<Button variant="secondary" size="sm" onClick={() => setShowSettings(true)}>
							<Settings className="h-4 w-4" /> Edit
						</Button>
						<Button
							variant="danger"
							size="sm"
							onClick={() => setDeleteOpen(true)}
							aria-label={`Delete ${toolkit.name}`}
						>
							<Trash2 className="h-4 w-4" /> Delete
						</Button>
					</div>
				</div>
			)}

			<AnimatePresence initial={false}>
				{suspended && (
					<motion.div key="suspended-banner" {...panelMotion} className="overflow-hidden">
						<div
							className="border-danger/40 bg-danger/5 flex items-start gap-3 rounded-xl border p-4"
							role="alert"
						>
							<div className="bg-danger/15 text-danger flex h-9 w-9 shrink-0 items-center justify-center rounded-lg">
								<ShieldOff className="h-5 w-5" />
							</div>
							<div className="min-w-0 flex-1">
								<p className="text-danger font-heading text-sm font-semibold">
									Toolkit suspended — all access blocked
								</p>
								<p className="text-muted-foreground mt-0.5 text-sm">
									Every call is rejected with{' '}
									<code className="bg-danger/10 text-danger rounded px-1 font-mono text-xs">
										403 toolkit_suspended
									</code>{' '}
									— this applies to both toolkit API keys and agent-identity
									callers. Restore access with the kill switch above.
								</p>
							</div>
						</div>
					</motion.div>
				)}
			</AnimatePresence>

			{/* API Keys */}
			<div
				className={`overflow-hidden rounded-xl border ${suspended ? 'border-danger/50' : 'border-border'} bg-card`}
			>
				<div
					className={`flex flex-wrap items-center justify-between gap-3 px-4 py-3.5 sm:px-5 sm:py-4 ${suspended ? 'border-danger/30 bg-danger/5 border-b' : 'border-border'}`}
				>
					<div className="flex items-center gap-2">
						<h3 className="font-heading text-foreground font-semibold">
							API Keys ({keys.length})
						</h3>
						{suspended && (
							<span className="bg-danger/15 text-danger border-danger/30 inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-xs">
								<Ban className="h-3 w-3" />
								Keys blocked
							</span>
						)}
					</div>
					{!suspended && (
						<Button size="sm" onClick={() => setShowKeyCreate(true)}>
							<Plus className="h-4 w-4" /> Create Key
						</Button>
					)}
				</div>
				<div className="space-y-3 px-4 py-3.5 sm:px-5 sm:py-4">
					<AnimatePresence initial={false}>
						{newKey && (
							<motion.div
								key="one-time-key"
								{...panelMotion}
								className="overflow-hidden"
							>
								<OneTimeKeyDisplay
									keyValue={newKey}
									onConfirm={() => setNewKey(null)}
								/>
							</motion.div>
						)}
						{showKeyCreate && (
							<motion.div
								key="create-key-form"
								{...panelMotion}
								className="overflow-hidden"
							>
								<div className="bg-muted/30 border-border/60 space-y-3 rounded-lg border p-4">
									<p className="text-foreground text-sm font-semibold">
										Create API Key
									</p>
									<Input
										type="text"
										value={keyName}
										onChange={(e) => setKeyName(e.target.value)}
										placeholder="Key label (optional)"
										aria-label="Key label"
										autoFocus
										onKeyDown={(e) => {
											if (e.key === 'Enter' && !createKey.isPending)
												submitKey();
										}}
									/>
									<div className="flex gap-2">
										<Button
											size="sm"
											onClick={submitKey}
											loading={createKey.isPending}
										>
											{createKey.isPending ? 'Generating...' : 'Generate'}
										</Button>
										<Button
											variant="secondary"
											size="sm"
											onClick={() => {
												setShowKeyCreate(false);
												setKeyName('');
											}}
										>
											Cancel
										</Button>
									</div>
								</div>
							</motion.div>
						)}
					</AnimatePresence>
					{keysError && <ErrorAlert message="Failed to load API keys." />}
					{keys.length === 0 && !showKeyCreate && !newKey && !keysError && (
						<div className="border-border/50 rounded-lg border border-dashed px-5 py-6 text-center">
							<Key className="text-muted-foreground/50 mx-auto h-6 w-6" />
							<p className="text-muted-foreground mt-2 text-sm">
								No keys yet. Create one to let agents call this toolkit with a
								static key.
							</p>
						</div>
					)}
					<AnimatePresence initial={false}>
						{keys.map((key) => (
							<motion.div
								key={key.key_id}
								{...rowMotion}
								layout
								className="bg-muted/30 border-border/60 hover:border-border flex flex-wrap items-center gap-3 overflow-hidden rounded-lg border p-3 transition-colors"
							>
								<div className="bg-accent-yellow/10 text-accent-yellow flex h-8 w-8 shrink-0 items-center justify-center rounded-lg">
									<Key className="h-4 w-4" />
								</div>
								<div className="min-w-0 flex-1 basis-40">
									<div className="flex items-center gap-2">
										<span className="text-foreground truncate text-sm font-medium">
											{key.label || 'Unnamed Key'}
										</span>
										<code className="text-muted-foreground font-mono text-xs">
											{key.key_preview}
										</code>
										{key.revoked && <Badge variant="danger">revoked</Badge>}
									</div>
									<p className="text-muted-foreground truncate text-xs">
										Created {new Date(key.created_at).toLocaleString()}
										{key.last_used_at
											? ` · last used ${timeAgo(Date.parse(key.last_used_at))}`
											: ' · never used'}
									</p>
								</div>
								{!key.revoked && (
									<div className="ml-auto w-full sm:w-auto">
										<InlineConfirm
											onConfirm={() => revokeKey.mutate(key.key_id)}
											message="Revoke this key?"
											confirmLabel="Revoke"
										>
											<Button
												variant="danger"
												size="sm"
												className="px-2 py-1 text-xs"
											>
												Revoke
											</Button>
										</InlineConfirm>
									</div>
								)}
							</motion.div>
						))}
					</AnimatePresence>
				</div>
			</div>

			{/* Bound Credentials */}
			<div className="bg-card border-border overflow-hidden rounded-xl border">
				<div className="border-border flex flex-wrap items-center justify-between gap-2 border-b px-4 py-3.5 sm:px-5 sm:py-4">
					<h3 className="font-heading text-foreground font-semibold">
						Bound Credentials ({bindings.length})
					</h3>
					<div className="flex items-center gap-2">
						<Button variant="secondary" size="sm" onClick={() => setBindOpen(true)}>
							<LinkIcon className="h-4 w-4" /> Bind existing
						</Button>
						<AppLink
							href={ROUTES.credentials}
							className="border-border bg-card text-foreground hover:bg-muted inline-flex h-8 items-center gap-1.5 rounded-md border px-3 text-sm font-medium transition-colors"
						>
							Manage
						</AppLink>
					</div>
				</div>
				<div className="space-y-2 px-4 py-3.5 sm:px-5 sm:py-4">
					{bindingsError && <ErrorAlert message="Failed to load bound credentials." />}
					{!bindingsError && bindings.length === 0 ? (
						<div className="border-border/50 rounded-lg border border-dashed px-5 py-6 text-center">
							<Key className="text-muted-foreground/50 mx-auto h-6 w-6" />
							<p className="text-muted-foreground mt-2 text-sm">
								No credentials bound. Bind credentials to grant this toolkit API
								access.
							</p>
						</div>
					) : (
						<AnimatePresence initial={false}>
							{bindings.map((cred) => {
								const agentRules = (cred.permissions ?? []).filter(
									(r) => !r._system,
								);
								return (
									<motion.div
										key={cred.credential_id}
										{...rowMotion}
										layout
										className="bg-muted/30 border-border/60 hover:border-border overflow-hidden rounded-lg border transition-colors"
									>
										<div className="flex flex-wrap items-center gap-3 px-4 py-3">
											<div className="min-w-0 flex-1 basis-40">
												<span className="text-foreground text-sm font-medium">
													{cred.label ?? cred.credential_id}
												</span>
												{(cred.api_name || cred.api_vendor) && (
													<p className="text-muted-foreground truncate font-mono text-xs">
														{cred.api_name ?? cred.api_vendor}
													</p>
												)}
												<p className="text-muted-foreground mt-0.5 flex items-center gap-1 text-xs">
													{agentRules.length === 0 ? (
														<span
															className="text-warning inline-flex items-center gap-1"
															title="All operations blocked — no allow rules defined"
														>
															<AlertTriangle className="h-3 w-3" />
															No rules — all ops blocked
														</span>
													) : (
														<>
															{agentRules.length} agent rule(s) +
															system safety
														</>
													)}
												</p>
											</div>
											<div className="ml-auto flex w-full shrink-0 items-center justify-end gap-1.5 sm:w-auto">
												<Button
													variant="secondary"
													size="sm"
													onClick={() =>
														setEditingPermForCred(
															editingPermForCred ===
																cred.credential_id
																? null
																: cred.credential_id,
														)
													}
													aria-expanded={
														editingPermForCred === cred.credential_id
													}
													className="inline-flex items-center gap-1 px-2 py-1 text-xs"
												>
													<Edit2 className="h-3 w-3" /> Permissions
													<motion.span
														animate={{
															rotate:
																editingPermForCred ===
																cred.credential_id
																	? 180
																	: 0,
														}}
														transition={{ duration: 0.18 }}
														className="flex"
													>
														<ChevronDown className="h-3 w-3" />
													</motion.span>
												</Button>
												<InlineConfirm
													onConfirm={() =>
														unbindCredential.mutate(cred.credential_id)
													}
													message="Unbind this credential?"
													confirmLabel="Unbind"
												>
													<Button
														variant="danger"
														size="sm"
														className="inline-flex items-center gap-1 px-2 py-1 text-xs"
													>
														<Unlink className="h-3 w-3" /> Unbind
													</Button>
												</InlineConfirm>
											</div>
										</div>
										<AnimatePresence initial={false}>
											{editingPermForCred === cred.credential_id && (
												<motion.div
													{...panelMotion}
													className="overflow-hidden"
												>
													<CredentialPermissionEditor
														toolkitId={toolkitId}
														credentialId={cred.credential_id}
														credentialLabel={
															cred.label ?? cred.credential_id
														}
														initialRules={cred.permissions ?? []}
														onClose={() => setEditingPermForCred(null)}
													/>
												</motion.div>
											)}
										</AnimatePresence>
									</motion.div>
								);
							})}
						</AnimatePresence>
					)}
				</div>
			</div>

			{/* Bound Agents — reverse lookup via GET /toolkits/{id}/agents. Link /
			    unlink reuse the agent-side bind endpoints (AgentsService). */}
			<div className="bg-card border-border overflow-hidden rounded-xl border">
				<div className="border-border flex flex-wrap items-center justify-between gap-2 border-b px-4 py-3.5 sm:px-5 sm:py-4">
					<h3 className="font-heading text-foreground font-semibold">
						Bound Agents ({agents.length})
					</h3>
					<div className="flex items-center gap-2">
						<Button
							variant="secondary"
							size="sm"
							onClick={() => setLinkAgentOpen(true)}
						>
							<LinkIcon className="h-4 w-4" /> Link agent
						</Button>
						<AppLink
							href={ROUTES.agents}
							className="border-border bg-card text-foreground hover:bg-muted inline-flex h-8 items-center gap-1.5 rounded-md border px-3 text-sm font-medium transition-colors"
						>
							<Bot className="h-4 w-4" /> Manage
						</AppLink>
					</div>
				</div>
				<div className="space-y-2 px-4 py-3.5 sm:px-5 sm:py-4">
					{agentsError && <ErrorAlert message="Failed to load bound agents." />}
					{!agentsError && agents.length === 0 ? (
						<div className="border-border/50 rounded-lg border border-dashed px-5 py-6 text-center">
							<Bot className="text-muted-foreground/50 mx-auto h-6 w-6" />
							<p className="text-muted-foreground mt-2 text-sm">
								No agents linked. Link an agent to let its identity call this
								toolkit.
							</p>
						</div>
					) : (
						<AnimatePresence initial={false}>
							{agents.map((agent) => (
								<motion.div
									key={agent.agent_id}
									{...rowMotion}
									layout
									data-testid="bound-agent-row"
									className="bg-muted/30 border-border/60 hover:border-border flex flex-wrap items-center gap-3 overflow-hidden rounded-lg border p-3 transition-colors"
								>
									<div className="bg-primary/10 text-primary flex h-8 w-8 shrink-0 items-center justify-center rounded-lg">
										<Bot className="h-4 w-4" />
									</div>
									<div className="min-w-0 flex-1 basis-40">
										<div className="flex items-center gap-2">
											<AppLink
												href={ROUTE_PATHS.agent(agent.agent_id)}
												className="text-foreground hover:text-primary truncate text-sm font-medium transition-colors"
											>
												{agent.agent_name}
											</AppLink>
											<ActorStatusBadge
												status={agent.status}
												className="text-[10px]"
											/>
										</div>
										<p className="text-muted-foreground truncate font-mono text-xs">
											{agent.agent_id}
											{agent.bound_at
												? ` · linked ${timeAgo(Date.parse(agent.bound_at))}`
												: ''}
										</p>
									</div>
									<div className="ml-auto w-full sm:w-auto">
										<InlineConfirm
											onConfirm={() => unlinkAgent.mutate(agent.agent_id)}
											message="Revoke this toolkit for the agent?"
											confirmLabel="Unlink"
										>
											<Button
												variant="danger"
												size="sm"
												className="inline-flex items-center gap-1 px-2 py-1 text-xs"
											>
												<Unlink className="h-3 w-3" /> Unlink
											</Button>
										</InlineConfirm>
									</div>
								</motion.div>
							))}
						</AnimatePresence>
					)}
				</div>
			</div>

			{/* Activity — read-only toolkit-scoped slice of the org-wide audit log. */}
			<ToolkitAuditPanel toolkitId={toolkitId} poll={poll} />

			{/* Settings dialog */}
			<Dialog
				open={showSettings}
				onClose={() => setShowSettings(false)}
				title="Edit Toolkit"
				size="sm"
				footer={
					<>
						<Button variant="secondary" onClick={() => setShowSettings(false)}>
							Cancel
						</Button>
						<Button
							onClick={() =>
								updateToolkit.mutate(
									{ name: editName || null, description: editDesc || null },
									{ onSuccess: () => setShowSettings(false) },
								)
							}
							loading={updateToolkit.isPending}
						>
							{updateToolkit.isPending ? 'Saving...' : 'Save Changes'}
						</Button>
					</>
				}
			>
				<div className="space-y-4">
					<div>
						<Label
							htmlFor="tk-settings-name"
							className="text-muted-foreground mb-1 block text-xs"
						>
							Name
						</Label>
						<Input
							id="tk-settings-name"
							type="text"
							value={editName}
							onChange={(e) => setEditName(e.target.value)}
						/>
					</div>
					<div>
						<Label
							htmlFor="tk-settings-description"
							className="text-muted-foreground mb-1 block text-xs"
						>
							Description
						</Label>
						<Textarea
							id="tk-settings-description"
							value={editDesc}
							onChange={(e) => setEditDesc(e.target.value)}
							rows={2}
						/>
					</div>
				</div>
			</Dialog>

			{/* Bind-existing dialog */}
			<Dialog
				open={bindOpen}
				onClose={() => setBindOpen(false)}
				title="Bind credential"
				size="sm"
				footer={
					<Button variant="secondary" onClick={() => setBindOpen(false)}>
						Cancel
					</Button>
				}
			>
				<div className="space-y-3">
					<p className="text-muted-foreground text-sm">
						Pick a credential to bind to this toolkit. Manage credentials on the{' '}
						<AppLink href={ROUTES.credentials} className="text-primary font-medium">
							Credentials
						</AppLink>{' '}
						page.
					</p>
					<CredentialPicker
						boundIds={boundIds}
						onSelect={submitBind}
						pending={bindCredential.isPending}
						enabled={bindOpen}
					/>
				</div>
			</Dialog>

			{/* Link-agent dialog */}
			<Dialog
				open={linkAgentOpen}
				onClose={() => setLinkAgentOpen(false)}
				title="Link agent"
				size="sm"
				footer={
					<Button variant="secondary" onClick={() => setLinkAgentOpen(false)}>
						Cancel
					</Button>
				}
			>
				<div className="space-y-3">
					<p className="text-muted-foreground text-sm">
						Pick an agent to grant this toolkit. The agent's identity will be able to
						call the toolkit's bound APIs. Manage agents on the{' '}
						<AppLink href={ROUTES.agents} className="text-primary font-medium">
							Agents
						</AppLink>{' '}
						page.
					</p>
					<AgentPicker
						linkedIds={linkedAgentIds}
						onSelect={submitLinkAgent}
						pending={linkAgent.isPending}
						enabled={linkAgentOpen}
					/>
				</div>
			</Dialog>

			{/* Cascade-delete dialog */}
			<CascadeDeleteDialog
				open={deleteOpen}
				entityType="toolkit"
				entityName={toolkit.name}
				dependents={deleteDependents.length > 0 ? deleteDependents : undefined}
				loading={deleteToolkit.isPending}
				error={deleteToolkit.error}
				onClose={() => setDeleteOpen(false)}
				onConfirm={() =>
					deleteToolkit.mutate(toolkitId, {
						onSuccess: () => {
							setDeleteOpen(false);
							onRequestClose();
						},
					})
				}
			/>
		</div>
	);
}
