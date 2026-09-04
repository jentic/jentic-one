import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router';
import {
	Plus,
	Trash2,
	Pencil,
	KeyRound,
	RotateCcw,
	ShieldAlert,
	ShieldQuestion,
	CheckCircle2,
	X,
} from 'lucide-react';
import {
	Badge,
	Button,
	CopyButton,
	Dialog,
	EmptyState,
	Input,
	Label,
	LoadingState,
	PageHelp,
	SegmentedToggle,
	TabNav,
	toast,
	type SegmentedToggleOption,
	type TabNavOption,
} from '@/shared/ui';
import {
	useOAuthClients,
	useOAuthClientQueue,
	useApproveOAuthClient,
	useDenyOAuthClient,
	useCreateOAuthClient,
	useUpdateOAuthClient,
	useDeactivateOAuthClient,
	useReactivateOAuthClient,
	useRotateOAuthClientSecret,
	usePermissionCatalogue,
	type OAuthClient,
} from '@/modules/settings/api/hooks';

interface SecretDialogProps {
	open: boolean;
	onClose: () => void;
	secret: string;
	title: string;
}

function SecretDialog({ open, onClose, secret, title }: SecretDialogProps) {
	return (
		<Dialog
			open={open}
			onClose={onClose}
			title={title}
			dismissOnBackdrop={false}
			footer={<Button onClick={onClose}>Done</Button>}
		>
			<div className="space-y-3">
				<p className="text-muted-foreground text-xs">
					Copy this secret now — it is shown only once and cannot be retrieved again.
				</p>
				<div className="bg-card border-border flex items-center gap-2 rounded-md border p-2">
					<code className="text-foreground min-w-0 flex-1 overflow-x-auto font-mono text-xs">
						{secret}
					</code>
					<CopyButton value={secret} variant="ghost" size="icon" toastMessage={false} />
				</div>
			</div>
		</Dialog>
	);
}

interface RedirectUriListProps {
	uris: string[];
	onChange: (uris: string[]) => void;
}

const nextUriKey = { current: 0 };

function RedirectUriList({ uris, onChange }: RedirectUriListProps) {
	const [keys, setKeys] = useState(() => uris.map(() => nextUriKey.current++));

	const handleChange = (index: number, value: string): void => {
		const updated = [...uris];
		updated[index] = value;
		onChange(updated);
	};

	const handleRemove = (index: number): void => {
		setKeys((prev) => prev.filter((_, i) => i !== index));
		onChange(uris.filter((_, i) => i !== index));
	};

	const handleAdd = (): void => {
		setKeys((prev) => [...prev, nextUriKey.current++]);
		onChange([...uris, '']);
	};

	return (
		<div className="space-y-2">
			{uris.map((uri, index) => (
				<div key={keys[index]} className="flex items-center gap-2">
					<Input
						value={uri}
						onChange={(e): void => handleChange(index, e.target.value)}
						placeholder="https://example.com/callback"
						className="flex-1"
					/>
					<Button
						type="button"
						variant="ghost"
						size="sm"
						onClick={(): void => handleRemove(index)}
						disabled={uris.length <= 1}
						aria-label="Remove URI"
					>
						<Trash2 className="h-4 w-4" />
					</Button>
				</div>
			))}
			<Button type="button" variant="outline" size="sm" onClick={handleAdd}>
				<Plus className="mr-1 h-3 w-3" />
				Add URI
			</Button>
		</div>
	);
}

interface ScopeSelectorProps {
	scopes: string[];
	onChange: (scopes: string[]) => void;
}

