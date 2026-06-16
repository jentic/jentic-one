import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { AnimatePresence, motion } from 'framer-motion';
import {
	Key,
	Plus,
	Trash2,
	Settings,
	AlertTriangle,
	Link as LinkIcon,
	Unlink,
	Edit2,
	ChevronDown,
	Save,
	Ban,
	Bot,
	ShieldOff,
} from 'lucide-react';
import { ToolkitKillSwitch } from './ToolkitKillSwitch';
import { api } from '@/api/client';
import type { KeyCreate } from '@/api/types';
import { Button } from '@/components/ui/Button';
import { AppLink } from '@/components/ui/AppLink';
import { Dialog } from '@/components/ui/Dialog';
import { Input } from '@/components/ui/Input';
import { Textarea } from '@/components/ui/Textarea';
import { Label } from '@/components/ui/Label';
import { ErrorAlert } from '@/components/ui/ErrorAlert';
import { OneTimeKeyDisplay } from '@/components/ui/OneTimeKeyDisplay';
import { ConfirmInline } from '@/components/ui/ConfirmInline';
import { ConfirmDeleteDialog } from '@/components/ui/ConfirmDeleteDialog';
import { toast } from '@/components/ui/toastStore';
import { Badge } from '@/components/ui/Badge';
import { CopyButton } from '@/components/ui/CopyButton';
import { PermissionRuleEditor } from '@/components/ui/PermissionRuleEditor';
import { CredentialEditSheet } from '@/components/credentials/CredentialEditSheet';
import { AddCredentialDialog } from '@/components/credentials/AddCredentialDialog';
import { BindExistingCredentialDialog } from '@/components/credentials/BindExistingCredentialDialog';
import { VendorIcon } from '@/components/discovery/VendorIcon';
import { useCredentialEditSheet } from '@/hooks/useCredentialEditSheet';
import { useAddCredentialDialog } from '@/hooks/useAddCredentialDialog';

/**
 * Shared toolkit detail UI, hosted in two shells:
 *   - `ToolkitDetailPage` (route `/toolkits/:id`, `layout="page"`)
 *   - `ToolkitDetailSheet` (slide-over, `layout="sheet"`)
 *
 * Owns all the toolkit queries + mutations. The host supplies the
 * `toolkitId` and an `onRequestClose` callback (page → navigate to
 * `/toolkits`; sheet → drop the `?toolkit=` param). `layout` only changes
 * chrome — never the data flow — so the page test suite (which renders the
 * page directly) keeps passing.
 */

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

/** A single shimmer row that matches the populated row footprint. */
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

