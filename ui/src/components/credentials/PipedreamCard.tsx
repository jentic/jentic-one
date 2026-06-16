import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AnimatePresence, motion } from 'framer-motion';
import { HelpCircle, Link2, Settings, RotateCcw, Trash2 } from 'lucide-react';
import { oauthBrokers, type OAuthBroker } from '@/api/client';
import { AppLink } from '@/components/ui/AppLink';
import { Button } from '@/components/ui/Button';
import { HoverTooltip } from '@/components/ui/HoverTooltip';
import { Input } from '@/components/ui/Input';
import { Label } from '@/components/ui/Label';
import { toast } from '@/components/ui/toastStore';

/**
 * Shared transition for swapping between the card's states (banner ↔ form ↔
 * configure ↔ status). Fade + subtle scale at 200ms/easeOut — the house
 * pattern for inline content swaps (see OverviewTab / ToolkitDetailBody).
 * `MotionConfig reducedMotion="user"` (App.tsx) neutralises this for users
 * who ask for reduced motion, so no per-component guard is needed.
 */
const stateMotion = {
	initial: { opacity: 0, scale: 0.98 },
	animate: { opacity: 1, scale: 1 },
	exit: { opacity: 0, scale: 0.98 },
	transition: { duration: 0.2, ease: 'easeOut' },
} as const;

/**
 * Pull the human-readable reason out of a failed sync probe. FastAPI puts
 * the message in `detail` (e.g. "Pipedream rejected these credentials…"),
 * which `ApiError.message` doesn't surface, so read it off the parsed body.
 */
function probeErrorDetail(err: unknown): string | null {
	const data = (err as { data?: { detail?: unknown } } | null)?.data;
	return typeof data?.detail === 'string' ? data.detail : null;
}