function ScopeSelector({ scopes, onChange }: ScopeSelectorProps) {
	const [customInput, setCustomInput] = useState('');
	const { data: catalogue = [] } = usePermissionCatalogue();
	const catalogueNames = catalogue.map((p) => p.name);

	const toggle = (scope: string): void => {
		if (scopes.includes(scope)) {
			onChange(scopes.filter((s) => s !== scope));
		} else {
			onChange([...scopes, scope]);
		}
	};

	const addCustom = (): void => {
		const trimmed = customInput.trim();
		if (trimmed && !scopes.includes(trimmed)) {
			onChange([...scopes, trimmed]);
			setCustomInput('');
		}
	};

	return (
		<div className="space-y-2">
			<div className="flex flex-wrap gap-1.5">
				{catalogue.map((perm) => {
					const selected = scopes.includes(perm.name);
					return (
						<button
							key={perm.name}
							type="button"
							onClick={(): void => toggle(perm.name)}
							title={perm.description}
							className={`rounded-full border px-2 py-0.5 text-xs transition ${
								selected
									? 'border-primary bg-primary/10 text-primary'
									: 'border-border text-muted-foreground hover:border-primary/50'
							}`}
						>
							{perm.name}
						</button>
					);
				})}
			</div>
			{scopes.filter((s) => !catalogueNames.includes(s)).length > 0 && (
				<div className="flex flex-wrap gap-1">
					{scopes
						.filter((s) => !catalogueNames.includes(s))
						.map((s) => (
							<span
								key={s}
								className="border-primary bg-primary/10 text-primary inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs"
							>
								{s}
								<button
									type="button"
									onClick={(): void => onChange(scopes.filter((x) => x !== s))}
									className="hover:text-destructive"
								>
									<X className="h-3 w-3" />
								</button>
							</span>
						))}
				</div>
			)}
			<div className="flex items-center gap-2">
				<Input
					value={customInput}
					onChange={(e): void => setCustomInput(e.target.value)}
					placeholder="custom:scope"
					className="flex-1"
					onKeyDown={(e): void => {
						if (e.key === 'Enter') {
							e.preventDefault();
							addCustom();
						}
					}}
				/>
				<Button type="button" variant="outline" size="sm" onClick={addCustom}>
					Add
				</Button>
			</div>
		</div>
	);
}

interface CreateEditDialogProps {
	open: boolean;
	onClose: () => void;
	onSecretRevealed?: (secret: string) => void;
	client?: OAuthClient;
}

function CreateEditDialog({ open, onClose, onSecretRevealed, client }: CreateEditDialogProps) {
	const [name, setName] = useState(client?.name ?? '');
	const [description, setDescription] = useState(client?.description ?? '');
	const [redirectUris, setRedirectUris] = useState<string[]>(
		client?.redirect_uris.length ? client.redirect_uris : [''],
	);
	const [requireConsent, setRequireConsent] = useState(client?.require_consent ?? true);
	const [restrictScopes, setRestrictScopes] = useState(client?.allowed_scopes != null);
	const [allowedScopes, setAllowedScopes] = useState<string[]>(client?.allowed_scopes ?? []);

	const createMutation = useCreateOAuthClient();
	const updateMutation = useUpdateOAuthClient();

	const isEdit = !!client;
	const isPending = createMutation.isPending || updateMutation.isPending;

	const handleSubmit = async (e: React.FormEvent): Promise<void> => {
		e.preventDefault();
		const uris = redirectUris.map((s) => s.trim()).filter(Boolean);

		if (!name.trim() || uris.length === 0) {
			toast({ title: 'Name and at least one redirect URI required', variant: 'error' });
			return;
		}

		try {
			const scopePayload = restrictScopes ? allowedScopes : null;
			if (isEdit) {
				await updateMutation.mutateAsync({
					id: client.id,
					input: {
						name: name.trim(),
						description: description.trim() || null,
						redirect_uris: uris,
						require_consent: requireConsent,
						allowed_scopes: scopePayload,
					},
				});
				toast({ title: 'OAuth client updated', variant: 'success' });
				onClose();
			} else {
				const result = await createMutation.mutateAsync({
					name: name.trim(),
					description: description.trim() || undefined,
					redirect_uris: uris,
					require_consent: requireConsent,
					allowed_scopes: scopePayload,
				});
				onClose();
				if (result.client_secret) {
					onSecretRevealed?.(result.client_secret);
				}
			}
		} catch (err) {
			toast({
				title: isEdit ? 'Failed to update client' : 'Failed to create client',
				description: err instanceof Error ? err.message : undefined,
				variant: 'error',
			});
		}
	};

	return (
		<Dialog
			open={open}
			onClose={onClose}
			title={isEdit ? 'Edit OAuth Client' : 'Create OAuth Client'}
			footer={
				<>
					<Button variant="outline" onClick={onClose}>
						Cancel
					</Button>
					<Button type="submit" form="oauth-client-form" disabled={isPending}>
						{isPending ? 'Saving...' : isEdit ? 'Update' : 'Create'}
					</Button>
				</>
			}
		>
			<form
				id="oauth-client-form"
				onSubmit={(e): void => void handleSubmit(e)}
				className="space-y-4"
			>
				<div>
					<Label htmlFor="name">Name</Label>
					<Input
						id="name"
						value={name}
						onChange={(e): void => setName(e.target.value)}
						placeholder="e.g., my-app-production"
						required
					/>
				</div>
				<div>
					<Label htmlFor="description">Description (optional)</Label>
					<Input
						id="description"
						value={description}
						onChange={(e): void => setDescription(e.target.value)}
						placeholder="e.g., Production deployment for user auth"
					/>
				</div>
				<div>
					<Label>Redirect URIs</Label>
					<RedirectUriList uris={redirectUris} onChange={setRedirectUris} />
				</div>
				<div className="flex items-center gap-2">
					<input
						type="checkbox"
						id="require_consent"
						checked={requireConsent}
						onChange={(e): void => setRequireConsent(e.target.checked)}
						className="h-4 w-4"
					/>
					<Label htmlFor="require_consent">Require consent screen</Label>
				</div>
				<div className="space-y-2">
					<div className="flex items-center gap-2">
						<input
							type="checkbox"
							id="restrict_scopes"
							checked={restrictScopes}
							onChange={(e): void => setRestrictScopes(e.target.checked)}
							className="h-4 w-4"
						/>
						<Label htmlFor="restrict_scopes">Restrict allowed scopes</Label>
					</div>
					{restrictScopes && (
						<ScopeSelector scopes={allowedScopes} onChange={setAllowedScopes} />
					)}
					{restrictScopes && allowedScopes.length === 0 && (
						<p className="text-muted-foreground text-xs">
							No scopes selected — this client will only be able to request OIDC
							scopes (openid, email, profile).
						</p>
					)}
				</div>
			</form>
		</Dialog>
	);
}