function CredentialPermissionEditor({
	toolkitId,
	credential,
	onClose,
}: {
	toolkitId: string;
	credential: any;
	onClose: () => void;
}) {
	const queryClient = useQueryClient();
	const [rules, setRules] = useState<any[]>([]);

	const {
		data: permissions,
		isLoading,
		isError,
	} = useQuery({
		queryKey: ['permissions', toolkitId, credential.credential_id],
		queryFn: () => api.getPermissions(toolkitId, credential.credential_id),
	});

	React.useEffect(() => {
		if (permissions) {
			// System safety rules are tagged `_system: true` by the server and
			// re-appended on every save — they must NOT be loaded into the
			// editor, or saving would persist them as agent rules and the
			// backend would append a fresh copy, duplicating them each save.
			// (Filtering by `_comment` text is fragile; the flag is canonical.)
			const agentRules = Array.isArray(permissions)
				? permissions.filter((r: any) => !r._system)
				: [];
			setRules(agentRules);
		}
	}, [permissions]);

	const saveMutation = useMutation({
		// The backend rejects unknown fields (`extra: forbid`), so strip any
		// read-only/system keys (`_system`, `_comment`) and empty values that
		// the editor or a prior `GET` may have attached. Only the four schema
		// fields survive.
		mutationFn: () => {
			const clean = rules
				// Defensive: never persist a system rule as an agent rule, even
				// if one leaked into the editor state.
				.filter((r: any) => !r._system)
				.map((r: any) => {
					const out: Record<string, unknown> = { effect: r.effect };
					if (Array.isArray(r.methods) && r.methods.length > 0) out.methods = r.methods;
					if (typeof r.path === 'string' && r.path.trim() !== '')
						out.path = r.path.trim();
					if (Array.isArray(r.operations) && r.operations.length > 0)
						out.operations = r.operations;
					return out;
				});
			return api.setPermissions(toolkitId, credential.credential_id, clean);
		},
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: ['toolkit', toolkitId] });
			queryClient.invalidateQueries({
				queryKey: ['permissions', toolkitId, credential.credential_id],
			});
			toast({
				title: 'Permission rules saved',
				description: `Updated rules for ${credential.label ?? 'credential'}.`,
				variant: 'success',
			});
			onClose();
		},
		onError: (err: any) => {
			toast({
				title: 'Failed to save rules',
				description:
					err?.body?.error ?? err?.message ?? 'The server rejected the rule changes.',
				variant: 'error',
			});
		},
	});

	if (isLoading)
		return (
			<div className="border-border bg-muted/20 space-y-2 border-t p-5">
				<div className="bg-muted h-4 w-40 animate-pulse rounded" />
				<div className="bg-muted h-9 w-full animate-pulse rounded-lg" />
			</div>
		);

	if (isError)
		return (
			<div className="border-border bg-muted/20 border-t p-5">
				<p className="text-danger text-sm">Failed to load permissions.</p>
			</div>
		);

	return (
		<div className="border-border bg-muted/20 space-y-4 border-t p-4 sm:p-5">
			<div className="flex items-start justify-between gap-2">
				<div>
					<p className="text-foreground text-sm font-semibold">
						Permission Rules for {credential.label}
					</p>
					<p className="text-muted-foreground mt-0.5 text-xs">
						Define which operations this credential can access. System safety rules are
						always appended.
					</p>
				</div>
				<Button variant="ghost" size="icon" onClick={onClose} className="shrink-0">
					<span className="sr-only">Close</span>×
				</Button>
			</div>

			<PermissionRuleEditor rules={rules} onChange={setRules} />

			<div className="flex gap-2 pt-2">
				<Button onClick={() => saveMutation.mutate()} loading={saveMutation.isPending}>
					<Save className="h-4 w-4" />{' '}
					{saveMutation.isPending ? 'Saving...' : 'Save Rules'}
				</Button>
				<Button variant="secondary" onClick={onClose}>
					Cancel
				</Button>
			</div>

			<div className="border-border/50 border-t pt-3">
				<p className="text-muted-foreground text-xs leading-relaxed">
					<strong>Rules syntax:</strong> Each rule has{' '}
					<code className="bg-muted rounded px-1 font-mono">effect</code> (allow/deny),
					optional <code className="bg-muted rounded px-1 font-mono">methods</code> (GET,
					POST, etc.), and optional{' '}
					<code className="bg-muted rounded px-1 font-mono">path</code> regex. Rules are
					evaluated in order. First match wins.
				</p>
			</div>
		</div>
	);
}

export interface ToolkitDetailBodyProps {
	/** The toolkit to render. Guaranteed non-null by both hosts. */
	toolkitId: string;
	/**
	 * Chrome variant. `page` keeps the heading block + nested credential
	 * edit sheet; `sheet` defers identity to the sheet header and avoids
	 * stacking a second slide-over.
	 */
	layout?: 'page' | 'sheet';
	/**
	 * Called when the body wants its host to close/leave (delete success,
	 * not-found "Back"). Page → navigate to `/toolkits`; sheet → drop param.
	 */
	onRequestClose: () => void;
}

