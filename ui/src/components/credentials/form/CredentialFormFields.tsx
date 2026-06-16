import React, { useEffect, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';
import { SchemePillBar } from './SchemePillBar';
import { ServerVariablesFields } from './ServerVariablesFields';
import { OAuthBrokerFields } from './OAuthBrokerFields';
import { AdvancedBrokerFields } from './AdvancedBrokerFields';
import { AuthTypeFields } from './AuthTypeFields';
import { TestConnectionButton } from '@/components/credentials/TestConnectionButton';
import type { ApiOut, CredentialCreate, CredentialOut, CredentialPatch } from '@/api/types';
import { api } from '@/api/client';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Label } from '@/components/ui/Label';
import { Textarea } from '@/components/ui/Textarea';
import { ErrorAlert } from '@/components/ui/ErrorAlert';
import { LoadingState } from '@/components/ui/LoadingState';
import { emitCredentialImported } from '@/lib/events/credentialImported';
import { emitApiImported } from '@/lib/events/apiImported';
import { useApiSchemes } from '@/hooks/useApiSchemes';
import { useApiServerVarDefs } from '@/hooks/useApiServerVarDefs';
import {
	parseSchemeOptions,
	isCompoundApiKey,
	compoundLabels,
	type SchemeOption,
	type SchemeType,
} from '@/lib/credentials/schemes';

/**
 * Optional values used to prefill a new credential — e.g. coming from
 * an agent-built `?api_id=…&label=…&server_vars[host]=…` deeplink, or
 * from a toolkit-anchored "add credential" dialog that already knows
 * the API + label conventions.
 *
 * `value` (the secret) is supported but rarely populated; agents
 * usually leave it blank for the user to paste, so the prefill keeps
 * the secret-handling story narrow.
 */
export interface CredentialFormPrefill {
	label?: string;
	value?: string;
	identity?: string;
	serverVars?: Record<string, string>;
}

export interface CredentialFormFieldsProps {
	selectedApi: ApiOut;
	/**
	 * Edit mode iff `editId` and `existing` are provided. They are
	 * separate props because the host might know the id (from the
	 * URL) before the existing credential row has loaded — we render
	 * a loading state in that case.
	 */
	editId?: string;
	existing?: CredentialOut;
	prefill?: CredentialFormPrefill;
	/**
	 * "Cancel" / "Change API" path. Called when the user clicks the
	 * `Change` chip in the selected-API summary (revert to picker)
	 * or the form-footer Cancel button. Hosts decide what that means:
	 *
	 *   - Page form: `navigate(-1)` or back to `/credentials`.
	 *   - Sheet: close the sheet.
	 *   - Dialog: go back to the search step.
	 */
	onBack: () => void;
	/**
	 * Save success path. Receives the freshly-saved credential row
	 * (create) or the previous `editId` (edit). Hosts use it to close
	 * the surface and navigate / invalidate.
	 */
	onSaved: (saved: { id: string; api_id: string }) => void;
	/**
	 * When true, hide the selected-API summary chip and the
	 * "Change" affordance. Useful for the toolkit-anchored add
	 * dialog where the API is locked-in by an earlier step.
	 */
	hideApiSummary?: boolean;
	/**
	 * Layout mode:
	 *  - `'inline'` (default): the form is a normal block on a `bg-muted`
	 *    card (the `/credentials/new` page). Fields and a sticky-on-scroll
	 *    footer flow in the host's own scroll container; the footer blends
	 *    with the page card surface.
	 *  - `'dialog'`: same flowing layout as `inline`, but the sticky
	 *    footer blends with the dialog surface (`bg-card`) and aligns to
	 *    the dialog body's own gutter (no negative-margin hack). Used by
	 *    `AddCredentialDialog` → `ConfigureStep`.
	 *  - `'sheet'`: the form owns a full-height flex column — fields
	 *    scroll in the middle and the Save/Cancel footer is pinned
	 *    flush to the bottom edge with a solid divider (no blur /
	 *    negative-margin hack). Used by `CredentialEditSheet`, whose
	 *    host gives the form `h-full`.
	 */
	layout?: 'inline' | 'dialog' | 'sheet';
}

const AUTH_TYPE_MAP: Record<SchemeType, CredentialCreate['auth_type']> = {
	bearer: 'bearer',
	apiKey: 'apiKey',
	basic: 'basic',
	oauth2: undefined,
	unknown: undefined,
};

