import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { ExternalLink, Loader2, X } from 'lucide-react';
import { Button, ErrorAlert, Input, Label, LoadingState, SheetPrimitive, toast } from '@/shared/ui';
import {
	CredentialType,
	credentialDetails,
	formatApiReference,
	useConnectCredential,
	useCredential,
	useProviders,
	useUpdateCredential,
	type CredentialKeyLocation,
} from '@/modules/credentials/api';
import { CredentialTypeBadge } from '@/modules/credentials/components/CredentialTypeBadge';
import {
	CredentialTypeFields,
	EMPTY_FORM,
	type CredentialFormState,
} from '@/modules/credentials/components/CredentialTypeFields';
import { buildUpdateBody } from '@/modules/credentials/lib/formBody';

interface EditCredentialSheetProps {
	credentialId: string | null;
	open: boolean;
	onClose: () => void;
	onAfterClose?: () => void;
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
}: EditCredentialSheetProps) {
	const headingId = 'edit-credential-sheet-title';
	const nameId = useId();
	const closeButtonRef = useRef<HTMLButtonElement | null>(null);

	const { data: cred, isLoading } = useCredential(credentialId ?? undefined);
	const updateMutation = useUpdateCredential(credentialId ?? '');
	const connectMutation = useConnectCredential(credentialId ?? '');
	const providersQuery = useProviders();

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
		updateMutation.mutate(buildUpdateBody(cred.type, state, originalName), {
			onSuccess: () => {
				toast({ title: 'Credential updated', variant: 'success' });
				onClose();
			},
		});
	};

	const handleConnect = (): void => {
		connectMutation.mutate(
			{},
			{
				onSuccess: (challenge) => {
					window.location.assign(challenge.authorize_url);
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
				<header className="border-border flex items-center justify-between gap-2 border-b px-5 py-3">
					<div className="min-w-0">
						<h2 id={headingId} className="text-foreground text-base font-semibold">
							Edit credential
						</h2>
						{cred?.name && (
							<p className="text-muted-foreground truncate text-xs">{cred.name}</p>
						)}
					</div>
					<Button
						ref={closeButtonRef}
						variant="ghost"
						size="sm"
						aria-label="Close"
						onClick={onClose}
						className="text-muted-foreground hover:text-foreground"
					>
						<X className="h-4 w-4" />
					</Button>
				</header>

				<div className="flex-1 space-y-5 overflow-y-auto px-5 py-4">
					{credentialId && isLoading && (
						<LoadingState
							message="Loading credential…"
							icon={<Loader2 className="h-5 w-5 animate-spin" />}
						/>
					)}

					{cred && (
						<>
							<div className="bg-muted/40 border-border flex items-center justify-between gap-3 rounded-lg border px-3 py-2">
								<div className="min-w-0">
									<p className="text-muted-foreground font-mono text-xs">
										{apiLabel}
									</p>
								</div>
								<CredentialTypeBadge type={cred.type} />
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
								/>
							</div>

							<div className="space-y-4">
								<CredentialTypeFields
									type={cred.type}
									state={state}
									onChange={(p): void => setState((s) => ({ ...s, ...p }))}
									errors={errors}
									mode="edit"
									providers={providersQuery.data?.providers}
								/>
							</div>

							{cred.type === CredentialType.OAUTH2 && (
								<div className="border-border space-y-2 rounded-lg border border-dashed p-3">
									<p className="text-foreground text-sm font-medium">
										OAuth connection
									</p>
									<p className="text-muted-foreground text-xs">
										Authorize this credential with the provider to obtain
										tokens.
									</p>
									<Button
										variant="outline"
										size="sm"
										onClick={handleConnect}
										loading={connectMutation.isPending}
									>
										<ExternalLink className="h-4 w-4" />
										Connect
									</Button>
								</div>
							)}

							{updateMutation.isError && (
								<ErrorAlert message={updateMutation.error} />
							)}
						</>
					)}
				</div>

				<footer className="border-border flex shrink-0 items-center justify-end gap-2 border-t px-5 py-3">
					<Button
						variant="secondary"
						onClick={onClose}
						disabled={updateMutation.isPending}
					>
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
				</footer>
			</form>
		</SheetPrimitive>
	);
}

/**
 * Structural equality for two form snapshots — drives the edit sheet's
 * dirty-tracking. Compares every scalar field plus a shallow compare of the
 * `serverVars` record (untouched in edit, but compared defensively).
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
		'fieldName',
		'location',
		'username',
		'password',
		'clientId',
		'clientSecret',
		'tokenUrl',
		'authorizeUrl',
		'scopes',
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