export function ToolkitDetailBody({
	toolkitId,
	layout = 'page',
	onRequestClose,
}: ToolkitDetailBodyProps) {
	const isSheet = layout === 'sheet';
	const queryClient = useQueryClient();
	// Bind/unbind/permission changes ripple across surfaces that host this view
	// (the toolkit list, the Workspace tiles, the API-detail page, and the card
	// enrichment counts). Invalidate them all so counts/lists never go stale.
	const invalidateToolkitSurfaces = React.useCallback(() => {
		queryClient.invalidateQueries({ queryKey: ['toolkit', toolkitId] });
		queryClient.invalidateQueries({ queryKey: ['toolkits'] });
		queryClient.invalidateQueries({ queryKey: ['toolkit-api-bindings'] });
		queryClient.invalidateQueries({ queryKey: ['toolkit-card-enrichment'] });
		queryClient.invalidateQueries({ queryKey: ['credentials'] });
		queryClient.invalidateQueries({ queryKey: ['workspace'] });
	}, [queryClient, toolkitId]);
	const [showKeyCreate, setShowKeyCreate] = useState(false);
	const [keyName, setKeyName] = useState('');
	const [newKey, setNewKey] = useState<string | null>(null);
	const [showSettings, setShowSettings] = useState(false);
	const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
	const [editName, setEditName] = useState('');
	const [editDesc, setEditDesc] = useState('');
	const [editingPermForCred, setEditingPermForCred] = useState<string | null>(null);
	const editSheet = useCredentialEditSheet();
	const addDialog = useAddCredentialDialog();
	const [bindOpen, setBindOpen] = useState(false);

	// Relax polling when hosted in a sheet — the list page underneath is
	// already polling `['toolkits']`, and a slide-over is a transient,
	// focused surface where 30s background churn isn't worth the requests.
	const refetchInterval = isSheet ? false : 30000;

	const { data: toolkit, isLoading } = useQuery({
		queryKey: ['toolkit', toolkitId],
		queryFn: () => api.getToolkit(toolkitId),
		enabled: !!toolkitId,
		refetchInterval,
	});

	const { data: keys = [], isError: keysError } = useQuery({
		queryKey: ['toolkit-keys', toolkitId],
		queryFn: () => api.listKeys(toolkitId),
		select: (d: any) => (Array.isArray(d) ? d : Array.isArray(d?.keys) ? d.keys : []),
		enabled: !!toolkitId,
		refetchInterval,
	});

	const { data: pending = [], isError: pendingError } = useQuery({
		queryKey: ['access-requests', toolkitId],
		queryFn: () => api.listAccessRequests(toolkitId, 'pending'),
		select: (d: any) => (Array.isArray(d) ? d.filter((r: any) => r.status === 'pending') : []),
		enabled: !!toolkitId,
		refetchInterval,
	});

	const {
		data: agents = [],
		isError: agentsError,
		isLoading: agentsLoading,
	} = useQuery({
		queryKey: ['toolkit-agents', toolkitId],
		queryFn: () => api.listToolkitAgents(toolkitId),
		select: (d) => (Array.isArray(d?.agents) ? d.agents : []),
		enabled: !!toolkitId,
		refetchInterval,
	});

	const revokeAgentMutation = useMutation({
		mutationFn: (agentClientId: string) => api.revokeAgentGrant(agentClientId, toolkitId),
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: ['toolkit-agents', toolkitId] });
			queryClient.invalidateQueries({ queryKey: ['agents'] });
			// agentCount on the toolkit cards comes from the enrichment query,
			// which invalidateToolkitSurfaces refreshes alongside the lists.
			invalidateToolkitSurfaces();
			toast({ title: 'Agent access revoked', variant: 'success' });
		},
		onError: (err: any) =>
			toast({
				title: 'Failed to revoke agent',
				description: err?.body?.error ?? err?.message,
				variant: 'error',
			}),
	});

	const createKeyMutation = useMutation({
		mutationFn: (d: KeyCreate) => api.createKey(toolkitId, d),
		onSuccess: (data) => {
			setNewKey(data.key);
			setShowKeyCreate(false);
			setKeyName('');
			queryClient.invalidateQueries({ queryKey: ['toolkit-keys', toolkitId] });
			// key_count is rendered from the toolkit lists/cards.
			invalidateToolkitSurfaces();
		},
	});

	const revokeKeyMutation = useMutation({
		mutationFn: (keyId: string) => api.revokeKey(toolkitId, keyId),
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: ['toolkit-keys', toolkitId] });
			invalidateToolkitSurfaces();
			toast({ title: 'API key revoked', variant: 'success' });
		},
		onError: (err: any) =>
			toast({
				title: 'Failed to revoke key',
				description: err?.body?.error ?? err?.message,
				variant: 'error',
			}),
	});

	const unbindMutation = useMutation({
		mutationFn: (credentialId: string) => api.unbindCredential(toolkitId, credentialId),
		onSuccess: () => {
			invalidateToolkitSurfaces();
			toast({ title: 'Credential unbound', variant: 'success' });
		},
		onError: (err: any) =>
			toast({
				title: 'Failed to unbind credential',
				description: err?.body?.error ?? err?.message,
				variant: 'error',
			}),
	});

	const updateMutation = useMutation({
		mutationFn: () =>
			api.updateToolkit(toolkitId, { name: editName || null, description: editDesc || null }),
		onSuccess: () => {
			invalidateToolkitSurfaces();
			setShowSettings(false);
		},
	});

	const deleteMutation = useMutation({
		mutationFn: () => api.deleteToolkit(toolkitId),
		onSuccess: () => {
			// The cascade also removes API keys and agent grants, so refresh the
			// agent surfaces in addition to the toolkit lists/cards.
			invalidateToolkitSurfaces();
			queryClient.invalidateQueries({ queryKey: ['agents'] });
			setShowDeleteConfirm(false);
			onRequestClose();
		},
		onError: (e: Error) => {
			toast({
				title: 'Failed to delete toolkit',
				description: e.message,
				variant: 'error',
			});
		},
	});

	const prevShowSettings = React.useRef(false);
	React.useEffect(() => {
		// Seed the edit fields once, on the open transition only — not on every
		// background refetch of `toolkit` while the dialog is open (that would
		// clobber the user's in-progress edits). See dialog-state-lifecycle.mdc.
		if (showSettings && !prevShowSettings.current && toolkit) {
			setEditName(toolkit.name);
			setEditDesc(toolkit.description ?? '');
		}
		prevShowSettings.current = showSettings;
	}, [toolkit, showSettings]);

	const id = toolkitId;

	if (isLoading)
		return (
			<div className="space-y-6" data-testid="toolkit-loading">
				<div className="space-y-2">
					<div className="bg-muted h-7 w-48 animate-pulse rounded" />
					<div className="bg-muted h-4 w-72 animate-pulse rounded" />
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

	const credentials = Array.isArray(toolkit.credentials) ? toolkit.credentials : [];

	return (
		<div className={isSheet ? 'space-y-5' : 'space-y-6'}>
			{!isSheet && (
				<div className="flex flex-wrap items-start justify-between gap-4">
					<div className="min-w-0">
						<p className="text-primary/75 font-mono text-[10px] tracking-widest uppercase">
							Toolkit
						</p>
						<h1 className="font-heading text-foreground mt-1 text-2xl font-bold">
							{toolkit.name}
						</h1>
						{toolkit.description && (
							<p className="text-muted-foreground mt-1 max-w-prose">
								{toolkit.description}
							</p>
						)}
						<div className="mt-2 flex flex-wrap items-center gap-2">
							<span className="bg-muted text-muted-foreground inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 font-mono text-xs">
								{toolkit.id}
								<CopyButton value={toolkit.id} size="icon" variant="ghost" />
							</span>
							{toolkit.simulate && <Badge variant="default">simulate mode</Badge>}
						</div>
					</div>
					<div className="flex shrink-0 items-center gap-2">
						<ToolkitKillSwitch toolkitId={id} disabled={!!toolkit.disabled} />
						{id !== 'default' && (
							<>
								<Button
									variant="secondary"
									size="sm"
									onClick={() => setShowSettings(true)}
								>
									<Settings className="h-4 w-4" /> Edit
								</Button>
								<Button
									variant="danger"
									size="sm"
									onClick={() => setShowDeleteConfirm(true)}
									aria-label="Delete toolkit"
								>
									<Trash2 className="h-4 w-4" />
								</Button>
							</>
						)}
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
						<ToolkitKillSwitch toolkitId={id} disabled={!!toolkit.disabled} />
						{id !== 'default' && (
							<>
								<Button
									variant="secondary"
									size="sm"
									onClick={() => setShowSettings(true)}
								>
									<Settings className="h-4 w-4" /> Edit
								</Button>
								<Button
									variant="danger"
									size="sm"
									onClick={() => setShowDeleteConfirm(true)}
									aria-label="Delete toolkit"
								>
									<Trash2 className="h-4 w-4" />
								</Button>
							</>
						)}
					</div>
				</div>
			)}

			{/* Suspended banner — single source of truth that the kill switch
			    blocks *everything*, not just toolkit API keys. The broker
			    enforces `toolkits.disabled` for agent-identity callers too, so
			    surface that here rather than implying only keys are affected. */}
			<AnimatePresence initial={false}>
				{toolkit.disabled && (
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

			{/* Pending requests */}
			{pendingError && <ErrorAlert message="Failed to load pending access requests." />}
			{pending.length > 0 && (
				<div className="bg-warning/10 border-warning/30 space-y-3 rounded-xl border p-5">
					<div className="flex items-center gap-2">
						<AlertTriangle className="text-warning h-5 w-5" />
						<h2 className="font-heading text-warning font-semibold">
							{pending.length} Pending Access Request{pending.length !== 1 ? 's' : ''}
						</h2>
					</div>
					{pending.map((req: any) => (
						<div
							key={req.id}
							className="bg-card/60 flex items-center gap-3 rounded-lg px-4 py-3"
						>
							<div className="flex-1">
								<Badge variant={req.type === 'grant' ? 'default' : 'pending'}>
									{req.type === 'grant'
										? 'credential access'
										: 'permission change'}
								</Badge>
								{req.reason && (
									<p className="text-muted-foreground mt-0.5 text-xs">
										{req.reason}
									</p>
								)}
							</div>
							<AppLink
								href={`/approve/${toolkit.id}/${req.id}`}
								className="bg-primary text-primary-foreground hover:bg-primary/90 inline-flex h-8 items-center rounded-md px-3 text-sm font-medium transition-colors"
							>
								Review
							</AppLink>
						</div>
					))}
				</div>
			)}

			{/* API Keys */}
			<div
				className={`overflow-hidden rounded-xl border ${toolkit.disabled ? 'border-danger/50' : 'border-border'} bg-card`}
			>
				<div
					className={`flex flex-wrap items-center justify-between gap-3 px-4 py-3.5 sm:px-5 sm:py-4 ${toolkit.disabled ? 'border-danger/30 bg-danger/5 border-b' : 'border-border'}`}
				>
					<div className="flex items-center gap-2">
						<h3 className="font-heading text-foreground font-semibold">
							API Keys ({keys.length})
						</h3>
						{toolkit.disabled && (
							<span className="bg-danger/15 text-danger border-danger/30 inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-xs">
								<Ban className="h-3 w-3" />
								Keys blocked
							</span>
						)}
					</div>
					<div className="flex items-center gap-2">
						{!toolkit.disabled && (
							<Button size="sm" onClick={() => setShowKeyCreate(true)}>
								<Plus className="h-4 w-4" /> Create Key
							</Button>
						)}
					</div>
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
									title="New API Key Created"
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
										placeholder="Key name (optional)"
										aria-label="Key name"
										autoFocus
										onKeyDown={(e) => {
											if (e.key === 'Enter' && !createKeyMutation.isPending) {
												createKeyMutation.mutate({ name: keyName || null });
											}
										}}
									/>
									<div className="flex gap-2">
										<Button
											size="sm"
											onClick={() =>
												createKeyMutation.mutate({ name: keyName || null })
											}
											loading={createKeyMutation.isPending}
										>
											{createKeyMutation.isPending
												? 'Generating...'
												: 'Generate'}
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
						{keys.map((key: any) => (
							<motion.div
								key={key.id}
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
										{key.prefix && (
											<code className="text-muted-foreground font-mono text-xs">
												{key.prefix}...
											</code>
										)}
										{key.revoked_at && <Badge variant="danger">revoked</Badge>}
									</div>
									{key.created_at && (
										<p className="text-muted-foreground truncate text-xs">
											Created{' '}
											{new Date(key.created_at * 1000).toLocaleString()}
										</p>
									)}
								</div>
								{!key.revoked_at && (
									<div className="ml-auto w-full sm:w-auto">
										<ConfirmInline
											onConfirm={() => revokeKeyMutation.mutate(key.id)}
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
										</ConfirmInline>
									</div>
								)}
							</motion.div>
						))}
					</AnimatePresence>
				</div>
			</div>

			{/* Credentials */}
			<div className="bg-card border-border overflow-hidden rounded-xl border">
				<div className="border-border flex flex-wrap items-center justify-between gap-2 border-b px-4 py-3.5 sm:px-5 sm:py-4">
					<h3 className="font-heading text-foreground font-semibold">
						Bound Credentials ({credentials.length})
					</h3>
					<AppLink
						href="/credentials"
						className="border-border bg-card text-foreground hover:bg-muted inline-flex h-8 items-center gap-1.5 rounded-md border px-3 text-sm font-medium transition-colors"
					>
						<LinkIcon className="h-4 w-4" /> Manage
					</AppLink>
				</div>
				{id !== 'default' && (
					<div className="border-border/60 flex flex-wrap items-center gap-2 border-b px-4 py-3 sm:px-5">
						<Button variant="secondary" size="sm" onClick={() => setBindOpen(true)}>
							<LinkIcon className="h-4 w-4" /> Bind existing
						</Button>
						<Button
							size="sm"
							onClick={() => addDialog.openForToolkit(id, toolkit.name ?? toolkit.id)}
						>
							<Plus className="h-4 w-4" /> Add credential
						</Button>
					</div>
				)}
				<div className="space-y-2 px-4 py-3.5 sm:px-5 sm:py-4">
					{credentials.length === 0 ? (
						<div className="border-border/50 rounded-lg border border-dashed px-5 py-6 text-center">
							<Key className="text-muted-foreground/50 mx-auto h-6 w-6" />
							<p className="text-muted-foreground mt-2 text-sm">
								No credentials bound. Bind credentials to grant this toolkit API
								access.
							</p>
							{id !== 'default' && (
								<div className="mt-2 flex items-center justify-center gap-2">
									<Button
										variant="ghost"
										size="sm"
										onClick={() => setBindOpen(true)}
										className="text-primary hover:text-primary/80 inline-flex items-center gap-1 text-sm font-medium"
									>
										<LinkIcon className="h-3.5 w-3.5" /> Bind existing
									</Button>
									<span className="text-muted-foreground text-xs">or</span>
									<Button
										variant="ghost"
										size="sm"
										onClick={() =>
											addDialog.openForToolkit(id, toolkit.name ?? toolkit.id)
										}
										className="text-primary hover:text-primary/80 inline-flex items-center gap-1 text-sm font-medium"
									>
										<Plus className="h-3.5 w-3.5" /> Add new
									</Button>
								</div>
							)}
						</div>
					) : (
						<AnimatePresence initial={false}>
							{credentials.map((cred: any) => (
								<motion.div
									key={cred.credential_id}
									{...rowMotion}
									layout
									className="bg-muted/30 border-border/60 hover:border-border overflow-hidden rounded-lg border transition-colors"
								>
									<div className="flex flex-wrap items-center gap-3 px-4 py-3">
										<VendorIcon
											name={cred.label ?? cred.api_id ?? 'credential'}
											vendor={cred.api_id ?? undefined}
											size="md"
										/>
										<div className="min-w-0 flex-1 basis-40">
											{isSheet ? (
												<span className="text-foreground text-sm font-medium">
													{cred.label}
												</span>
											) : (
												<button
													type="button"
													onClick={() =>
														editSheet.openSheet(cred.credential_id)
													}
													className="text-foreground hover:text-primary text-left text-sm font-medium focus-visible:underline focus-visible:outline-none"
												>
													{cred.label}
												</button>
											)}
											{cred.api_id && (
												<p className="text-muted-foreground truncate font-mono text-xs">
													{cred.api_id}
												</p>
											)}
											{Array.isArray(cred.permissions) && (
												<p className="text-muted-foreground mt-0.5 text-xs">
													{
														cred.permissions.filter(
															(r: any) => !r._system,
														).length
													}{' '}
													agent rule(s) + system safety
												</p>
											)}
										</div>
										<div className="ml-auto flex w-full shrink-0 items-center justify-end gap-1.5 sm:w-auto">
											<Button
												variant="secondary"
												size="sm"
												onClick={() =>
													setEditingPermForCred(
														editingPermForCred === cred.credential_id
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
											{id !== 'default' && (
												<ConfirmInline
													onConfirm={() =>
														unbindMutation.mutate(cred.credential_id)
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
												</ConfirmInline>
											)}
										</div>
									</div>
									<AnimatePresence initial={false}>
										{editingPermForCred === cred.credential_id && (
											<motion.div
												{...panelMotion}
												className="overflow-hidden"
											>
												<CredentialPermissionEditor
													toolkitId={id}
													credential={cred}
													onClose={() => setEditingPermForCred(null)}
												/>
											</motion.div>
										)}
									</AnimatePresence>
								</motion.div>
							))}
						</AnimatePresence>
					)}
				</div>
			</div>

			{/* Bound Agents */}
			<div
				className={`overflow-hidden rounded-xl border ${toolkit.disabled ? 'border-danger/50' : 'border-border'} bg-card`}
			>
				<div
					className={`flex flex-wrap items-center justify-between gap-2 px-4 py-3.5 sm:px-5 sm:py-4 ${toolkit.disabled ? 'border-danger/30 bg-danger/5 border-b' : 'border-border border-b'}`}
				>
					<div className="flex items-center gap-2">
						<h3 className="font-heading text-foreground font-semibold">
							Bound Agents ({agents.length})
						</h3>
						{toolkit.disabled && (
							<span className="bg-danger/15 text-danger border-danger/30 inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-xs">
								<Ban className="h-3 w-3" />
								Agents blocked
							</span>
						)}
					</div>
					<AppLink
						href="/agents"
						className="border-border bg-card text-foreground hover:bg-muted inline-flex h-8 items-center gap-1.5 rounded-md border px-3 text-sm font-medium transition-colors"
					>
						<Bot className="h-4 w-4" /> Manage agents
					</AppLink>
				</div>
				<div className="space-y-2 px-4 py-3.5 sm:px-5 sm:py-4">
					{agentsError ? (
						<ErrorAlert message="Failed to load agents for this toolkit." />
					) : agentsLoading ? (
						<div className="space-y-2">
							<RowSkeleton />
							<RowSkeleton />
						</div>
					) : agents.length === 0 ? (
						<div className="border-border/50 rounded-lg border border-dashed px-5 py-6 text-center">
							<Bot className="text-muted-foreground/50 mx-auto h-6 w-6" />
							<p className="text-muted-foreground mt-2 text-sm">
								No agents are granted this toolkit yet. Grant access from the{' '}
								<AppLink
									href="/agents"
									className="text-primary hover:text-primary/80 font-medium"
								>
									Agents
								</AppLink>{' '}
								page.
							</p>
						</div>
					) : (
						<AnimatePresence initial={false}>
							{agents.map((agent) => {
								const isRevoking =
									revokeAgentMutation.isPending &&
									revokeAgentMutation.variables === agent.client_id;
								return (
									<motion.div
										key={agent.client_id}
										{...rowMotion}
										layout
										className={`bg-muted/30 border-border/60 hover:border-border flex flex-wrap items-center gap-3 overflow-hidden rounded-lg border px-4 py-3 transition-colors ${
											isRevoking ? 'opacity-50' : ''
										}`}
									>
										<div className="bg-primary/10 text-primary flex h-8 w-8 shrink-0 items-center justify-center rounded-lg">
											<Bot className="h-4 w-4" />
										</div>
										<div className="min-w-0 flex-1 basis-40">
											<div className="flex items-center gap-2">
												<span className="text-foreground truncate text-sm font-medium">
													{agent.client_name || agent.client_id}
												</span>
												{agent.status === 'approved' ? (
													<Badge variant="success">approved</Badge>
												) : agent.status === 'disabled' ? (
													<Badge variant="danger">disabled</Badge>
												) : (
													<Badge variant="pending">{agent.status}</Badge>
												)}
											</div>
											<p className="text-muted-foreground truncate font-mono text-xs">
												{agent.client_id}
											</p>
										</div>
										<div className="ml-auto w-full sm:w-auto">
											<ConfirmInline
												onConfirm={() =>
													revokeAgentMutation.mutate(agent.client_id)
												}
												message="Revoke this agent's access?"
												confirmLabel="Revoke"
											>
												<Button
													variant="danger"
													size="sm"
													disabled={isRevoking}
													className="inline-flex items-center gap-1 px-2 py-1 text-xs"
												>
													<Unlink className="h-3 w-3" /> Revoke
												</Button>
											</ConfirmInline>
										</div>
									</motion.div>
								);
							})}
						</AnimatePresence>
					)}
				</div>
			</div>

			{/* Settings Modal */}
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
							onClick={() => updateMutation.mutate()}
							loading={updateMutation.isPending}
						>
							{updateMutation.isPending ? 'Saving...' : 'Save Changes'}
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
							resizable="none"
						/>
					</div>
				</div>
			</Dialog>

			<ConfirmDeleteDialog
				target={
					showDeleteConfirm
						? { kind: 'toolkit', id: toolkitId, name: toolkit.name ?? toolkit.id }
						: null
				}
				open={showDeleteConfirm}
				onClose={() => {
					if (!deleteMutation.isPending) setShowDeleteConfirm(false);
				}}
				onConfirm={() => deleteMutation.mutate()}
				loading={deleteMutation.isPending}
			/>

			{!isSheet && (
				<CredentialEditSheet
					credentialId={editSheet.stickyId}
					open={editSheet.open}
					onClose={editSheet.closeSheet}
					onAfterClose={editSheet.clearSticky}
				/>
			)}

			<AddCredentialDialog
				state={addDialog.state}
				onClose={addDialog.close}
				onGoToStep={addDialog.goToStep}
				onSelectApi={addDialog.setSelectedApi}
				onSavedCredentialId={addDialog.setSavedCredentialId}
			/>

			{id !== 'default' && (
				<BindExistingCredentialDialog
					open={bindOpen}
					toolkitId={id}
					toolkitName={toolkit.name ?? toolkit.id}
					excludeCredentialIds={credentials.map((c: any) => c.credential_id)}
					onClose={() => setBindOpen(false)}
				/>
			)}
		</div>
	);
}