/**
 * The actual credential form body — no page chrome, no sheet/dialog
 * frame, no router awareness. Three host surfaces compose this:
 *
 *   1. `CredentialFormPage` (`/credentials/new`) — the legacy full
 *      page. Wraps in `PageShell` + `PageHeader` and forwards
 *      `onBack`/`onSaved` to navigation.
 *   2. `CredentialEditSheet` (Phase 2) — slides in from the right;
 *      forwards `onBack` to a sheet close.
 *   3. `AddCredentialDialog` (Phase 3) — multi-step dialog;
 *      forwards `onBack` to the previous step and `onSaved` to a
 *      `binding` step (toolkit mode) or close.
 *
 * Owns:
 *  - All local form state (label, value, identity, description,
 *    server vars, scheme selection, advanced fields).
 *  - The two mutations (create + edit) — not the host, because
 *    error UI and pending state both live in the body.
 *  - Cross-tab event emission on save (`credentialImported`,
 *    `apiImported`).
 *
 * Does NOT own:
 *  - Routing.
 *  - "What does Cancel mean here" — the host decides.
 *  - The selected API itself — the host owns it. (We render a
 *    summary chip with a `Change` button that calls `onBack`; the
 *    host can interpret that as "go back to the picker" or
 *    something else.)
 */