interface RotateConfirmDialogProps {
	open: boolean;
	onClose: () => void;
	onConfirm: () => void;
	isPending: boolean;
	clientName: string;
}

function RotateConfirmDialog({
	open,
	onClose,
	onConfirm,
	isPending,
	clientName,
}: RotateConfirmDialogProps) {
	return (
		<Dialog
			open={open}
			onClose={onClose}
			title="Rotate Client Secret?"
			footer={
				<>
					<Button variant="outline" onClick={onClose}>
						Cancel
					</Button>
					<Button variant="danger" onClick={onConfirm} disabled={isPending}>
						{isPending ? 'Rotating...' : 'Rotate Secret'}
					</Button>
				</>
			}
		>
			<p className="text-muted-foreground">
				This will invalidate the current secret for <strong>{clientName}</strong>{' '}
				immediately. Any application using the old secret will lose access.
			</p>
		</Dialog>
	);
}

/**
 * Row badges shared by the clients list and the approval queue: `Public`
 * (secret-less PKCE client, D5), `DCR` (front-door registration, D8), and the
 * approval state when it isn't plain `approved` (D7).
 */
function ClientBadges({ client }: { client: OAuthClient }) {
	return (
		<>
			{client.token_endpoint_auth_method === 'none' && (
				<Badge variant="default">Public</Badge>
			)}
			{client.registration_source === 'dcr' && <Badge variant="default">DCR</Badge>}
			{client.approval_status === 'pending' && <Badge variant="pending">Pending</Badge>}
			{client.approval_status === 'denied' && <Badge variant="danger">Denied</Badge>}
		</>
	);
}

interface DenyClientDialogProps {
	/** The client under decision; null only before the first Deny click. */
	client: OAuthClient | null;
	open: boolean;
	onClose: () => void;
	onConfirm: (reason: string) => void;
	isPending: boolean;
}

/**
 * Deny confirmation with an optional reason draft. Mounted once and toggled
 * via `open` (dialog-state rule: persist between dismissals, reset on
 * successful commit) — a casual Esc/backdrop dismiss keeps the half-typed
 * reason. The draft resets when the TARGET changes (a different client's
 * denial is a different draft), which also covers the success path: the
 * parent clears the target after a committed deny.
 */
