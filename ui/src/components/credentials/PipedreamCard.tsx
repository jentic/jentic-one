import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link2, Settings, RotateCcw, Trash2 } from 'lucide-react';
import { oauthBrokers, type OAuthBroker } from '@/api/client';
import { AppLink } from '@/components/ui/AppLink';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Label } from '@/components/ui/Label';

/**
 * "Pipedream OAuth" status + setup.
 *
 * In the old layout this lived as a free-floating one-line note above the
 * credentials list, with two completely different forms hidden behind it.
 * Now it's:
 *
 *   - one **status line** when Pipedream isn't configured (single-line affordance,
 *     "Enable OAuth via Pipedream"),
 *   - one **status line + configure link** when it IS configured,
 *   - one **inline form card** for either setup OR edit, opened from the line.
 *
 * The form card stays a child of this component so its mutations don't tear down
 * when the parent re-renders on credential list invalidation.
 */
export function PipedreamCard() {
	const [showForm, setShowForm] = useState(false);
	const [showConfigure, setShowConfigure] = useState(false);
	const queryClient = useQueryClient();

	const { data: brokersRaw } = useQuery({
		queryKey: ['oauth-brokers'],
		queryFn: () => oauthBrokers.list(),
	});
	const brokers = Array.isArray(brokersRaw) ? brokersRaw : [];
	const pipedream = brokers.find((b) => b.id === 'pipedream') ?? null;

	const { data: accountsRaw } = useQuery({
		queryKey: ['oauth-broker-accounts', 'pipedream'],
		queryFn: () => oauthBrokers.accounts('pipedream', 'default'),
		enabled: !!pipedream,
	});
	const accounts = Array.isArray(accountsRaw) ? accountsRaw : [];

	const syncMutation = useMutation({
		mutationFn: () => oauthBrokers.sync('pipedream', 'default'),
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: ['oauth-broker-accounts'] });
			queryClient.invalidateQueries({ queryKey: ['credentials'] });
		},
	});

	const lastSynced =
		accounts.length > 0 ? Math.max(...accounts.map((a) => Number(a.synced_at) || 0)) : null;

	if (showForm) {
		return (
			<PipedreamForm
				existing={pipedream ?? undefined}
				onClose={() => setShowForm(false)}
				onDeleted={() => setShowForm(false)}
			/>
		);
	}

	if (showConfigure && pipedream) {
		return (
			<div className="bg-card border-border space-y-4 rounded-xl border p-5">
				<div className="flex items-center justify-between">
					<h2 className="text-foreground text-sm font-semibold">
						Configure Pipedream OAuth
					</h2>
					<Button variant="ghost" size="sm" onClick={() => setShowConfigure(false)}>
						Close
					</Button>
				</div>
				<div className="space-y-3">
					<div className="bg-background border-border rounded-lg border p-4">
						<div className="mb-2 flex items-center justify-between">
							<h3 className="text-foreground text-xs font-semibold">Credentials</h3>
							<Button
								variant="secondary"
								size="sm"
								onClick={() => {
									setShowConfigure(false);
									setShowForm(true);
								}}
							>
								<Settings className="h-4 w-4" /> Edit credentials
							</Button>
						</div>
						<p className="text-muted-foreground text-xs">
							Client ID and secret are configured. Update if you need to rotate keys.
						</p>
					</div>

					<div className="bg-background border-border rounded-lg border p-4">
						<div className="mb-2 flex items-center justify-between">
							<h3 className="text-foreground text-xs font-semibold">
								Sync all connections
							</h3>
							<Button
								variant="secondary"
								size="sm"
								onClick={() => syncMutation.mutate()}
								loading={syncMutation.isPending}
							>
								<RotateCcw className="h-4 w-4" /> Resync
							</Button>
						</div>
						<p className="text-muted-foreground text-xs">
							Fetch the latest connected accounts from Pipedream. Use this to clean up
							defunct credentials or discover connections created elsewhere.
						</p>
						{syncMutation.isSuccess && (
							<p className="text-success mt-2 text-xs">
								✓ Sync complete — {syncMutation.data?.accounts_synced ?? 0} accounts
								refreshed
							</p>
						)}
						{syncMutation.isError && (
							<p className="text-danger mt-2 text-xs">
								Sync failed: {(syncMutation.error as Error).message}
							</p>
						)}
					</div>
				</div>
			</div>
		);
	}

	if (!pipedream) {
		return (
			<p className="text-muted-foreground text-xs">
				<Link2 className="mr-1 inline h-3 w-3 align-middle opacity-60" />
				OAuth not configured.{' '}
				<Button
					type="button"
					variant="ghost"
					className="text-primary inline h-auto p-0 hover:underline focus:outline-none"
					onClick={() => setShowForm(true)}
				>
					Enable OAuth via Pipedream
				</Button>
				.
			</p>
		);
	}

	return (
		<p className="text-muted-foreground text-xs">
			<Link2 className="text-primary mr-1 inline h-3 w-3 align-middle" />
			OAuth enabled via Pipedream
			{accounts.length > 0 && (
				<span>
					{' · '}
					{accounts.length} account{accounts.length !== 1 ? 's' : ''}
				</span>
			)}
			{lastSynced && (
				<span>
					{' · '}last synced {new Date(lastSynced * 1000).toLocaleString()}
				</span>
			)}
			{' · '}
			<Button
				type="button"
				variant="ghost"
				className="text-primary inline h-auto p-0 hover:underline focus:outline-none"
				onClick={() => setShowConfigure(true)}
			>
				<Settings className="mr-0.5 inline h-3 w-3 align-middle" />
				configure
			</Button>
		</p>
	);
}

