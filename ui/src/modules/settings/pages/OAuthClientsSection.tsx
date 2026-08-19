import { useState } from 'react';
import { Plus, Copy, Check, Trash2, Pencil } from 'lucide-react';
import { Button, Dialog, Input, Label, PageHelp, Textarea, toast } from '@/shared/ui';
import {
	useOAuthClients,
	useCreateOAuthClient,
	useUpdateOAuthClient,
	useDeactivateOAuthClient,
	type OAuthClient,
} from '@/modules/settings/api/hooks';

function CopyButton({ value }: { value: string }) {
	const [copied, setCopied] = useState(false);

	const handleCopy = async (): Promise<void> => {
		await navigator.clipboard.writeText(value);
		setCopied(true);
		setTimeout(() => setCopied(false), 2000);
	};

	return (
		<button
			type="button"
			onClick={(): void => void handleCopy()}
			className="text-muted-foreground hover:text-foreground ml-2 inline-flex"
			title="Copy to clipboard"
		>
			{copied ? <Check className="h-4 w-4 text-green-500" /> : <Copy className="h-4 w-4" />}
		</button>
	);
}

interface CreateEditDialogProps {
	open: boolean;
	onClose: () => void;
	client?: OAuthClient;
}

function CreateEditDialog({ open, onClose, client }: CreateEditDialogProps) {
	const [name, setName] = useState(client?.name ?? '');
	const [description, setDescription] = useState(client?.description ?? '');
	const [redirectUris, setRedirectUris] = useState(client?.redirect_uris.join('\n') ?? '');
	const [requireConsent, setRequireConsent] = useState(client?.require_consent ?? true);

	const createMutation = useCreateOAuthClient();
	const updateMutation = useUpdateOAuthClient();

	const isEdit = !!client;
	const isPending = createMutation.isPending || updateMutation.isPending;

	const handleSubmit = async (e: React.FormEvent): Promise<void> => {
		e.preventDefault();
		const uris = redirectUris
			.split('\n')
			.map((s) => s.trim())
			.filter(Boolean);

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
			} else {
				await createMutation.mutateAsync({
					name: name.trim(),
					description: description.trim() || undefined,
					redirect_uris: uris,
					require_consent: requireConsent,
				});
				toast({ title: 'OAuth client created', variant: 'success' });
			}
			onClose();
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
					<Button
						onClick={(): void =>
							void handleSubmit({ preventDefault: () => {} } as React.FormEvent)
						}
						disabled={isPending}
					>
						{isPending ? 'Saving...' : isEdit ? 'Update' : 'Create'}
					</Button>
				</>
			}
		>
			<form onSubmit={(e): void => void handleSubmit(e)} className="space-y-4">
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
					<Label htmlFor="redirect_uris">Redirect URIs (one per line)</Label>
					<Textarea
						id="redirect_uris"
						value={redirectUris}
						onChange={(e): void => setRedirectUris(e.target.value)}
						placeholder="https://example.com/callback"
						rows={3}
						required
					/>
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

export function OAuthClientsSection() {
	const [createOpen, setCreateOpen] = useState(false);
	const [editClient, setEditClient] = useState<OAuthClient | null>(null);
	const [deleteTarget, setDeleteTarget] = useState<OAuthClient | null>(null);
	const [showInactive, setShowInactive] = useState(false);

	const { data: clients, isLoading, error, refetch } = useOAuthClients(showInactive);
	const deactivateMutation = useDeactivateOAuthClient();

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

	return (
		<div>
			<div className="mb-6 flex items-center justify-between">
				<div>
					<h2 className="text-foreground text-lg font-semibold">OAuth Clients</h2>
					<p className="text-muted-foreground text-sm">
						Manage third-party applications that can authenticate users via Jentic One.
					</p>
				</div>
				<div className="flex items-center gap-2">
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
				<p className="text-muted-foreground">Loading...</p>
			) : error ? (
				<p className="text-destructive">Failed to load OAuth clients</p>
			) : !clients?.length ? (
				<div className="text-muted-foreground rounded-lg border border-dashed p-8 text-center">
					<p>No OAuth clients registered yet.</p>
					<Button className="mt-4" onClick={(): void => setCreateOpen(true)}>
						<Plus className="mr-2 h-4 w-4" />
						Create your first client
					</Button>
				</div>
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
								<div className="flex gap-2">
									<Button
										variant="ghost"
										size="sm"
										onClick={(): void => setEditClient(client)}
									>
										<Pencil className="h-4 w-4" />
									</Button>
									{client.active && (
										<Button
											variant="ghost"
											size="sm"
											onClick={(): void => setDeleteTarget(client)}
										>
											<Trash2 className="h-4 w-4" />
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
									<CopyButton value={client.client_id} />
								</div>
								<div>
									<span className="text-muted-foreground">Redirect URIs: </span>
									<span className="text-foreground">
										{client.redirect_uris.join(', ')}
									</span>
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

			<CreateEditDialog open={createOpen} onClose={(): void => setCreateOpen(false)} />

			{editClient && (
				<CreateEditDialog
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
						authorization flows. Existing sessions are not affected.
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
		</div>
	);
}
