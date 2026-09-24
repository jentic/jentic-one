/**
 * Edit dialog for an existing OAuth app registration — labels, endpoints,
 * default scopes, active flag. `client_id` is immutable server-side and
 * `client_secret` rotates via the dedicated action, so neither appears here.
 *
 * Registration *creation* lives on the credentials page (the "Available to
 * everyone in the organization" toggle on OAuth2 creates) — this dialog is
 * only for lifecycle changes on registrations that already exist.
 */
import { useEffect, useState } from 'react';
import { Button, Checkbox, CopyButton, Dialog, ErrorAlert, Input, Label, toast } from '@/shared/ui';
import {
	useUpdateOAuthAppRegistration,
	usePlatformRedirectUri,
	type OAuthAppRegistration,
	type OAuthAppRegistrationFlowKind,
} from '@/modules/oauth-app-registrations/api/hooks';

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
	is_active: boolean;
}

const emptyEditDraft: EditDraft = {
	name: '',
	authorize_url: '',
	token_url: '',
	authorization_endpoint: '',
	token_endpoint: '',
	default_scopes: [],
	is_active: true,
};

function draftFromRegistration(reg: OAuthAppRegistration): EditDraft {
	return {
		name: reg.name,
		authorize_url: reg.authorize_url ?? '',
		token_url: reg.token_url ?? '',
		authorization_endpoint: reg.authorization_endpoint ?? '',
		token_endpoint: reg.token_endpoint ?? '',
		default_scopes: reg.default_scopes ?? [],
		is_active: reg.is_active,
	};
}

export function OAuthAppRegistrationEditDialog({ open, onClose, registration }: EditDialogProps) {
	const [draft, setDraft] = useState<EditDraft>(emptyEditDraft);
	const [validationError, setValidationError] = useState<string | null>(null);
	const [scopeInput, setScopeInput] = useState('');
	const updateMutation = useUpdateOAuthAppRegistration();
	const { redirectUri } = usePlatformRedirectUri();

	useEffect(() => {
		if (open && registration) {
			setDraft(draftFromRegistration(registration));
			setValidationError(null);
			setScopeInput('');
		}
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
					is_active: draft.is_active,
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
			toast({ title: 'OAuth app registration updated', variant: 'success' });
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
					<Label required>Display name</Label>
					<Input
						value={draft.name}
						onChange={(e): void => patch({ name: e.target.value })}
						placeholder="MyOrg GitHub app"
					/>
				</div>

				<div className="space-y-1.5">
					<Label>API vendor</Label>
					<Input value={registration.api_vendor} disabled readOnly />
					<p className="text-muted-foreground text-xs">Immutable after creation.</p>
				</div>

				<div className="space-y-1.5">
					<Label>Client ID</Label>
					<Input value={registration.client_id} disabled readOnly />
					<p className="text-muted-foreground text-xs">Immutable after creation.</p>
				</div>

				{isAuthCode && redirectUri && (
					<div className="space-y-1.5">
						<Label>Callback URL</Label>
						<div className="flex gap-1.5">
							<Input
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
							<Label>Authorize URL</Label>
							<Input
								type="url"
								value={draft.authorize_url}
								onChange={(e): void => patch({ authorize_url: e.target.value })}
							/>
						</div>
						<div className="space-y-1.5">
							<Label>Token URL</Label>
							<Input
								type="url"
								value={draft.token_url}
								onChange={(e): void => patch({ token_url: e.target.value })}
							/>
						</div>
					</>
				) : (
					<>
						<div className="space-y-1.5">
							<Label>Device authorization endpoint</Label>
							<Input
								type="url"
								value={draft.authorization_endpoint}
								onChange={(e): void =>
									patch({ authorization_endpoint: e.target.value })
								}
							/>
						</div>
						<div className="space-y-1.5">
							<Label>Token endpoint</Label>
							<Input
								type="url"
								value={draft.token_endpoint}
								onChange={(e): void => patch({ token_endpoint: e.target.value })}
							/>
						</div>
					</>
				)}

				<div className="space-y-2">
					<Label>Default scopes</Label>
					<div className="flex gap-1.5">
						<Input
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
								<button
									key={scope}
									type="button"
									onClick={(): void => removeScope(scope)}
									className="border-border hover:bg-muted rounded-md border px-2 py-0.5 font-mono text-xs"
									aria-label={`Remove scope ${scope}`}
								>
									{scope} ×
								</button>
							))}
						</div>
					)}
				</div>

				<div className="border-border flex items-center gap-3 border-t pt-3">
					<Checkbox
						id="oar-edit-is-active"
						checked={draft.is_active}
						onChange={(checked): void => patch({ is_active: checked })}
					/>
					<label
						htmlFor="oar-edit-is-active"
						className="cursor-pointer text-sm select-none"
					>
						Active — allow users to SSO through this registration
					</label>
				</div>
			</form>
		</Dialog>
	);
}
