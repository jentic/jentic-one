import { useState } from 'react';
import { Plus, Trash2, Pencil, KeyRound, RotateCcw, ShieldAlert } from 'lucide-react';
import {
	Button,
	CopyButton,
	Dialog,
	EmptyState,
	Input,
	Label,
	LoadingState,
	PageHelp,
	toast,
} from '@/shared/ui';
import {
	useOAuthClients,
	useCreateOAuthClient,
	useUpdateOAuthClient,
	useDeactivateOAuthClient,
	useReactivateOAuthClient,
	useRotateOAuthClientSecret,
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

function RedirectUriList({ uris, onChange }: RedirectUriListProps) {
	const handleChange = (index: number, value: string): void => {
		const updated = [...uris];
		updated[index] = value;
		onChange(updated);
	};

	const handleRemove = (index: number): void => {
		onChange(uris.filter((_, i) => i !== index));
	};

	const handleAdd = (): void => {
		onChange([...uris, '']);
	};

	return (
		<div className="space-y-2">
			{uris.map((uri, index) => (
				<div key={`uri-${index}`} className="flex items-center gap-2">
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
			if (isEdit) {
				await updateMutation.mutateAsync({
					id: client.id,
					input: {
						name: name.trim(),
						description: description.trim() || undefined,
						redirect_uris: uris,
						require_consent: requireConsent,
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

export function OAuthClientsSection() {
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
									<h3 className="text-foreground font-medium">
										{client.name}
										{!client.active && (
											<span className="bg-muted text-muted-foreground ml-2 rounded px-1.5 py-0.5 text-xs">
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
									{client.active && (
										<Button
											variant="ghost"
											size="sm"
											onClick={(): void => setRotateTarget(client)}
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
											onClick={(): void => void handleReactivate(client)}
											disabled={reactivateMutation.isPending}
										>
											<RotateCcw className="h-4 w-4" />
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
									<CopyButton
										value={client.client_id}
										variant="ghost"
										size="icon"
									/>
								</div>
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
								<div>
									<span className="text-muted-foreground">
										Consent required:{' '}
									</span>
									<span className="text-foreground">
										{client.require_consent ? 'Yes' : 'No'}
									</span>
								</div>
							</div>
						</div>
					))}
				</div>
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
					onClose={(): void => setRevealedSecret(null)}
					secret={revealedSecret}
					title={secretDialogTitle}
				/>
			)}
		</section>
	);
}
