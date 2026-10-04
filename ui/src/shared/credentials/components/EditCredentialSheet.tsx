import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { ExternalLink, Loader2, X } from 'lucide-react';
import {
	Button,
	ErrorAlert,
	Input,
	Label,
	LoadingState,
	SheetBody,
	SheetFooter,
	SheetHeader,
	SheetPrimitive,
	toast,
} from '@/shared/ui';
import {
	CredentialType,
	credentialDetails,
	formatApiReference,
	useAllCredentials,
	useConnectCredential,
	useCredential,
	useProviders,
	useUpdateCredential,
	type CredentialKeyLocation,
} from '@/shared/credentials/api';
import { CredentialTypeBadge } from '@/shared/credentials/components/CredentialTypeBadge';
import {
	CredentialTypeFields,
	EMPTY_FORM,
	type CredentialFormState,
} from '@/shared/credentials/components/CredentialTypeFields';
import { buildUpdateBody, validateUpdate } from '@/shared/credentials/lib/formBody';
import { assignVendorUrl, isHttpsVendorUrl } from '@/shared/credentials/lib/safe-navigation';
import { BoundAgentsSection } from '@/shared/credentials/components/BoundAgentsSection';
import { credentialNameClash } from '@/shared/credentials/lib/credentialIdentity';
import { CredentialNameClashNote } from '@/shared/credentials/components/CredentialNameClashNote';

interface EditCredentialSheetProps {
	credentialId: string | null;
	open: boolean;
	onClose: () => void;
	onAfterClose?: () => void;
	/**
	 * An in-app link inside the sheet was followed in this tab. Hosts stacked
	 * over the link's destination pass a dismissal here (see
	 * `BoundAgentsSection`); hosts on a route of their own unmount anyway and
	 * pass nothing.
	 */
	onNavigateAway?: () => void;
}

/**
 * Right-side slide-over for editing an existing credential. Metadata (name)
 * and the secret can be updated; secrets are write-only — blank means "keep
 * current". OAuth credentials also expose the connect CTA here, since the
 * redirect flow belongs with the credential it authorizes.
 */