/**
 * "Pipedream OAuth" status + setup.
 *
 * In the old layout this lived as a free-floating one-line note above the
 * credentials list, with two completely different forms hidden behind it.
 * Now it's:
 *
 *   - one **invitation banner** when Pipedream isn't configured (card-shaped,
 *     icon + pitch + "Enable OAuth" CTA, so it reads as part of the same card
 *     family as the credential rows below it),
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

	const { data: brokersRaw, isLoading: brokersLoading } = useQuery({
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

	// One key per visual state so `AnimatePresence mode="wait"` plays the
	// exit of the old state before the new one enters. While the brokers
	// query is in-flight we show a skeleton rather than flashing the
	// "not configured" banner for the legitimate already-configured case.
	const stateKey = showForm
		? 'form'
		: showConfigure && pipedream
			? 'configure'
			: brokersLoading && !pipedream
				? 'loading'
				: !pipedream
					? 'banner'
					: 'status';

	return (
		<AnimatePresence mode="wait" initial={false}>
			<motion.div key={stateKey} {...stateMotion}>
				{stateKey === 'form' && (
					<PipedreamForm
						existing={pipedream ?? undefined}
						onClose={() => setShowForm(false)}
						onDeleted={() => setShowForm(false)}
					/>
				)}

				{stateKey === 'loading' && (
					<div
						className="border-border/60 bg-card flex items-center gap-3 rounded-xl border p-4"
						aria-hidden="true"
					>
						<div className="bg-muted h-9 w-9 shrink-0 animate-pulse rounded-lg" />
						<div className="min-w-0 flex-1 space-y-2">
							<div className="bg-muted h-3.5 w-40 animate-pulse rounded" />
							<div className="bg-muted h-3 w-28 animate-pulse rounded" />
						</div>
						<div className="bg-muted h-8 w-24 animate-pulse rounded-md" />
					</div>
				)}

				{stateKey === 'configure' && (
					<div className="bg-card border-border rounded-xl border p-5">
						<div className="flex items-center justify-between">
							<div className="flex items-center gap-2.5">
								<div className="bg-primary/10 text-primary flex h-8 w-8 shrink-0 items-center justify-center rounded-lg">
									<Link2 className="h-4 w-4" />
								</div>
								<h2 className="text-foreground text-sm font-semibold">
									Pipedream OAuth
								</h2>
							</div>
							<Button
								variant="ghost"
								size="sm"
								onClick={() => setShowConfigure(false)}
							>
								Close
							</Button>
						</div>

						{/* Flat action rows separated by a divider rather than
						    nested bordered boxes — same information, far less
						    visual weight inside the already-bordered card. */}
						<div className="divide-border/60 border-border/60 mt-4 divide-y rounded-lg border">
							<div className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between sm:gap-4">
								<div className="min-w-0">
									<h3 className="text-foreground text-xs font-semibold">
										Credentials
									</h3>
									<p className="text-muted-foreground mt-0.5 text-xs leading-snug">
										Client ID and secret are configured. Update if you need to
										rotate keys.
									</p>
								</div>
								<Button
									variant="secondary"
									size="sm"
									className="shrink-0 max-sm:w-full"
									onClick={() => {
										setShowConfigure(false);
										setShowForm(true);
									}}
								>
									<Settings className="h-4 w-4" /> Edit
								</Button>
							</div>

							<div className="p-4">
								<div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between sm:gap-4">
									<div className="min-w-0">
										<h3 className="text-foreground text-xs font-semibold">
											Sync all connections
										</h3>
										<p className="text-muted-foreground mt-0.5 text-xs leading-snug">
											Fetch the latest connected accounts from Pipedream to
											clean up defunct credentials or discover connections
											created elsewhere.
										</p>
									</div>
									<Button
										variant="secondary"
										size="sm"
										className="shrink-0 max-sm:w-full"
										onClick={() => syncMutation.mutate()}
										loading={syncMutation.isPending}
									>
										<RotateCcw className="h-4 w-4" /> Resync
									</Button>
								</div>
								{syncMutation.isSuccess && (
									<p className="text-success mt-2 text-xs">
										✓ Sync complete — {syncMutation.data?.accounts_synced ?? 0}{' '}
										accounts refreshed
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
				)}

				{stateKey === 'banner' && (
					<div className="border-border/60 from-primary/[0.04] bg-card flex flex-col gap-4 rounded-xl border bg-gradient-to-br to-transparent p-5 sm:flex-row sm:items-center sm:justify-between">
						<div className="flex items-start gap-3">
							<div className="bg-primary/10 text-primary flex h-10 w-10 shrink-0 items-center justify-center rounded-xl">
								<Link2 className="h-5 w-5" />
							</div>
							<div className="min-w-0">
								<h3 className="font-heading text-foreground text-sm font-semibold">
									Connect OAuth APIs with Pipedream
								</h3>
								<p className="text-muted-foreground mt-0.5 text-xs leading-snug">
									Let agents reach OAuth-protected APIs without you minting tokens
									by hand — connect once and grants are managed and refreshed for
									you.
								</p>
							</div>
						</div>
						<Button
							type="button"
							onClick={() => setShowForm(true)}
							className="shrink-0 sm:self-center"
						>
							Enable OAuth
						</Button>
					</div>
				)}

				{stateKey === 'status' && (
					<div className="border-border/60 bg-card flex flex-col gap-3 rounded-xl border p-4 sm:flex-row sm:items-center">
						<div className="bg-success/15 text-success flex h-9 w-9 shrink-0 items-center justify-center rounded-lg">
							<Link2 className="h-4 w-4" />
						</div>
						<div className="min-w-0 flex-1">
							<p className="text-foreground text-sm font-medium">
								OAuth enabled via Pipedream
							</p>
							<p className="text-muted-foreground text-xs">
								{accounts.length > 0
									? `${accounts.length} account${accounts.length !== 1 ? 's' : ''} connected`
									: 'No accounts connected yet'}
								{lastSynced && (
									<>
										{' '}
										· last synced {new Date(lastSynced * 1000).toLocaleString()}
									</>
								)}
							</p>
						</div>
						<Button
							type="button"
							variant="secondary"
							size="sm"
							className="shrink-0 max-sm:w-full"
							onClick={() => setShowConfigure(true)}
						>
							<Settings className="h-4 w-4" /> Configure
						</Button>
					</div>
				)}
			</motion.div>
		</AnimatePresence>
	);
}

// ── Form (edit/create Pipedream broker config) ────────────────────────────────

