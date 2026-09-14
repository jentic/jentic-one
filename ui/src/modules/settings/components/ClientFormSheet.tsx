/**
 * ClientFormSheet — create/edit form for OAuth clients, as a slide-over
 * (library-first: forms prefer `SheetPrimitive` so the roster stays visible).
 *
 * Dialog-state lifecycle: mounted persistently by the section (never
 * `{open && …}`), transient flags cleared on every (re)open, the draft
 * SEEDED only when the target identity changes, and hard-reset only on the
 * successful-commit path — a casual Esc/backdrop dismiss keeps a half-typed
 * draft.
 *
 * Create-only fields (the API accepts them at POST but not PATCH, so edit
 * mode neither shows nor sends them): the consent model (`user` | `agent`,
 * the MCP marker) and the client type (`confidential` | `public` →
 * `token_endpoint_auth_method`).
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { Plus, Trash2, X } from 'lucide-react';
import {
	Badge,
	Button,
	Checkbox,
	ErrorAlert,
	Input,
	Label,
	LoadingState,
	ScopePicker,
	Select,
	SheetPrimitive,
	toast,
} from '@/shared/ui';
import { extractResourceFromScope, type EnhancedScope } from '@/shared/lib/scopes';
import {
	useCreateOAuthClient,
	useUpdateOAuthClient,
	usePermissionCatalogue,
	OAuthClientCreateRequest,
	type OAuthClient,
} from '@/modules/settings/api/hooks';

/** A redirect-URI draft row with a stable key (rows can be added/removed). */
interface UriRow {
	key: number;
	value: string;
}

let nextUriKey = 0;
const makeUriRows = (uris: string[]): UriRow[] =>
	(uris.length > 0 ? uris : ['']).map((value) => ({ key: nextUriKey++, value }));

function RedirectUriList({
	rows,
	onChange,
}: {
	rows: UriRow[];
	onChange: (rows: UriRow[]) => void;
}) {
	return (
		<div className="space-y-2">
			{rows.map((row, index) => (
				<div key={row.key} className="flex items-center gap-2">
					<Input
						value={row.value}
						onChange={(e): void =>
							onChange(
								rows.map((r, i) =>
									i === index ? { ...r, value: e.target.value } : r,
								),
							)
						}
						placeholder="https://example.com/callback"
						aria-label={`Redirect URI ${index + 1}`}
						className="flex-1"
					/>
					<Button
						type="button"
						variant="ghost"
						size="sm"
						onClick={(): void => onChange(rows.filter((_, i) => i !== index))}
						disabled={rows.length <= 1}
						aria-label="Remove URI"
					>
						<Trash2 className="h-4 w-4" />
					</Button>
				</div>
			))}
			<Button
				type="button"
				variant="outline"
				size="sm"
				onClick={(): void => onChange([...rows, { key: nextUriKey++, value: '' }])}
			>
				<Plus className="mr-1 h-3 w-3" />
				Add URI
			</Button>
		</div>
	);
}

/**
 * Allowed-scopes selector: the shared grouped/searchable `ScopePicker` over
 * the platform permission catalogue (replacing the old local chip cloud),
 * PLUS a free-text row for scopes outside the catalogue — OAuth
 * `allowed_scopes` is an open set (a client may be restricted to scopes the
 * catalogue doesn't know), which the shared picker deliberately doesn't
 * model, so the custom-scope affordance stays local to this form.
 */