export function EditCredentialSheet({
	credentialId,
	open,
	onClose,
	onAfterClose,
	onNavigateAway,
}: EditCredentialSheetProps) {
	const headingId = 'edit-credential-sheet-title';
	const nameId = useId();
	const closeButtonRef = useRef<HTMLButtonElement | null>(null);

	const { data: cred, isLoading } = useCredential(credentialId ?? undefined);
	const updateMutation = useUpdateCredential(credentialId ?? '');
	const connectMutation = useConnectCredential(credentialId ?? '');
	const providersQuery = useProviders();
	const credentialsSource = useAllCredentials({ enabled: open });

	const [state, setState] = useState<CredentialFormState>(EMPTY_FORM);
	const [errors, setErrors] = useState<Partial<Record<keyof CredentialFormState, string>>>({});
	/**
	 * Snapshot of the form as first seeded from the loaded credential. We diff
	 * the live `state` against it to decide whether anything actually changed —
	 * the Save button stays disabled until it does. Secrets seed blank, so
	 * typing any secret value naturally registers as a change.
	 */
	const [initialState, setInitialState] = useState<CredentialFormState>(EMPTY_FORM);

	const originalName = cred?.name ?? '';

	// A name a sibling for the same API holds — including the one it was saved
	// with, so opening a duplicate says so. A warning only; the save still goes.
	const nameClash = useMemo(
		() =>
			cred
				? credentialNameClash(
						credentialsSource.items,
						{ vendor: cred.api.vendor ?? '', name: cred.api.name ?? '' },
						state.name,
						cred.credential_id,
					)
				: null,
		[cred, credentialsSource.items, state.name],
	);
	// sigv4: does the stored credential currently carry a session token? Drives
	// the "Clear session token" affordance in the edit form.
	const hasStoredSessionToken = useMemo(
		() => Boolean(cred && credentialDetails(cred).has_session_token),
		[cred],
	);

	// Prefill non-secret fields whenever a (different) credential loads.
	useEffect(() => {
		if (!cred) return;
		const details = credentialDetails(cred);
		const seeded: CredentialFormState = {
			...EMPTY_FORM,
			name: cred.name,
			provider: cred.provider ?? '',
			apiVendor: cred.api.vendor ?? '',
			apiName: cred.api.name ?? '',
			apiVersion: cred.api.version ?? '',
			fieldName: typeof details.field_name === 'string' ? details.field_name : '',
			location: (details.location as CredentialKeyLocation) === 'query' ? 'query' : 'header',
			// sigv4: the access key id / region / service are non-secret and come
			// back on the redacted details, so surface them in the edit form.
			accessKeyId: typeof details.access_key_id === 'string' ? details.access_key_id : '',
			awsRegion: typeof details.aws_region === 'string' ? details.aws_region : '',
			awsService: typeof details.aws_service === 'string' ? details.aws_service : '',
			// oauth2: scopes are non-secret; the seeded value is also the baseline
			// `buildUpdateBody` diffs against, so an untouched field sends no scopes.
			scopes: Array.isArray(details.scopes) ? details.scopes.join(' ') : '',
			serverVars: cred.server_variables ?? {},
		};
		setState(seeded);
		setInitialState(seeded);
		setErrors({});
	}, [cred]);

	useEffect(() => {
		if (open) closeButtonRef.current?.focus();
	}, [open, credentialId]);

	const handleSubmit = (e: React.FormEvent): void => {
		e.preventDefault();
		if (!cred || !credentialId || !dirty) return;
		const validation = validateUpdate(cred.type, state);
		if (Object.keys(validation).length > 0) {
			setErrors(validation);
			return;
		}
		setErrors({});
		updateMutation.mutate(
			buildUpdateBody(cred.type, state, originalName, initialState.scopes),
			{
				onSuccess: () => {
					toast({ title: 'Credential updated', variant: 'success' });
					onClose();
				},
			},
		);
	};

	const handleConnect = (): void => {
		connectMutation.mutate(
			{},
			{
				onSuccess: (challenge) => {
					if (challenge.kind === 'authorization_code') {
						// ``authorize_url`` is vendor-supplied; only follow https
						// (same guard as ``runConnectFlow``).
						if (!isHttpsVendorUrl(challenge.authorize_url)) {
							toast({
								title: 'The provider returned an unsafe sign-in URL',
								variant: 'error',
							});
							return;
						}
						assignVendorUrl(challenge.authorize_url);
						return;
					}
					// device_code challenges are handled by the credential row's
					// Connect action (which mounts the human-step dialog).
					toast({
						title: 'Use Connect on the credential row for this sign-in.',
						variant: 'error',
					});
				},
				onError: () => {
					toast({ title: 'Could not start the OAuth flow', variant: 'error' });
				},
			},
		);
	};

	const apiLabel = useMemo(() => (cred ? formatApiReference(cred.api) : ''), [cred]);

	// Has the user changed anything worth saving? Compare every editable field
	// against the seeded snapshot; `serverVars` isn't edited here so it stays
	// equal. Keeps Save disabled (and submits no-op'd) until there's a change.
	const dirty = useMemo(() => !formStatesEqual(state, initialState), [state, initialState]);

	return (
		<SheetPrimitive
			open={open}
			onClose={onClose}
			onAfterClose={onAfterClose}
			side="right"
			ariaLabelledBy={headingId}
			initialFocus={closeButtonRef}
		>
			<form onSubmit={handleSubmit} className="flex h-full flex-col">
				<SheetHeader className="justify-between">
					<div className="min-w-0">
						<h2
							id={headingId}
							className="font-heading text-foreground-name text-lg leading-tight font-semibold"
						>
							Edit credential
						</h2>
						{cred?.name && (
							<p className="text-foreground-sub mt-1 truncate text-xs">{cred.name}</p>
						)}
					</div>
					<Button
						ref={closeButtonRef}
						variant="ghost"
						size="icon"
						aria-label="Close"
						onClick={onClose}
						className="-mt-1 -mr-1.5 shrink-0"
					>
						<X className="h-4 w-4" />
					</Button>
				</SheetHeader>

				<SheetBody className="space-y-5">
					{credentialId && isLoading && (
						<LoadingState
							message="Loading credential…"
							icon={<Loader2 className="h-5 w-5 animate-spin" />}
						/>
					)}

					{cred && (
						<>
							<div className="bg-surface-inset flex items-center justify-between gap-3 rounded-lg px-3 py-2">
								<div className="min-w-0">
									<p className="text-foreground-sub font-mono text-xs">
										{apiLabel}
									</p>
								</div>
								<CredentialTypeBadge credential={cred} />
							</div>

							<div className="space-y-1.5">
								<Label htmlFor={nameId} required>
									Name
								</Label>
								<Input
									id={nameId}
									value={state.name}
									onChange={(e): void =>
										setState((s) => ({ ...s, name: e.target.value }))
									}
									error={errors.name}
									aria-describedby={nameClash ? `${nameId}-clash` : undefined}
								/>
								{nameClash && (
									<CredentialNameClashNote
										id={`${nameId}-clash`}
										{...nameClash}
										onUseSuggestion={(name): void =>
											setState((s) => ({ ...s, name }))
										}
									/>
								)}
							</div>

							<div className="space-y-4">
								<CredentialTypeFields
									type={cred.type}
									state={state}
									onChange={(p): void => setState((s) => ({ ...s, ...p }))}
									errors={errors}
									mode="edit"
									providers={providersQuery.data?.providers}
									hasStoredSessionToken={hasStoredSessionToken}
								/>
							</div>

							{cred.type === CredentialType.OAUTH2 && (
								<div className="bg-surface-inset space-y-2 rounded-lg p-3">
									<p className="text-foreground-name text-sm font-semibold">
										OAuth connection
									</p>
									<p className="text-muted-foreground text-xs">
										Authorize this credential with the provider to obtain
										tokens.
									</p>
									<Button
										variant="tonal"
										size="xs"
										onClick={handleConnect}
										loading={connectMutation.isPending}
									>
										<ExternalLink className="h-3.5 w-3.5" />
										Connect
									</Button>
								</div>
							)}

							{/* Direct agent bindings (theme 5 phase 5a) — read-only
							    roster; management lives on the flat Agents surface
							    (each agent's API tiles + access sidebar). */}
							<BoundAgentsSection
								credentialId={cred.credential_id}
								open={open}
								onNavigateAway={onNavigateAway}
							/>

							{updateMutation.isError && (
								<ErrorAlert message={updateMutation.error} />
							)}
						</>
					)}
				</SheetBody>

				<SheetFooter>
					<Button variant="ghost" onClick={onClose} disabled={updateMutation.isPending}>
						Cancel
					</Button>
					<Button
						type="submit"
						variant="primary"
						loading={updateMutation.isPending}
						disabled={!cred || !dirty}
						title={!dirty ? 'No changes to save' : undefined}
					>
						Save changes
					</Button>
				</SheetFooter>
			</form>
		</SheetPrimitive>
	);
}