/**
 * Where each Pipedream value comes from. Surfaced as a hover/focus tooltip
 * on a `?` icon next to the field label so the long-form steps above can
 * stay terse without leaving users hunting through the Pipedream dashboard.
 * Locations verified against Pipedream's Connect / API-settings docs.
 */
const FIELD_HELP: Record<string, React.ReactNode> = {
	client_id: (
		<>
			In Pipedream, go to <strong>Settings → API</strong> (
			<span className="font-mono">pipedream.com/settings/api</span>) and open your{' '}
			<strong>OAuth Client</strong>. The <strong>Client ID</strong> is shown in the clients
			list.
		</>
	),
	client_secret: (
		<>
			Shown <strong>once</strong> when you click <strong>+ New OAuth Client</strong> under{' '}
			<strong>Settings → API</strong>. If you didn't copy it, use the client's{' '}
			<strong>… → Rotate client secret</strong> to generate a new one.
		</>
	),
	project_id: (
		<>
			Open your project at <span className="font-mono">pipedream.com/projects</span>, click
			the <strong>Settings</strong> tab, and copy the <strong>Project ID</strong> (format:{' '}
			<span className="font-mono">proj_xxx</span>).
		</>
	),
	environment: (
		<>
			Which Pipedream environment to target — <span className="font-mono">production</span> or{' '}
			<span className="font-mono">development</span>. Most setups use{' '}
			<span className="font-mono">production</span>.
		</>
	),
	default_external_user_id: (
		<>
			Pipedream's identifier for the user that owns the connected accounts. Jentic is
			single-tenant, so leave this as <span className="font-mono">default</span>. Changing it
			to a value that doesn't match the one used during the OAuth flow silently routes
			credentials to a Pipedream user the sync never reads.
		</>
	),
};

/**
 * A field label paired with a `?` help icon. The icon opens a
 * `HoverTooltip` (hover + keyboard focus) describing where to find the
 * value in Pipedream. `tabIndex={0}` on the trigger keeps the help
 * reachable without a mouse.
 */
