/**
 * Edit dialog for an existing OAuth app registration — labels, endpoints,
 * default scopes. `client_id` is immutable server-side and `client_secret`
 * rotates via the dedicated action, so neither appears here. Activation is the
 * row's action (with its own confirm): a kept draft would otherwise carry a
 * stale active flag and silently re-enable an app deactivated since.
 *
 * Registration *creation* runs through the credential create flow in
 * ``registerSharedApp`` mode — this dialog is only for lifecycle changes on
 * registrations that already exist.
 *
 * Draft lifecycle follows dialog-state-lifecycle: the draft seeds when the
 * target registration changes (not on every open), survives Esc / Cancel,
 * and resets only after a successful save.
 */
import { useEffect, useId, useRef, useState } from 'react';
import { X } from 'lucide-react';
import { Badge, Button, CopyButton, Dialog, ErrorAlert, Input, Label, toast } from '@/shared/ui';
import {
	useUpdateOAuthAppRegistration,
	usePlatformRedirectUri,
	type OAuthAppRegistration,
	type OAuthAppRegistrationFlowKind,
} from '@/shared/credentials/oauth-app-registrations/api/hooks';

interface EditDialogProps {
	open: boolean;
	onClose: () => void;
	registration: OAuthAppRegistration | null;
}

interface EditDraft {
	name: string;
	authorize_url: string;
	token_url: string;
	authorization_endpoint: string;
	token_endpoint: string;
	default_scopes: string[];
}

const emptyEditDraft: EditDraft = {
	name: '',
	authorize_url: '',
	token_url: '',
	authorization_endpoint: '',
	token_endpoint: '',
	default_scopes: [],
};

function draftFromRegistration(reg: OAuthAppRegistration): EditDraft {
	return {
		name: reg.name,
		authorize_url: reg.authorize_url ?? '',
		token_url: reg.token_url ?? '',
		authorization_endpoint: reg.authorization_endpoint ?? '',
		token_endpoint: reg.token_endpoint ?? '',
		default_scopes: reg.default_scopes ?? [],
	};
}