function DenyClientDialog({ client, open, onClose, onConfirm, isPending }: DenyClientDialogProps) {
	const [reason, setReason] = useState('');
	const lastIdRef = useRef(client?.id);
	useEffect(() => {
		if (lastIdRef.current !== client?.id) {
			lastIdRef.current = client?.id;
			setReason('');
		}
	}, [client?.id]);
	return (
		<Dialog
			open={open && client != null}
			onClose={onClose}
			title="Deny OAuth Client?"
			footer={
				<>
					<Button variant="outline" onClick={onClose}>
						Cancel
					</Button>
					<Button
						variant="danger"
						onClick={(): void => onConfirm(reason.trim())}
						disabled={isPending}
					>
						{isPending ? 'Denying...' : 'Deny'}
					</Button>
				</>
			}
		>
			<div className="space-y-3">
				<p className="text-muted-foreground">
					<strong>{client?.name}</strong> will not be able to start authorization flows.
					The registration is kept, so you can approve it later to reverse this.
				</p>
				<div>
					<Label htmlFor="deny-reason">Reason (optional)</Label>
					<Input
						id="deny-reason"
						value={reason}
						onChange={(e): void => setReason(e.target.value)}
						placeholder="e.g., unknown redirect URIs"
					/>
				</div>
			</div>
		</Dialog>
	);
}

type QueueFilter = 'pending' | 'denied';

const QUEUE_FILTER_OPTIONS: SegmentedToggleOption<QueueFilter>[] = [
	{ value: 'pending', label: 'Pending' },
	{ value: 'denied', label: 'Denied' },
];

/**
 * The DCR approval queue (phase-3a §4.8, D7): registrations land `pending` +
 * inactive; Approve activates them, Deny keeps the row (reversible — the
 * Denied filter re-offers Approve as the recovery path). Approval-first is
 * the default everywhere (D7 as amended 2026-09-03): every fresh instance's
 * first DCR client lands here. With the explicit `auto_approve_clients: true`
 * opt-in the queue is normally empty.
 */
function ApprovalQueueTab() {
	const [filter, setFilter] = useState<QueueFilter>('pending');
	const { data: clients, isLoading, error } = useOAuthClientQueue(filter);
	const approveMutation = useApproveOAuthClient();
	const denyMutation = useDenyOAuthClient();
	// Target + open are separate so a casual dismiss keeps the target (and the
	// dialog's reason draft — dialog-state rule); only a committed deny clears it.
	const [denyTarget, setDenyTarget] = useState<OAuthClient | null>(null);
	const [denyOpen, setDenyOpen] = useState(false);

	const handleApprove = async (client: OAuthClient): Promise<void> => {
		try {
			await approveMutation.mutateAsync(client.id);
			toast({ title: `${client.name} approved`, variant: 'success' });
		} catch (err) {
			toast({
				title: 'Failed to approve client',
				description: err instanceof Error ? err.message : undefined,
				variant: 'error',
			});
		}
	};

	const handleDeny = async (reason: string): Promise<void> => {
		if (!denyTarget) return;
		try {
			await denyMutation.mutateAsync({
				id: denyTarget.id,
				reason: reason || undefined,
			});
			toast({ title: `${denyTarget.name} denied`, variant: 'success' });
			setDenyOpen(false);
			// Clearing the target resets the dialog's reason draft (commit path).
			setDenyTarget(null);
		} catch (err) {
			toast({
				title: 'Failed to deny client',
				description: err instanceof Error ? err.message : undefined,
				variant: 'error',
			});
		}
	};

	return (
		<div className="space-y-4">
			<SegmentedToggle options={QUEUE_FILTER_OPTIONS} value={filter} onChange={setFilter} />

			{isLoading ? (
				<LoadingState message="Loading approval queue..." />
			) : error ? (
				<p className="text-destructive">Failed to load the approval queue</p>
			) : !clients?.length ? (
				<EmptyState
					icon={<CheckCircle2 className="h-6 w-6" />}
					title={filter === 'pending' ? 'No pending registrations' : 'No denied clients'}
					description={
						filter === 'pending'
							? 'Client registrations awaiting approval will appear here.'
							: 'Denied registrations are kept here — approving one reverses the decision.'
					}
				/>
			) : (
				<div className="space-y-4">
					{clients.map((client) => (
						<div key={client.id} className="border-border rounded-lg border p-4">
							<div className="flex items-start justify-between gap-3">
								<div className="min-w-0">
									<h3 className="text-foreground flex flex-wrap items-center gap-2 font-medium">
										{client.name}
										<ClientBadges client={client} />
									</h3>
									{client.description && (
										<p className="text-muted-foreground text-sm">
											{client.description}
										</p>
									)}
								</div>
								<div className="flex shrink-0 gap-2">
									<Button
										size="sm"
										onClick={(): void => void handleApprove(client)}
										disabled={approveMutation.isPending}
									>
										Approve
									</Button>
									{client.approval_status !== 'denied' && (
										<Button
											size="sm"
											variant="outline"
											onClick={(): void => {
												setDenyTarget(client);
												setDenyOpen(true);
											}}
											disabled={denyMutation.isPending}
										>
											Deny
										</Button>
									)}
								</div>
							</div>
							<div className="mt-3 space-y-2 text-sm">
								<div>
									<span className="text-muted-foreground">Client ID: </span>
									<code className="bg-muted rounded px-1.5 py-0.5 font-mono text-xs">
										{client.client_id}
									</code>
								</div>
								{client.software_id && (
									<div>
										<span className="text-muted-foreground">Software ID: </span>
										<code className="bg-muted rounded px-1.5 py-0.5 font-mono text-xs">
											{client.software_id}
										</code>
									</div>
								)}
								<div>
									<span className="text-muted-foreground">Redirect URIs: </span>
									<ul className="text-foreground mt-1 list-inside list-disc pl-1">
										{client.redirect_uris.map((uri) => (
											<li key={uri} className="truncate font-mono text-xs">
												{uri}
											</li>
										))}
									</ul>
								</div>
							</div>
						</div>
					))}
				</div>
			)}

			{/* Mounted persistently (not `{target && …}`) so a casual dismiss
			    keeps the reason draft — see DenyClientDialog. */}
			<DenyClientDialog
				client={denyTarget}
				open={denyOpen}
				onClose={(): void => setDenyOpen(false)}
				onConfirm={(reason): void => void handleDeny(reason)}
				isPending={denyMutation.isPending}
			/>
		</div>
	);
}