// ── Form (edit/create Pipedream broker config) ────────────────────────────────

function PipedreamForm({
	existing,
	onClose,
	onDeleted,
}: {
	existing?: OAuthBroker;
	onClose: () => void;
	onDeleted?: () => void;
}) {
	const queryClient = useQueryClient();
	const [form, setForm] = useState({
		client_id: existing?.config?.client_id ?? '',
		client_secret: '',
		project_id: existing?.config?.project_id ?? '',
		environment: existing?.config?.environment ?? 'production',
		default_external_user_id: existing?.config?.default_external_user_id ?? 'default',
	});
	const [confirmDelete, setConfirmDelete] = useState(false);

	const set = (field: string) => (e: React.ChangeEvent<HTMLInputElement>) =>
		setForm((f) => ({ ...f, [field]: e.target.value }));

	const saveMutation = useMutation({
		mutationFn: () =>
			existing
				? oauthBrokers.update('pipedream', {
						client_id: form.client_id || undefined,
						client_secret: form.client_secret || undefined,
						project_id: form.project_id || undefined,
					})
				: oauthBrokers.create({
						id: 'pipedream',
						type: 'pipedream',
						config: {
							client_id: form.client_id,
							client_secret: form.client_secret,
							project_id: form.project_id,
							environment: form.environment,
							default_external_user_id: form.default_external_user_id,
						},
					}),
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: ['oauth-brokers'] });
			onClose();
		},
	});

	const deleteMutation = useMutation({
		mutationFn: () => oauthBrokers.delete('pipedream'),
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: ['oauth-brokers'] });
			queryClient.invalidateQueries({ queryKey: ['credentials'] });
			onDeleted?.();
		},
	});

	const isNew = !existing;
	const canSubmit = isNew
		? !!(form.client_id && form.client_secret && form.project_id)
		: !!(form.client_id || form.client_secret || form.project_id);

	return (
		<div className="bg-card border-border space-y-4 rounded-xl border p-5">
			<h2 className="text-foreground text-sm font-semibold">
				{isNew ? 'Enable OAuth with Pipedream' : 'Edit Pipedream configuration'}
			</h2>

			{isNew && (
				<div className="bg-background border-border space-y-2 rounded-lg border p-4 text-xs">
					<p className="text-foreground font-medium">One-time Pipedream setup</p>
					<ol className="text-muted-foreground ml-4 list-decimal space-y-1.5">
						<li>
							Go to{' '}
							<AppLink
								href="https://pipedream.com"
								className="text-primary underline"
							>
								pipedream.com
							</AppLink>{' '}
							and sign in or create an account.
						</li>
						<li>
							Go to <strong>Settings → API</strong> → click{' '}
							<strong>+ New OAuth Client</strong>. Name it <em>Jentic</em>. Copy the{' '}
							<strong>Client ID</strong> and <strong>Client Secret</strong> — the
							secret is not shown again.
						</li>
						<li>
							Go to <strong>Projects → + New Project</strong>. Name it <em>Jentic</em>
							. Open its <strong>Settings</strong> and copy the{' '}
							<strong>Project ID</strong> (format: <code>proj_xxx</code>).
						</li>
					</ol>
					<p className="text-muted-foreground mt-1">
						Jentic automatically configures the Connect application name and logo in
						Pipedream — you don't need to touch the Connect → Configuration screen.
					</p>
				</div>
			)}

			<div className="grid grid-cols-2 gap-3">
				<div>
					<Label
						htmlFor="pd-client-id"
						className="text-muted-foreground mb-1 block text-xs"
					>
						Client ID
					</Label>
					<Input
						id="pd-client-id"
						value={form.client_id}
						onChange={set('client_id')}
						placeholder={existing ? '(unchanged)' : 'AbCdEfGhIjKlMnOpQrStUvWxYz012345'}
					/>
				</div>
				<div>
					<Label
						htmlFor="pd-client-secret"
						className="text-muted-foreground mb-1 block text-xs"
					>
						Client Secret
					</Label>
					<Input
						id="pd-client-secret"
						type="password"
						value={form.client_secret}
						onChange={set('client_secret')}
						placeholder={
							existing
								? '(unchanged)'
								: 'abc-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789-de-fghij'
						}
					/>
				</div>
				<div className="col-span-2">
					<Label
						htmlFor="pd-project-id"
						className="text-muted-foreground mb-1 block text-xs"
					>
						Project ID
					</Label>
					<Input
						id="pd-project-id"
						value={form.project_id}
						onChange={set('project_id')}
						placeholder={existing ? '(unchanged)' : 'proj_AbCdEfGhIjKlMnOpQrStUvWxYz01'}
					/>
				</div>
				<div>
					<Label
						htmlFor="pd-environment"
						className="text-muted-foreground mb-1 block text-xs"
					>
						Environment
					</Label>
					<Input
						id="pd-environment"
						value={form.environment}
						onChange={set('environment')}
						placeholder="development"
					/>
				</div>
				<div>
					<Label
						htmlFor="pd-ext-user"
						className="text-muted-foreground mb-1 block text-xs"
					>
						External User ID
					</Label>
					<Input
						id="pd-ext-user"
						value={form.default_external_user_id}
						onChange={set('default_external_user_id')}
						placeholder="default"
					/>
				</div>
			</div>

			{saveMutation.isError && (
				<p role="alert" className="text-danger text-xs">
					{(saveMutation.error as Error).message}
				</p>
			)}
			{deleteMutation.isError && (
				<p role="alert" className="text-danger text-xs">
					Failed to remove: {(deleteMutation.error as Error).message}
				</p>
			)}

			<div className="flex items-center justify-between gap-2">
				<div className="flex items-center gap-2">
					<Button
						onClick={() => saveMutation.mutate()}
						loading={saveMutation.isPending}
						disabled={!canSubmit || deleteMutation.isPending}
					>
						{isNew ? 'Enable Pipedream OAuth' : 'Save changes'}
					</Button>
					<Button
						variant="ghost"
						onClick={onClose}
						disabled={saveMutation.isPending || deleteMutation.isPending}
					>
						Cancel
					</Button>
				</div>
				{existing && (
					<div className="flex items-center gap-2">
						{confirmDelete ? (
							<>
								<span className="text-muted-foreground text-xs">
									Remove Pipedream and all OAuth credentials?
								</span>
								<Button
									variant="danger"
									size="sm"
									loading={deleteMutation.isPending}
									onClick={() => deleteMutation.mutate()}
								>
									Yes, remove
								</Button>
								{!deleteMutation.isPending && (
									<Button
										variant="ghost"
										size="sm"
										onClick={() => setConfirmDelete(false)}
									>
										Cancel
									</Button>
								)}
							</>
						) : (
							<Button
								variant="danger"
								size="sm"
								onClick={() => setConfirmDelete(true)}
								disabled={saveMutation.isPending}
							>
								<Trash2 className="h-4 w-4" /> Remove Pipedream
							</Button>
						)}
					</div>
				)}
			</div>
		</div>
	);
}