function AllowedScopesField({
	selected,
	onChange,
}: {
	selected: string[];
	onChange: (scopes: string[]) => void;
}) {
	const [customInput, setCustomInput] = useState('');
	const catalogue = usePermissionCatalogue();
	const entries = useMemo(() => catalogue.data ?? [], [catalogue.data]);
	const catalogueNames = useMemo(() => new Set(entries.map((p) => p.name)), [entries]);
	const scopes: EnhancedScope[] = useMemo(
		() =>
			entries.map((p) => ({
				scope: p.name,
				description: p.description,
				origin: 'platform' as const,
				isRecommended: false,
			})),
		[entries],
	);
	const customScopes = selected.filter((s) => !catalogueNames.has(s));

	const toggle = (scope: string): void => {
		onChange(
			selected.includes(scope) ? selected.filter((s) => s !== scope) : [...selected, scope],
		);
	};
	const selectAll = (group?: string): void => {
		const pool = scopes.filter((s) => !group || extractResourceFromScope(s.scope) === group);
		onChange([...new Set([...selected, ...pool.map((s) => s.scope)])]);
	};
	const deselectAll = (group?: string): void => {
		onChange(
			group
				? selected.filter(
						(s) => !catalogueNames.has(s) || extractResourceFromScope(s) !== group,
					)
				: customScopes,
		);
	};
	const addCustom = (): void => {
		const trimmed = customInput.trim();
		if (trimmed && !selected.includes(trimmed)) {
			onChange([...selected, trimmed]);
			setCustomInput('');
		}
	};

	return (
		<div className="space-y-3">
			{catalogue.isPending ? (
				<LoadingState size="sm" message="Loading permissions…" />
			) : catalogue.error ? (
				<ErrorAlert message={catalogue.error as Error} />
			) : (
				<ScopePicker
					scopes={scopes}
					selectedScopes={selected.filter((s) => catalogueNames.has(s))}
					showRecommended={false}
					onScopeToggle={toggle}
					onSelectAll={selectAll}
					onDeselectAll={deselectAll}
				/>
			)}
			{customScopes.length > 0 && (
				<div className="flex flex-wrap gap-1">
					{customScopes.map((s) => (
						<Badge key={s} variant="default" className="gap-1">
							{s}
							<button
								type="button"
								onClick={(): void => onChange(selected.filter((x) => x !== s))}
								className="hover:text-danger cursor-pointer"
								aria-label={`Remove scope ${s}`}
							>
								<X className="h-3 w-3" />
							</button>
						</Badge>
					))}
				</div>
			)}
			<div className="flex items-center gap-2">
				<Input
					value={customInput}
					onChange={(e): void => setCustomInput(e.target.value)}
					placeholder="custom:scope"
					aria-label="Custom scope"
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

export interface ClientFormSheetProps {
	open: boolean;
	onClose: () => void;
	/** Edit target; null/undefined = create mode. */
	client?: OAuthClient | null;
	/** Create-mode only: the one-time secret of a new confidential client. */
	onSecretRevealed?: (secret: string) => void;
}

export function ClientFormSheet({ open, onClose, client, onSecretRevealed }: ClientFormSheetProps) {
	const isEdit = client != null;

	const [name, setName] = useState('');
	const [description, setDescription] = useState('');
	const [uriRows, setUriRows] = useState<UriRow[]>(() => makeUriRows([]));
	const [requireConsent, setRequireConsent] = useState(true);
	const [restrictScopes, setRestrictScopes] = useState(false);
	const [allowedScopes, setAllowedScopes] = useState<string[]>([]);
	// Create-only knobs (the PATCH API doesn't accept them — never sent on edit).
	const [consentModel, setConsentModel] = useState<'user' | 'agent'>('user');
	const [clientType, setClientType] = useState<'confidential' | 'public'>('confidential');
	const [validationError, setValidationError] = useState<string | null>(null);
	const nameRef = useRef<HTMLInputElement>(null);

	const createMutation = useCreateOAuthClient();
	const updateMutation = useUpdateOAuthClient();
	const isPending = createMutation.isPending || updateMutation.isPending;

	// (a) Transient flags are NOT user input — clear on every (re)open.
	useEffect(() => {
		if (open) setValidationError(null);
	}, [open]);

	// (b) Seed-from-props only when the TARGET identity changed — not on every
	// `open` flip, or re-opening would clobber the user's draft.
	const identity = client?.id ?? null;
	const lastIdentityRef = useRef<string | null | undefined>(undefined);
	useEffect(() => {
		if (lastIdentityRef.current === identity) return;
		lastIdentityRef.current = identity;
		setName(client?.name ?? '');
		setDescription(client?.description ?? '');
		setUriRows(makeUriRows(client?.redirect_uris ?? []));
		setRequireConsent(client?.require_consent ?? true);
		setRestrictScopes(client?.allowed_scopes != null);
		setAllowedScopes(client?.allowed_scopes ?? []);
		setConsentModel(client?.consent_model === 'agent' ? 'agent' : 'user');
		setClientType(client?.token_endpoint_auth_method === 'none' ? 'public' : 'confidential');
	}, [identity, client]);

	// (c) Hard reset of the draft is reserved for the success path.
	const resetDraft = (): void => {
		setName('');
		setDescription('');
		setUriRows(makeUriRows([]));
		setRequireConsent(true);
		setRestrictScopes(false);
		setAllowedScopes([]);
		setConsentModel('user');
		setClientType('confidential');
		setValidationError(null);
	};

	const handleSubmit = async (e: React.FormEvent): Promise<void> => {
		e.preventDefault();
		const uris = uriRows.map((r) => r.value.trim()).filter(Boolean);
		if (!name.trim() || uris.length === 0) {
			setValidationError('Name and at least one redirect URI are required.');
			return;
		}
		setValidationError(null);

		try {
			if (isEdit) {
				// allowed_scopes is TRI-STATE on PATCH (OAuthClientUpdateRequest):
				// null/omitted = NO CHANGE, `['*']` = reset to unrestricted, any
				// other array = restrict to it ([] = OIDC-only). Sending null on
				// uncheck would success-toast a silent no-op, so translate the
				// checkbox against the client's CURRENT restriction instead.
				let scopeUpdate: string[] | undefined;
				if (restrictScopes) {
					scopeUpdate = allowedScopes;
				} else if (client.allowed_scopes != null) {
					scopeUpdate = ['*'];
				}
				await updateMutation.mutateAsync({
					id: client.id,
					input: {
						name: name.trim(),
						description: description.trim() || null,
						redirect_uris: uris,
						require_consent: requireConsent,
						...(scopeUpdate !== undefined ? { allowed_scopes: scopeUpdate } : {}),
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
					allowed_scopes: restrictScopes ? allowedScopes : null,
					consent_model:
						consentModel === 'agent'
							? OAuthClientCreateRequest.consent_model.AGENT
							: OAuthClientCreateRequest.consent_model.USER,
					token_endpoint_auth_method:
						clientType === 'public'
							? OAuthClientCreateRequest.token_endpoint_auth_method.NONE
							: OAuthClientCreateRequest.token_endpoint_auth_method
									.CLIENT_SECRET_BASIC,
				});
				resetDraft();
				onClose();
				if (result.client_secret) {
					onSecretRevealed?.(result.client_secret);
				}
				// The one-time secret has been handed to the owner's reveal
				// dialog — don't let it linger in the mutation cache too
				// (mirrors the rotate path's reset-after-reveal).
				createMutation.reset();
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
		<SheetPrimitive
			open={open}
			onClose={onClose}
			side="right"
			ariaLabel={isEdit ? `Edit ${client.name}` : 'Create OAuth client'}
			initialFocus={nameRef}
			className="flex flex-col"
		>
			<header className="border-border border-b p-5">
				<h2 className="text-foreground text-lg font-semibold">
					{isEdit ? 'Edit OAuth client' : 'Create OAuth client'}
				</h2>
				<p className="text-muted-foreground mt-1 text-sm">
					{isEdit
						? 'Update the client configuration. The client type and consent model are fixed at creation.'
						: 'Register a third-party application that authenticates users via Jentic One.'}
				</p>
			</header>

			<form
				id="oauth-client-form"
				onSubmit={(e): void => void handleSubmit(e)}
				className="flex-1 space-y-4 overflow-y-auto p-5"
			>
				{validationError && <ErrorAlert message={validationError} />}
				<div className="space-y-1.5">
					<Label htmlFor="oauth-client-name">Name</Label>
					<Input
						ref={nameRef}
						id="oauth-client-name"
						value={name}
						onChange={(e): void => setName(e.target.value)}
						placeholder="e.g., my-app-production"
						required
					/>
				</div>
				<div className="space-y-1.5">
					<Label htmlFor="oauth-client-description">Description (optional)</Label>
					<Input
						id="oauth-client-description"
						value={description}
						onChange={(e): void => setDescription(e.target.value)}
						placeholder="e.g., Production deployment for user auth"
					/>
				</div>
				<div className="space-y-1.5">
					<Label>Redirect URIs</Label>
					<RedirectUriList rows={uriRows} onChange={setUriRows} />
				</div>

				{!isEdit && (
					<>
						<div className="space-y-1.5">
							<Label htmlFor="oauth-client-type">Client type</Label>
							<Select
								id="oauth-client-type"
								value={clientType}
								onChange={(e): void =>
									setClientType(e.target.value as 'confidential' | 'public')
								}
							>
								<option value="confidential">
									Confidential — server-side app with a client secret
								</option>
								<option value="public">
									Public — native/SPA client, PKCE only (no secret)
								</option>
							</Select>
							<p className="text-muted-foreground text-xs">
								{clientType === 'public'
									? 'No secret is generated; the client must use PKCE.'
									: 'A one-time secret is shown after creation.'}
							</p>
						</div>
						<div className="space-y-1.5">
							<Label htmlFor="oauth-client-consent-model">Consent model</Label>
							<Select
								id="oauth-client-consent-model"
								value={consentModel}
								onChange={(e): void =>
									setConsentModel(e.target.value as 'user' | 'agent')
								}
							>
								<option value="user">
									User — consent lets the client act as the user
								</option>
								<option value="agent">
									Agent — consent binds the client to an agent (MCP)
								</option>
							</Select>
						</div>
					</>
				)}

				<Checkbox
					checked={requireConsent}
					onChange={setRequireConsent}
					size="sm"
					id="oauth-client-require-consent"
					ariaLabel="Require consent screen"
				>
					<span className="text-foreground text-sm">Require consent screen</span>
				</Checkbox>

				<div className="space-y-2">
					<Checkbox
						checked={restrictScopes}
						onChange={setRestrictScopes}
						size="sm"
						id="oauth-client-restrict-scopes"
						ariaLabel="Restrict allowed scopes"
					>
						<span className="text-foreground text-sm">Restrict allowed scopes</span>
					</Checkbox>
					{restrictScopes && (
						<AllowedScopesField selected={allowedScopes} onChange={setAllowedScopes} />
					)}
					{restrictScopes && allowedScopes.length === 0 && (
						<p className="text-muted-foreground text-xs">
							No scopes selected — this client will only be able to request OIDC
							scopes (openid, email, profile).
						</p>
					)}
				</div>
			</form>

			<footer className="border-border flex items-center justify-end gap-2 border-t p-5">
				<Button variant="secondary" onClick={onClose} disabled={isPending}>
					Cancel
				</Button>
				<Button type="submit" form="oauth-client-form" loading={isPending}>
					{isEdit ? 'Update' : 'Create'}
				</Button>
			</footer>
		</SheetPrimitive>
	);
}