export function OAuthAppRegistrationEditDialog({ open, onClose, registration }: EditDialogProps) {
	const [draft, setDraft] = useState<EditDraft>(emptyEditDraft);
	const [validationError, setValidationError] = useState<string | null>(null);
	const [scopeInput, setScopeInput] = useState('');
	const updateMutation = useUpdateOAuthAppRegistration();
	const { redirectUri } = usePlatformRedirectUri();
	const fieldId = useId();

	// (a) Transient flags clear on every (re)open.
	useEffect(() => {
		if (!open) return;
		setValidationError(null);
	}, [open]);

	// (b) Seed only when the target changes, so re-opening the same
	// registration after an Esc keeps the admin's edits. ``seededId`` is
	// cleared after a successful save so the next open re-reads the server.
	const seededId = useRef<string | null>(null);
	useEffect(() => {
		if (!open || !registration || seededId.current === registration.id) return;
		seededId.current = registration.id;
		setDraft(draftFromRegistration(registration));
		setScopeInput('');
	}, [open, registration]);

	if (!registration) return null;

	const flowKind = registration.flow_kind;
	const isAuthCode = flowKind === ('authorization_code' as OAuthAppRegistrationFlowKind);

	const patch = (p: Partial<EditDraft>): void => {
		setDraft((d) => ({ ...d, ...p }));
	};

	const addScope = (): void => {
		const trimmed = scopeInput.trim();
		if (!trimmed || draft.default_scopes.includes(trimmed)) {
			setScopeInput('');
			return;
		}
		patch({ default_scopes: [...draft.default_scopes, trimmed] });
		setScopeInput('');
	};

	const removeScope = (scope: string): void => {
		patch({ default_scopes: draft.default_scopes.filter((s) => s !== scope) });
	};

	const handleSubmit = async (e: React.FormEvent): Promise<void> => {
		e.preventDefault();
		if (!draft.name.trim()) {
			setValidationError('Name is required.');
			return;
		}

		try {
			await updateMutation.mutateAsync({
				id: registration.id,
				input: {
					name: draft.name.trim(),
					default_scopes: draft.default_scopes.length > 0 ? draft.default_scopes : null,
					...(isAuthCode
						? {
								authorize_url: draft.authorize_url.trim() || null,
								token_url: draft.token_url.trim() || null,
							}
						: {
								authorization_endpoint: draft.authorization_endpoint.trim() || null,
								token_endpoint: draft.token_endpoint.trim() || null,
							}),
				},
			});
			toast({ title: 'Shared app updated', variant: 'success' });
			// (c) Hard reset only on success.
			seededId.current = null;
			onClose();
		} catch (err) {
			toast({
				title: 'Failed to update registration',
				description: err instanceof Error ? err.message : undefined,
				variant: 'error',
			});
		}
	};

	return (
		<Dialog
			open={open}
			onClose={onClose}
			title={`Edit ${registration.name}`}
			subtitle={isAuthCode ? 'Authorization code flow' : 'Device authorization flow'}
			size="lg"
			footer={
				<>
					<Button
						variant="secondary"
						onClick={onClose}
						disabled={updateMutation.isPending}
					>
						Cancel
					</Button>
					<Button type="submit" form="oar-edit-form" loading={updateMutation.isPending}>
						Save
					</Button>
				</>
			}
		>
			<form
				id="oar-edit-form"
				onSubmit={(e): void => void handleSubmit(e)}
				className="space-y-4"
			>
				{validationError && <ErrorAlert message={validationError} />}

				<div className="space-y-1.5">
					<Label htmlFor={`${fieldId}-name`} required>
						Display name
					</Label>
					<Input
						id={`${fieldId}-name`}
						value={draft.name}
						onChange={(e): void => patch({ name: e.target.value })}
						placeholder="MyOrg GitHub app"
					/>
				</div>

				<div className="space-y-1.5">
					<Label htmlFor={`${fieldId}-vendor`}>API vendor</Label>
					<Input
						id={`${fieldId}-vendor`}
						value={registration.api_vendor}
						disabled
						readOnly
					/>
					<p className="text-muted-foreground text-xs">Immutable after creation.</p>
				</div>

				<div className="space-y-1.5">
					<Label htmlFor={`${fieldId}-client`}>Client ID</Label>
					<Input
						id={`${fieldId}-client`}
						value={registration.client_id}
						disabled
						readOnly
					/>
					<p className="text-muted-foreground text-xs">Immutable after creation.</p>
				</div>

				{isAuthCode && redirectUri && (
					<div className="space-y-1.5">
						<Label htmlFor={`${fieldId}-callback`}>Callback URL</Label>
						<div className="flex gap-1.5">
							<Input
								id={`${fieldId}-callback`}
								value={redirectUri}
								readOnly
								className="flex-1 font-mono text-xs"
							/>
							<CopyButton
								value={redirectUri}
								ariaLabel="Copy callback URL"
								toastMessage="Callback URL copied"
							/>
						</div>
						<p className="text-muted-foreground text-xs">
							Add this URL to your OAuth app&apos;s allowed redirect URIs.
						</p>
					</div>
				)}

				{isAuthCode ? (
					<>
						<div className="space-y-1.5">
							<Label htmlFor={`${fieldId}-authorize`}>Authorize URL</Label>
							<Input
								id={`${fieldId}-authorize`}
								type="url"
								value={draft.authorize_url}
								onChange={(e): void => patch({ authorize_url: e.target.value })}
							/>
						</div>
						<div className="space-y-1.5">
							<Label htmlFor={`${fieldId}-token`}>Token URL</Label>
							<Input
								id={`${fieldId}-token`}
								type="url"
								value={draft.token_url}
								onChange={(e): void => patch({ token_url: e.target.value })}
							/>
						</div>
					</>
				) : (
					<>
						<div className="space-y-1.5">
							<Label htmlFor={`${fieldId}-device-auth`}>
								Device authorization endpoint
							</Label>
							<Input
								id={`${fieldId}-device-auth`}
								type="url"
								value={draft.authorization_endpoint}
								onChange={(e): void =>
									patch({ authorization_endpoint: e.target.value })
								}
							/>
						</div>
						<div className="space-y-1.5">
							<Label htmlFor={`${fieldId}-device-token`}>Token endpoint</Label>
							<Input
								id={`${fieldId}-device-token`}
								type="url"
								value={draft.token_endpoint}
								onChange={(e): void => patch({ token_endpoint: e.target.value })}
							/>
						</div>
					</>
				)}

				<div className="space-y-2">
					<Label htmlFor={`${fieldId}-scopes`}>Default scopes</Label>
					<div className="flex gap-1.5">
						<Input
							id={`${fieldId}-scopes`}
							value={scopeInput}
							onChange={(e): void => setScopeInput(e.target.value)}
							onKeyDown={(e): void => {
								if (e.key === 'Enter') {
									e.preventDefault();
									addScope();
								}
							}}
							placeholder="Add a scope and press Enter"
							className="flex-1"
						/>
						<Button
							type="button"
							variant="secondary"
							onClick={addScope}
							disabled={!scopeInput.trim()}
						>
							Add
						</Button>
					</div>
					{draft.default_scopes.length > 0 && (
						<div className="flex flex-wrap gap-1.5">
							{draft.default_scopes.map((scope) => (
								<Badge key={scope} className="pr-1">
									{scope}
									<Button
										type="button"
										variant="ghost"
										size="icon"
										className="text-primary h-4 w-4 p-0"
										onClick={(): void => removeScope(scope)}
										aria-label={`Remove scope ${scope}`}
									>
										<X className="h-3 w-3" aria-hidden="true" />
									</Button>
								</Badge>
							))}
						</div>
					)}
				</div>
			</form>
		</Dialog>
	);
}