const SECTION_TABS = ['clients', 'queue'] as const;
type SectionTab = (typeof SECTION_TABS)[number];

function isSectionTab(value: string | null): value is SectionTab {
	return SECTION_TABS.includes(value as SectionTab);
}

export function OAuthClientsSection() {
	// Tab lives in `?tab=` so the rail's "Review" action on an
	// `oauth_client.registered` alert can deep-link straight to the queue.
	const [searchParams, setSearchParams] = useSearchParams();
	const tabParam = searchParams.get('tab');
	const activeTab: SectionTab = isSectionTab(tabParam) ? tabParam : 'clients';
	// Fetched at section level so the queue tab label can carry the pending
	// count even while the clients tab is active.
	const { data: pendingClients } = useOAuthClientQueue('pending');

	const setTab = (tab: SectionTab): void => {
		setSearchParams(
			(prev) => {
				const next = new URLSearchParams(prev);
				if (tab === 'clients') next.delete('tab');
				else next.set('tab', tab);
				return next;
			},
			{ replace: false },
		);
	};

	const tabOptions: TabNavOption<SectionTab>[] = [
		{ value: 'clients', label: 'Clients', icon: <KeyRound className="h-4 w-4" /> },
		{
			value: 'queue',
			label: 'Approval queue',
			icon: <ShieldQuestion className="h-4 w-4" />,
			count: pendingClients?.length || undefined,
		},
	];

	const [createOpen, setCreateOpen] = useState(false);
	const [editClient, setEditClient] = useState<OAuthClient | null>(null);
	const [deleteTarget, setDeleteTarget] = useState<OAuthClient | null>(null);
	const [rotateTarget, setRotateTarget] = useState<OAuthClient | null>(null);
	const [revealedSecret, setRevealedSecret] = useState<string | null>(null);
	const [secretDialogTitle, setSecretDialogTitle] = useState('Client Secret');
	const [showInactive, setShowInactive] = useState(false);

	const { data: clients, isLoading, error, refetch } = useOAuthClients(showInactive);
	const deactivateMutation = useDeactivateOAuthClient();
	const reactivateMutation = useReactivateOAuthClient();
	const rotateMutation = useRotateOAuthClientSecret();

	const handleDeactivate = async (): Promise<void> => {
		if (!deleteTarget) return;
		try {
			await deactivateMutation.mutateAsync(deleteTarget.id);
			toast({ title: 'OAuth client deactivated', variant: 'success' });
			setDeleteTarget(null);
		} catch (err) {
			toast({
				title: 'Failed to deactivate client',
				description: err instanceof Error ? err.message : undefined,
				variant: 'error',
			});
		}
	};

	const handleReactivate = async (client: OAuthClient): Promise<void> => {
		try {
			await reactivateMutation.mutateAsync(client.id);
			toast({ title: `${client.name} reactivated`, variant: 'success' });
		} catch (err) {
			toast({
				title: 'Failed to reactivate client',
				description: err instanceof Error ? err.message : undefined,
				variant: 'error',
			});
		}
	};

	const handleRotateConfirm = async (): Promise<void> => {
		if (!rotateTarget) return;
		try {
			const result = await rotateMutation.mutateAsync(rotateTarget.id);
			setRotateTarget(null);
			setSecretDialogTitle(`New Secret for ${rotateTarget.name}`);
			setRevealedSecret(result.client_secret);
		} catch (err) {
			toast({
				title: 'Failed to rotate secret',
				description: err instanceof Error ? err.message : undefined,
				variant: 'error',
			});
		}
	};

	const handleSecretRevealed = (secret: string): void => {
		setSecretDialogTitle('Client Secret Created');
		setRevealedSecret(secret);
	};

	return (
		<section>
			<div className="mb-6 flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
				<div className="min-w-0 flex-1">
					<h2 className="text-foreground text-lg font-semibold tracking-tight">
						OAuth Clients
					</h2>
					<p className="text-muted-foreground mt-0.5 text-sm">
						Manage third-party applications that can authenticate users via Jentic One.
					</p>
				</div>
				<div className="flex shrink-0 items-center gap-2 self-center">
					<Button onClick={(): void => setCreateOpen(true)}>
						<Plus className="h-4 w-4" />
						Add client
					</Button>
					<PageHelp
						title="About OAuth Clients"
						intro="OAuth clients are third-party applications that use Jentic One for user authentication."
						sections={[
							{
								heading: 'Client ID',
								body: 'The client_id is a public identifier used in OAuth flows. Configure it in the third-party application.',
							},
							{
								heading: 'Client Secret',
								body: 'The client secret is shown once at creation and after rotation. Store it securely — it cannot be retrieved later.',
							},
							{
								heading: 'Redirect URIs',
								body: 'OAuth callbacks are only allowed to URLs in this list. Include all environments (dev, staging, prod).',
							},
							{
								heading: 'Consent screen',
								body: 'When enabled, users see a consent screen before authorizing. Disable for trusted first-party apps.',
							},
						]}
					/>
				</div>
			</div>

			<TabNav<SectionTab>
				options={tabOptions}
				value={activeTab}
				onChange={setTab}
				ariaLabel="OAuth client sections"
				className="mb-4"
			/>

			{activeTab === 'queue' && <ApprovalQueueTab />}

			{activeTab === 'clients' && (
				<>
					<div className="mb-4 flex items-center justify-between">
						<div className="flex items-center gap-2">
							<input
								type="checkbox"
								id="show-inactive"
								checked={showInactive}
								onChange={(e): void => setShowInactive(e.target.checked)}
								className="h-4 w-4"
							/>
							<Label htmlFor="show-inactive" className="text-sm">
								Show inactive
							</Label>
						</div>
						<Button variant="ghost" size="sm" onClick={(): void => void refetch()}>
							Refresh
						</Button>
					</div>

					{isLoading ? (
						<LoadingState message="Loading OAuth clients..." />
					) : error ? (
						<p className="text-destructive">Failed to load OAuth clients</p>
					) : !clients?.length ? (
						<EmptyState
							icon={<KeyRound className="h-6 w-6" />}
							title="No OAuth clients"
							description="Register an OAuth client to allow third-party applications to authenticate with Jentic One."
							action={
								<Button onClick={(): void => setCreateOpen(true)}>
									<Plus className="mr-2 h-4 w-4" />
									Create your first client
								</Button>
							}
						/>
					) : (
						<div className="space-y-4">
							{clients.map((client) => (
								<div
									key={client.id}
									className={`border-border rounded-lg border p-4 ${!client.active ? 'opacity-50' : ''}`}
								>
									<div className="flex items-start justify-between">
										<div>
											<h3 className="text-foreground flex flex-wrap items-center gap-2 font-medium">
												{client.name}
												<ClientBadges client={client} />
												{!client.active && (
													<span className="bg-muted text-muted-foreground rounded px-1.5 py-0.5 text-xs">
														Inactive
													</span>
												)}
											</h3>
											{client.description && (
												<p className="text-muted-foreground text-sm">
													{client.description}
												</p>
											)}
										</div>
										<div className="flex gap-1">
											{client.active &&
												client.token_endpoint_auth_method !== 'none' && (
													<Button
														variant="ghost"
														size="sm"
														onClick={(): void =>
															setRotateTarget(client)
														}
														title="Rotate secret"
													>
														<ShieldAlert className="h-4 w-4" />
													</Button>
												)}
											<Button
												variant="ghost"
												size="sm"
												onClick={(): void => setEditClient(client)}
											>
												<Pencil className="h-4 w-4" />
											</Button>
											{client.active ? (
												<Button
													variant="ghost"
													size="sm"
													onClick={(): void => setDeleteTarget(client)}
												>
													<Trash2 className="h-4 w-4" />
												</Button>
											) : (
												<Button
													variant="ghost"
													size="sm"
													onClick={(): void =>
														void handleReactivate(client)
													}
													disabled={reactivateMutation.isPending}
												>
													<RotateCcw className="h-4 w-4" />
												</Button>
											)}
										</div>
									</div>
									<div className="mt-3 space-y-2 text-sm">
										<div>
											<span className="text-muted-foreground">
												Client ID:{' '}
											</span>
											<code className="bg-muted rounded px-1.5 py-0.5 font-mono text-xs">
												{client.client_id}
											</code>
											<CopyButton
												value={client.client_id}
												variant="ghost"
												size="icon"
											/>
										</div>
										<div>
											<span className="text-muted-foreground">
												Redirect URIs:{' '}
											</span>
											<ul className="text-foreground mt-1 list-inside list-disc pl-1">
												{client.redirect_uris.map((uri) => (
													<li
														key={uri}
														className="truncate font-mono text-xs"
													>
														{uri}
													</li>
												))}
											</ul>
										</div>
										<div>
											<span className="text-muted-foreground">
												Consent required:{' '}
											</span>
											<span className="text-foreground">
												{client.require_consent ? 'Yes' : 'No'}
											</span>
										</div>
										<div>
											<span className="text-muted-foreground">
												Active grants:{' '}
											</span>
											<span className="text-foreground">
												{client.active_grant_count}
											</span>
										</div>
										{client.allowed_scopes != null && (
											<div>
												<span className="text-muted-foreground">
													Allowed scopes:{' '}
												</span>
												<span className="text-foreground">
													{client.allowed_scopes.length > 0
														? client.allowed_scopes.join(', ')
														: 'OIDC only'}
												</span>
											</div>
										)}
									</div>
								</div>
							))}
						</div>
					)}
				</>
			)}

			{createOpen && (
				<CreateEditDialog
					key="create"
					open={createOpen}
					onClose={(): void => setCreateOpen(false)}
					onSecretRevealed={handleSecretRevealed}
				/>
			)}

			{editClient && (
				<CreateEditDialog
					key={editClient.id}
					open={!!editClient}
					onClose={(): void => setEditClient(null)}
					client={editClient}
				/>
			)}

			{deleteTarget != null && (
				<Dialog
					open
					onClose={(): void => setDeleteTarget(null)}
					title="Deactivate OAuth Client?"
					footer={
						<>
							<Button variant="outline" onClick={(): void => setDeleteTarget(null)}>
								Cancel
							</Button>
							<Button
								variant="danger"
								onClick={(): void => void handleDeactivate()}
								disabled={deactivateMutation.isPending}
							>
								{deactivateMutation.isPending ? 'Deactivating...' : 'Deactivate'}
							</Button>
						</>
					}
				>
					<p className="text-muted-foreground">
						This will prevent <strong>{deleteTarget.name}</strong> from initiating new
						authorization flows. Existing sessions are not affected. You can reactivate
						the client later if needed.
					</p>
					{deactivateMutation.error && (
						<p className="text-destructive mt-2 text-sm">
							{deactivateMutation.error instanceof Error
								? deactivateMutation.error.message
								: 'An error occurred'}
						</p>
					)}
				</Dialog>
			)}

			{rotateTarget != null && (
				<RotateConfirmDialog
					open
					onClose={(): void => setRotateTarget(null)}
					onConfirm={(): void => void handleRotateConfirm()}
					isPending={rotateMutation.isPending}
					clientName={rotateTarget.name}
				/>
			)}

			{revealedSecret != null && (
				<SecretDialog
					open
					onClose={(): void => {
						setRevealedSecret(null);
						rotateMutation.reset();
					}}
					secret={revealedSecret}
					title={secretDialogTitle}
				/>
			)}
		</section>
	);
}