export function CredentialFormFields({
	selectedApi,
	editId,
	existing,
	prefill,
	onBack,
	onSaved,
	hideApiSummary,
	layout = 'inline',
}: CredentialFormFieldsProps) {
	const queryClient = useQueryClient();
	const isEdit = !!editId;
	const isSheet = layout === 'sheet';
	const isDialog = layout === 'dialog';

	const { schemes, loading: schemesLoading, localDetail, spec } = useApiSchemes(selectedApi);
	const serverVarDefs = useApiServerVarDefs(selectedApi, localDetail, spec);
	const schemeOptions = parseSchemeOptions(schemes);
	const defaultScheme = schemeOptions[0] ?? null;
	const [selectedScheme, setSelectedScheme] = useState<SchemeOption | null>(null);

	const [serverVars, setServerVars] = useState<Record<string, string>>({});

	const [label, setLabel] = useState(
		prefill?.label ?? existing?.label ?? selectedApi.name ?? selectedApi.id,
	);
	const [value, setValue] = useState(prefill?.value ?? '');
	const [identity, setIdentity] = useState(prefill?.identity ?? existing?.identity ?? '');
	// `description` is bookkeeping metadata only — never sent upstream.
	// Round-trips through the Tier-1 `credentials.description` column.
	const [description, setDescription] = useState(existing?.description ?? '');
	const [error, setError] = useState<string | Error | null>(null);

	const [schemeJson, setSchemeJson] = useState(
		existing?.scheme ? JSON.stringify(existing.scheme, null, 2) : '',
	);
	const [routesText, setRoutesText] = useState(
		existing?.routes ? (existing.routes as string[]).join('\n') : '',
	);
	const [showAdvanced, setShowAdvanced] = useState(!!(existing?.scheme || existing?.routes));

	// Reset state when the user picks a different API. Keep prefill
	// values where they apply (label / identity / server vars) so
	// agent deeplinks survive an "API change" round-trip.
	useEffect(() => {
		setSelectedScheme(null);
		setLabel(prefill?.label ?? selectedApi.name ?? selectedApi.id);
		setValue(prefill?.value ?? '');
		setIdentity(prefill?.identity ?? '');
		setServerVars(prefill?.serverVars ?? {});
		setError(null);
		// eslint-disable-next-line react-hooks/exhaustive-deps -- intentional reset boundary
	}, [selectedApi.id]);

	// When the server-var defs arrive (could be after the initial
	// render thanks to async spec fetch), seed defaults — but only
	// if the user hasn't started editing yet. Prefill values win
	// over spec defaults, matching agent expectations.
	useEffect(() => {
		if (serverVarDefs.length > 0 && Object.keys(serverVars).length === 0) {
			const defaults: Record<string, string> = {};
			serverVarDefs.forEach((v) => {
				if (v.default) defaults[v.name] = v.default;
			});
			if (Object.keys(defaults).length > 0)
				setServerVars((prev) =>
					Object.keys(prev).length > 0
						? prev
						: { ...defaults, ...(prefill?.serverVars ?? {}) },
				);
		}
		// eslint-disable-next-line react-hooks/exhaustive-deps -- triggered by defs identity change
	}, [serverVarDefs, prefill?.serverVars]);

	// Hydrate from `existing` when entering edit mode.
	// The secret value is intentionally NOT prefilled — it's
	// write-only at the API level (we never read it back), and the
	// UI reflects that with the "leave blank to keep existing"
	// placeholder.
	useEffect(() => {
		if (existing) {
			setLabel(existing.label ?? '');
			setIdentity(existing.identity ?? '');
			setDescription(existing.description ?? '');
			if (existing.server_variables && Object.keys(existing.server_variables).length > 0) {
				setServerVars(existing.server_variables as Record<string, string>);
			}
			setSchemeJson(existing.scheme ? JSON.stringify(existing.scheme, null, 2) : '');
			setRoutesText(existing.routes ? (existing.routes as string[]).join('\n') : '');
			setShowAdvanced(!!(existing.scheme || existing.routes));
		}
	}, [existing]);

	const activeScheme = selectedScheme ?? defaultScheme;
	const schemeType = activeScheme?.type ?? 'unknown';
	const compound = isCompoundApiKey(schemes);
	const { secretLabel, identityLabel } = compoundLabels(schemes);

	const createMutation = useMutation({
		mutationFn: (d: CredentialCreate) => api.createCredential(d),
		onSuccess: (created) => {
			queryClient.invalidateQueries({ queryKey: ['credentials'] });
			const createdId = (created as { id?: string } | undefined)?.id;
			if (createdId) {
				queryClient.invalidateQueries({ queryKey: ['credential', createdId] });
			}
			const apiIdFromForm =
				selectedApi?.id ?? (created as { api_id?: string } | undefined)?.api_id ?? '';
			emitCredentialImported({ api_id: apiIdFromForm });
			// Catalog imports trigger a server-side
			// `ensure_catalog_api_imported`. Surface the resulting
			// API arrival as a separate event so the catalog
			// "Available" pill can flip to "In workspace" without
			// overloading `credentialImported`.
			if (apiIdFromForm && selectedApi?.source === 'catalog') {
				emitApiImported({ api_id: apiIdFromForm, source: 'catalog' });
			}
			onSaved({ id: (created as any).id, api_id: apiIdFromForm });
		},
		onError: (e: Error) => setError(e),
	});

	const updateMutation = useMutation({
		mutationFn: (d: CredentialPatch) => api.updateCredential(editId!, d),
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: ['credentials'] });
			queryClient.invalidateQueries({ queryKey: ['credential', editId!] });
			onSaved({ id: editId!, api_id: selectedApi.id });
		},
		onError: (e: Error) => setError(e),
	});

	const handleSubmit = (e: React.FormEvent) => {
		e.preventDefault();
		if (schemeType === 'oauth2') return;
		setError(null);

		const missingVars = serverVarDefs.filter((v) => v.required && !serverVars[v.name]?.trim());
		if (missingVars.length > 0) {
			setError(
				`Required server variables missing: ${missingVars.map((v) => v.name).join(', ')}`,
			);
			return;
		}

		const cleanedVars =
			Object.keys(serverVars).length > 0
				? Object.fromEntries(Object.entries(serverVars).filter(([, v]) => v.trim()))
				: null;

		if (isEdit) {
			let parsedScheme: Record<string, unknown> | null = null;
			if (schemeJson.trim()) {
				try {
					parsedScheme = JSON.parse(schemeJson.trim());
				} catch {
					setError('Scheme JSON is invalid');
					return;
				}
			}
			const parsedRoutes = routesText.trim()
				? routesText
						.split('\n')
						.map((r) => r.trim())
						.filter(Boolean)
				: null;

			updateMutation.mutate({
				label: label || null,
				api_id: selectedApi.id,
				auth_type: AUTH_TYPE_MAP[schemeType],
				value: value || null,
				identity: identity || null,
				server_variables: cleanedVars,
				scheme: parsedScheme,
				routes: parsedRoutes,
				description: description.trim() || null,
			});
			return;
		}

		if (!value) {
			setError('Credential value is required');
			return;
		}
		createMutation.mutate({
			label,
			api_id: selectedApi.id,
			auth_type: AUTH_TYPE_MAP[schemeType],
			value,
			identity: identity || undefined,
			server_variables: cleanedVars,
			description: description.trim() || undefined,
		});
	};

	const isPending = createMutation.isPending || updateMutation.isPending;

	if (schemesLoading) {
		return (
			<LoadingState
				message="Reading API spec…"
				icon={<Loader2 className="h-5 w-5 animate-spin" />}
			/>
		);
	}

	return (
		<form onSubmit={handleSubmit} className={isSheet ? 'flex h-full flex-col' : 'space-y-5'}>
			<div className={isSheet ? 'flex-1 space-y-5 overflow-y-auto px-5 py-4' : 'space-y-5'}>
				{!hideApiSummary && (
					<div className="bg-muted/50 border-border flex items-center gap-2 rounded-lg border px-3 py-2.5">
						<div className="min-w-0 flex-1">
							<p className="text-foreground text-sm font-medium">
								{selectedApi.name ?? selectedApi.id}
							</p>
							<p className="text-muted-foreground truncate font-mono text-xs">
								{selectedApi.id}
							</p>
						</div>
						<Button
							variant="ghost"
							size="sm"
							onClick={onBack}
							className="text-muted-foreground hover:text-foreground shrink-0 text-xs transition-colors"
						>
							Change
						</Button>
					</div>
				)}

				<SchemePillBar
					options={schemeOptions}
					active={activeScheme}
					onChange={setSelectedScheme}
				/>

				<ServerVariablesFields
					defs={serverVarDefs}
					values={serverVars}
					onChange={(name, val) => setServerVars((prev) => ({ ...prev, [name]: val }))}
				/>

				<div>
					<Label
						htmlFor="cred-label"
						className="text-muted-foreground mb-1 block text-xs"
					>
						Label
					</Label>
					<Input
						id="cred-label"
						type="text"
						value={label}
						onChange={(e) => setLabel(e.target.value)}
						required
						className="bg-background"
					/>
				</div>

				<div>
					<Label
						htmlFor="cred-description"
						className="text-muted-foreground mb-1 block text-xs"
					>
						Description <span className="text-muted-foreground/60">(optional)</span>
					</Label>
					<Textarea
						id="cred-description"
						value={description}
						onChange={(e) => setDescription(e.target.value)}
						placeholder="What is this credential used for? When was it rotated?"
						rows={2}
						className="bg-background"
					/>
				</div>

				{schemeType === 'oauth2' && (
					<OAuthBrokerFields selectedApi={selectedApi} label={label} />
				)}

				<AuthTypeFields
					schemeType={schemeType}
					isEdit={isEdit}
					value={value}
					onValueChange={setValue}
					identity={identity}
					onIdentityChange={setIdentity}
					compound={compound}
					secretLabel={secretLabel}
					identityLabel={identityLabel}
				/>

				{error && <ErrorAlert message={error} />}

				{schemeType !== 'oauth2' && (
					<AdvancedBrokerFields
						open={showAdvanced}
						onToggle={() => setShowAdvanced((v) => !v)}
						schemeJson={schemeJson}
						onSchemeJsonChange={setSchemeJson}
						routesText={routesText}
						onRoutesTextChange={setRoutesText}
					/>
				)}

				{/* Test connection — edit mode only.
				 *
				 * The probe needs a saved credential ID (the backend
				 * decrypts the value from `credentials.id`), so we only
				 * render this when we're editing an existing credential.
				 * New credentials get tested by clicking back into the
				 * row in the list after save. */}
				{isEdit && editId && (
					<div className="border-border/60 rounded-lg border border-dashed p-3">
						<p className="text-muted-foreground mb-2 text-xs">
							Verify the credential by issuing a single, low-impact probe to the
							upstream API. Bearer / API key creds only — Pipedream OAuth grants are
							validated by the broker.
						</p>
						<TestConnectionButton credentialId={editId} />
					</div>
				)}
			</div>

			{/* Save / Cancel footer.
			 *
			 * Sheet layout: a solid `shrink-0` footer pinned flush to
			 * the bottom edge of the flex column, matching the sheet
			 * panel surface (`bg-card`) with a clean divider.
			 *
			 * Dialog layout: a flush footer that bleeds horizontally to
			 * the dialog body's edges (`-mx-5 px-5`) with a full-width
			 * top divider. NOT sticky and NO bottom negative margin —
			 * the parent `<form>`'s `space-y-5` supplies the 20px gap
			 * above the divider, `pt-4` supplies 16px below it, and the
			 * dialog body's own `py-4` supplies the matching 16px below
			 * the buttons. That keeps the buttons symmetric in the band.
			 *
			 * Inline (page) layout: same flush footer, but without the
			 * horizontal bleed — it sits within the page card's padding.
			 *
			 * For NEW OAuth2 credentials we hide submit — the
			 * Pipedream "Connect" flow above is the actual save
			 * path. For EDITS we always show it (issue #159) — even
			 * Pipedream-managed credentials let the user PATCH
			 * label/description/etc. without touching the upstream
			 * grant. */}
			{(isEdit || schemeType !== 'oauth2') && (
				<div
					className={
						isSheet
							? 'border-border bg-card flex shrink-0 gap-2 border-t px-5 py-4'
							: isDialog
								? 'border-border -mx-5 flex flex-col-reverse gap-2 border-t px-5 pt-4 sm:flex-row sm:items-center'
								: 'border-border flex flex-col-reverse gap-2 border-t pt-4 sm:flex-row sm:items-center'
					}
				>
					<Button type="submit" loading={isPending} className="flex-1">
						{isEdit ? 'Update Credential' : 'Save Credential'}
					</Button>
					<Button
						type="button"
						variant="secondary"
						onClick={onBack}
						className="sm:flex-none"
					>
						Cancel
					</Button>
				</div>
			)}
		</form>
	);
}