/**
 * Structural equality for two form snapshots — drives the edit sheet's
 * dirty-tracking. Compares every *editable* scalar field plus a shallow compare
 * of the `serverVars` record. `fieldName`/`location` are omitted: they are
 * immutable after create (#589), rendered read-only in edit mode, so they can
 * never legitimately dirty the form.
 */
function formStatesEqual(a: CredentialFormState, b: CredentialFormState): boolean {
	const keys: (keyof CredentialFormState)[] = [
		'name',
		'provider',
		'apiVendor',
		'apiName',
		'apiVersion',
		'token',
		'key',
		'username',
		'password',
		'clientId',
		'clientSecret',
		'tokenUrl',
		'authorizeUrl',
		'scopes',
		// sigv4: access key id / region / service are editable, plus the secret,
		// session token, and the clear-session-token flag. Omitting these left the
		// Save button disabled after a sigv4-only edit.
		'accessKeyId',
		'secretAccessKey',
		'sessionToken',
		'clearSessionToken',
		'awsRegion',
		'awsService',
	];
	for (const k of keys) {
		if (a[k] !== b[k]) return false;
	}
	const av = a.serverVars;
	const bv = b.serverVars;
	const ak = Object.keys(av);
	if (ak.length !== Object.keys(bv).length) return false;
	return ak.every((k) => av[k] === bv[k]);
}