function FieldLabel({ htmlFor, children }: { htmlFor: string; children: React.ReactNode }) {
	const help = FIELD_HELP[htmlFor.replace(/^pd-/, '').replace(/-/g, '_')];
	return (
		<div className="mb-1 flex items-center gap-1">
			<Label htmlFor={htmlFor} className="text-muted-foreground block text-xs">
				{children}
			</Label>
			{help && (
				<HoverTooltip
					content={help}
					side="top"
					triggerClassName="inline-flex"
					className="text-muted-foreground/70 hover:text-foreground cursor-help transition-colors"
				>
					<HelpCircle
						className="h-3.5 w-3.5"
						aria-label={`Where to find ${typeof children === 'string' ? children : 'this value'}`}
					/>
				</HoverTooltip>
			)}
		</div>
	);
}

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

	// Once the broker row exists, a second submit (e.g. retry after a failed
	// connectivity probe) must PATCH rather than POST — re-POSTing would
	// 409 / duplicate. `savedOnce` flips true after the first store succeeds.
	const [savedOnce, setSavedOnce] = useState(false);

	const saveMutation = useMutation({
		// Two steps: (1) store the broker config, (2) probe it with a real
		// sync. The backend stores creds WITHOUT validating them, so without
		// the probe a wrong client secret would still land on a green "OAuth
		// enabled" status and only fail later on a real API call. `sync`
		// exercises the live Pipedream token, so a thrown error here means
		// the credentials are bad — surface that instead of a false success.
		mutationFn: async () => {
			const useUpdate = !!existing || savedOnce;
			if (useUpdate) {
				await oauthBrokers.update('pipedream', {
					client_id: form.client_id || undefined,
					client_secret: form.client_secret || undefined,
					project_id: form.project_id || undefined,
				});
			} else {
				await oauthBrokers.create({
					id: 'pipedream',
					type: 'pipedream',
					config: {
						client_id: form.client_id,
						client_secret: form.client_secret,
						project_id: form.project_id,
						environment: form.environment,
						default_external_user_id: form.default_external_user_id,
					},
				});
			}
			// Broker is now stored — any further retry edits in place.
			setSavedOnce(true);
			queryClient.invalidateQueries({ queryKey: ['oauth-brokers'] });
			// Connectivity probe. Throws if Pipedream rejects the token.
			await oauthBrokers.sync('pipedream', form.default_external_user_id || 'default');
		},
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: ['oauth-broker-accounts'] });
			queryClient.invalidateQueries({ queryKey: ['credentials'] });
			toast({
				title: 'Pipedream connected',
				description: 'Credentials verified — OAuth is ready to use.',
				variant: 'success',
			});
			onClose();
		},
		// On failure we deliberately keep the form open: the broker row may
		// already be stored, so the user can fix the secret and re-submit
		// (which now PATCHes via `savedOnce`) without losing their input.
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
			<div className="flex items-center gap-2.5">
				<div className="bg-primary/10 text-primary flex h-8 w-8 shrink-0 items-center justify-center rounded-lg">
					<Link2 className="h-4 w-4" />
				</div>
				<h2 className="text-foreground text-sm font-semibold">
					{isNew ? 'Enable OAuth with Pipedream' : 'Edit Pipedream configuration'}
				</h2>
			</div>

			{isNew && (
				<div className="bg-muted/40 space-y-2 rounded-lg p-4 text-xs">
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

			<div className="space-y-3">
				<div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
					<div>
						<FieldLabel htmlFor="pd-client-id">Client ID</FieldLabel>
						<Input
							id="pd-client-id"
							value={form.client_id}
							onChange={set('client_id')}
							placeholder={
								existing ? '(unchanged)' : 'AbCdEfGhIjKlMnOpQrStUvWxYz012345'
							}
						/>
					</div>
					<div>
						<FieldLabel htmlFor="pd-client-secret">Client Secret</FieldLabel>
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
					<div className="sm:col-span-2">
						<FieldLabel htmlFor="pd-project-id">Project ID</FieldLabel>
						<Input
							id="pd-project-id"
							value={form.project_id}
							onChange={set('project_id')}
							placeholder={
								existing ? '(unchanged)' : 'proj_AbCdEfGhIjKlMnOpQrStUvWxYz01'
							}
						/>
					</div>
				</div>

				{/* Advanced knobs — most users leave these at the defaults, so
				    keep them visually subordinate to the three values copied
				    from Pipedream above. */}
				<div className="border-border/60 grid grid-cols-1 gap-3 border-t pt-3 sm:grid-cols-2">
					<div>
						<FieldLabel htmlFor="pd-environment">Environment</FieldLabel>
						<Input
							id="pd-environment"
							value={form.environment}
							onChange={set('environment')}
							placeholder="development"
						/>
					</div>
					<div>
						<FieldLabel htmlFor="pd-ext-user">External User ID</FieldLabel>
						<Input
							id="pd-ext-user"
							value={form.default_external_user_id}
							onChange={set('default_external_user_id')}
							placeholder="default"
						/>
					</div>
				</div>
			</div>

			{saveMutation.isError && (
				<p role="alert" className="text-danger text-xs">
					{savedOnce
						? // The connectivity probe failed. Prefer the backend's
							// specific reason (e.g. "Pipedream rejected these
							// credentials…") over a bare status line.
							(probeErrorDetail(saveMutation.error) ??
							"Saved, but couldn't reach Pipedream — double-check the client ID, secret, and project ID, then try again.")
						: `Couldn't save: ${(saveMutation.error as Error).message}`}
				</p>
			)}
			{deleteMutation.isError && (
				<p role="alert" className="text-danger text-xs">
					Failed to remove: {(deleteMutation.error as Error).message}
				</p>
			)}

			<div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between sm:gap-2">
				<div className="flex items-center gap-2">
					<Button
						onClick={() => saveMutation.mutate()}
						loading={saveMutation.isPending}
						disabled={!canSubmit || deleteMutation.isPending}
						className="flex-1 sm:flex-none"
					>
						{saveMutation.isError && savedOnce
							? 'Retry connection'
							: isNew
								? 'Enable Pipedream OAuth'
								: 'Save changes'}
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
					<div className="border-border/60 flex flex-wrap items-center gap-2 border-t pt-3 sm:border-0 sm:pt-0">
						{confirmDelete ? (
							<>
								<span className="text-muted-foreground w-full text-xs sm:w-auto">
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
