import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { ArrowLeft, Download, Info, Loader2 } from 'lucide-react';
import { Button, Dialog, ErrorAlert, Input, Label, Skeleton, toast } from '@/shared/ui';
import {
	CREDENTIAL_TYPE_ORDER,
	CredentialType,
	useApiSchemes,
	useCreateCredential,
	useImportCatalogEntry,
	useProviders,
	type SelectedApi,
} from '@/modules/credentials/api';
import {
	CredentialTypeFields,
	EMPTY_FORM,
	type CredentialFormState,
} from '@/modules/credentials/components/CredentialTypeFields';
import {
	buildCreateBody,
	seedApiKeyFromScheme,
	seedFormFromSelectedApi,
	seedOAuth2FromScheme,
	seedServerVars,
	validateCreate,
	validateServerVars,
} from '@/modules/credentials/lib/formBody';
import { managedProviderUnavailableMessage, providerOptions } from '@/modules/credentials/config';
import { ApiPicker } from '@/modules/credentials/components/ApiPicker';
import { AuthTypeCards } from '@/modules/credentials/components/AuthTypeCards';
import { ServerVariablesSection } from '@/modules/credentials/components/ServerVariablesSection';
import {
	oauth2FlowsFromSchemes,
	schemeTypeToCredentialType,
	type OAuth2FlowDef,
	type SchemeOption,
} from '@/modules/credentials/lib/schemes';
import {
	enhancedScopesFromSchemes,
	getRecommendedScopes,
	scopesInGroup,
} from '@/modules/credentials/lib/scope-utils';

export interface CreatedCredentialInfo {
	credentialId: string;
	type: CredentialType;
	provider: string;
	/**
	 * Whether the credential carries an authorize URL — i.e. it actually needs a
	 * browser-based connect flow. `client_credentials` (and other non-redirect
	 * grants) have no authorize URL and must NOT auto-connect.
	 */
	needsConnect: boolean;
}

interface CreateCredentialDialogProps {
	open: boolean;
	onClose: () => void;
	/** Called once the credential has been successfully created. */
	onCreated: (info: CreatedCredentialInfo) => void;
}

type Step = 'pick' | 'form';

/**
 * Centered dialog that guides the user through creating a credential.
 *
 * Two-step flow:
 *  1. **Pick** — search workspace + public catalog, or fall back to manual
 *     free-text entry for APIs missing/owning malformed specs.
 *  2. **Form** — auto-shaped from the picked API's `components.securitySchemes`
 *     (type, apiKey field name/location, scheme pills). The user can override
 *     the type via the scheme pills or by entering manually.
 *
 * On submit:
 *  - Un-registered catalog API → fire `POST /catalog/{id}:import` (async),
 *    then create against `{vendor,name,version}`.
 *  - Otherwise → create directly. Pipedream provider work composes under the
 *    oauth2 branch unchanged.
 *
 * Why a dialog (not the sheet pattern we use for edit): the create flow is a
 * focused, modal task with a wizard shape that benefits from a centred,
 * resizable container. The edit flow stays a sheet because it sits inline
 * with the list and supports quick back-and-forth.
 */
export function CreateCredentialDialog({ open, onClose, onCreated }: CreateCredentialDialogProps) {
	const [step, setStep] = useState<Step>('pick');
	const [selectedApi, setSelectedApi] = useState<SelectedApi | null>(null);
	const [manualMode, setManualMode] = useState(false);
	const [type, setType] = useState<CredentialType>(CredentialType.BEARER_TOKEN);
	/** When non-null, the spec drove the type (UI hides the manual toggle). */
	const [activeScheme, setActiveScheme] = useState<SchemeOption | null>(null);
	const [state, setState] = useState<CredentialFormState>(EMPTY_FORM);
	const [errors, setErrors] = useState<Partial<Record<keyof CredentialFormState, string>>>({});
	const [serverVarErrors, setServerVarErrors] = useState<Record<string, string>>({});
	const [oauth2Flows, setOAuth2Flows] = useState<OAuth2FlowDef[]>([]);
	/**
	 * The `id` of the currently-selected OAuth2 flow (e.g.
	 * `oauth2Primary.authorizationCode`). When the spec exposes multiple
	 * `(scheme, flow)` pairs the user picks one here; flow id drives URL
	 * seeding and the wire `grant_type`. Null until flows are parsed.
	 */
	const [activeFlowId, setActiveFlowId] = useState<string | null>(null);
	/**
	 * Tracks whether the user has manually touched scopes, so auto-selection of
	 * recommended scopes only fires once (when scopes first load) and never
	 * stomps a deliberate selection. Mirrors jentic-webapp.
	 */
	const hasUserInteractedWithScopes = useRef(false);
	/**
	 * Whether the user has manually edited the credential name. Until they do,
	 * switching the picked API refreshes the name to the new API's label.
	 */
	const nameDirty = useRef(false);

	// Stable id prefix for wiring <Label htmlFor> to the manual-entry + name
	// controls (the shared Input/Select auto-generate ids, but a label can't
	// see those, so we own the ids here).
	const fieldId = useId();

	const schemesResult = useApiSchemes(selectedApi);
	const createMutation = useCreateCredential();
	const importMutation = useImportCatalogEntry();
	const providersQuery = useProviders();

	const callbackUrl = useMemo(() => {
		const entry = providersQuery.data?.providers?.find((p) => p.id === state.provider);
		return entry?.callback_url ?? undefined;
	}, [providersQuery.data, state.provider]);

	// When the spec arrives, seed the form: pick a default scheme, derive the
	// credential type, prefill apiKey field_name/location, seed OAuth2 URLs and
	// grant type, and seed server variables from their spec defaults. The user
	// can still override via the type cards or by editing the fields directly.
	//
	// For OAuth2 we surface ALL `(scheme, flow)` pairs across every oauth2
	// scheme so the user can disambiguate when a spec declares more than one —
	// the grant-type selector then owns picking which scheme + flow drives the
	// URLs. We still seed initial state from the first parsed flow.
	useEffect(() => {
		if (!selectedApi || schemesResult.loading) return;
		if (manualMode) return;
		const first = schemesResult.options[0] ?? null;
		setActiveScheme(first);
		setState((s) => ({
			...s,
			serverVars: seedServerVars(s.serverVars, schemesResult.serverVars),
		}));
		const flows = oauth2FlowsFromSchemes(schemesResult.schemes);
		setOAuth2Flows(flows);
		setActiveFlowId(flows[0]?.id ?? null);
		if (first) {
			const derived = schemeTypeToCredentialType(first.type);
			if (derived) {
				setType(derived);
				setState((s) => ({ ...s, provider: providerOptions(derived)[0].id }));
				if (derived === CredentialType.API_KEY) {
					setState((s) => seedApiKeyFromScheme(s, schemesResult.schemes, first.name));
				}
				if (derived === CredentialType.OAUTH2) {
					setState(
						(s) =>
							seedOAuth2FromScheme(s, schemesResult.schemes, null, flows[0]?.id)
								.state,
					);
				}
			}
		}
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [selectedApi, schemesResult.loading, schemesResult.options.length]);

	const patch = (p: Partial<CredentialFormState>): void => {
		setState((s) => ({ ...s, ...p }));
	};

	/**
	 * User picked a different OAuth2 flow in the grant-type selector. Flow id
	 * is unique across `(scheme, flow)` pairs — switching it overwrites the
	 * spec-derived URLs (token + authorize) **even if the user previously had
	 * spec-seeded values**, because those values came from a different flow
	 * and would be misleading to keep. Values the user manually typed (that
	 * don't match the previous flow's URLs) are preserved.
	 */
	const handleFlowChange = (flowId: string): void => {
		const flow = oauth2Flows.find((f) => f.id === flowId);
		if (!flow) return;
		setActiveFlowId(flowId);
		setState((s) => {
			const prev = oauth2Flows.find((f) => f.id === activeFlowId);
			const tokenIsFromPrevSpec = !!prev?.tokenUrl && s.tokenUrl === prev.tokenUrl;
			const authorizeIsFromPrevSpec =
				!!prev?.authorizationUrl && s.authorizeUrl === prev.authorizationUrl;
			return {
				...s,
				grantType: flow.grantType,
				tokenUrl:
					!s.tokenUrl.trim() || tokenIsFromPrevSpec ? (flow.tokenUrl ?? '') : s.tokenUrl,
				authorizeUrl:
					!s.authorizeUrl.trim() || authorizeIsFromPrevSpec
						? (flow.authorizationUrl ?? '')
						: s.authorizeUrl,
			};
		});
	};

	const reset = (): void => {
		setStep('pick');
		setSelectedApi(null);
		setManualMode(false);
		setActiveScheme(null);
		setState(EMPTY_FORM);
		setErrors({});
		setServerVarErrors({});
		setOAuth2Flows([]);
		setActiveFlowId(null);
		hasUserInteractedWithScopes.current = false;
		nameDirty.current = false;
		setType(CredentialType.BEARER_TOKEN);
		createMutation.reset();
		importMutation.reset();
	};

	// Closing the dialog must always reset internal state — otherwise reopening
	// would land mid-wizard with stale data. We do this with an effect rather
	// than via Dialog's `onAfterClose` since the shared Dialog primitive
	// doesn't expose a post-close hook today.
	useEffect(() => {
		if (!open) reset();
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [open]);

	const handlePickApi = (api: SelectedApi): void => {
		setSelectedApi(api);
		setManualMode(false);
		setState((s) => seedFormFromSelectedApi(s, api, nameDirty.current));
		setStep('form');
	};

	const handleManualEntry = (): void => {
		setSelectedApi(null);
		setManualMode(true);
		setState(EMPTY_FORM);
		setStep('form');
	};

	/**
	 * User picked a type from the auth cards. In spec mode we also re-seed the
	 * apiKey field_name/location from the scheme that matches the chosen type
	 * (so switching to "API key" still benefits from the spec's header name).
	 */
	const handleTypeChange = (next: CredentialType): void => {
		setType(next);
		setErrors({});
		patch({ provider: providerOptions(next)[0].id });
		const matchingScheme =
			schemesResult.options.find((o) => schemeTypeToCredentialType(o.type) === next) ?? null;
		setActiveScheme(matchingScheme);
		if (next === CredentialType.API_KEY && matchingScheme) {
			setState((s) => seedApiKeyFromScheme(s, schemesResult.schemes, matchingScheme.name));
		}
		if (next === CredentialType.OAUTH2) {
			// Surface every `(scheme, flow)` pair across all oauth2 schemes —
			// the grant-type selector lets the user pick which one drives URLs.
			const flows = oauth2FlowsFromSchemes(schemesResult.schemes);
			setOAuth2Flows(flows);
			setActiveFlowId(flows[0]?.id ?? null);
			setState(
				(s) => seedOAuth2FromScheme(s, schemesResult.schemes, null, flows[0]?.id).state,
			);
		}
	};

	// --- Scope selection (OAuth2) -----------------------------------------
	// `state.scopes` stays the space-separated source of truth; these helpers
	// edit it as a set and flag manual interaction so auto-select backs off.
	const isOAuth2 = type === CredentialType.OAUTH2;

	// OAuth2 scopes declared by the spec — drives the grouped scope picker.
	// Enhanced with display metadata + recommended-by-default flags.
	const availableScopes = useMemo(
		() => (isOAuth2 ? enhancedScopesFromSchemes(schemesResult.schemes) : []),
		[isOAuth2, schemesResult.schemes],
	);

	const selectedScopeList = useMemo(
		() => state.scopes.split(/\s+/).filter(Boolean),
		[state.scopes],
	);

	const setScopes = (names: string[]): void => {
		patch({ scopes: Array.from(new Set(names)).join(' ') });
	};

	const handleScopeToggle = (scope: string): void => {
		hasUserInteractedWithScopes.current = true;
		const set = new Set(selectedScopeList);
		if (set.has(scope)) set.delete(scope);
		else set.add(scope);
		setScopes([...set]);
	};

	const handleScopeSelectAll = (groupId?: string): void => {
		hasUserInteractedWithScopes.current = true;
		if (groupId) {
			setScopes([...selectedScopeList, ...scopesInGroup(availableScopes, groupId)]);
		} else {
			setScopes(availableScopes.map((s) => s.scope));
		}
	};

	const handleScopeDeselectAll = (groupId?: string): void => {
		hasUserInteractedWithScopes.current = true;
		if (groupId) {
			const inGroup = new Set(scopesInGroup(availableScopes, groupId));
			setScopes(selectedScopeList.filter((s) => !inGroup.has(s)));
		} else {
			setScopes([]);
		}
	};

	// Auto-select recommended (read-only / safe) scopes the first time scopes
	// become available for an OAuth2 API, unless the user already touched them.
	// Mirrors jentic-webapp: a one-shot convenience, never overriding intent.
	useEffect(() => {
		if (
			isOAuth2 &&
			availableScopes.length > 0 &&
			selectedScopeList.length === 0 &&
			!hasUserInteractedWithScopes.current
		) {
			const recommended = getRecommendedScopes(availableScopes);
			if (recommended.length > 0) {
				setScopes(recommended.map((s) => s.scope));
			}
		}
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [isOAuth2, availableScopes, selectedScopeList.length]);

	const handleSubmit = async (e: React.FormEvent): Promise<void> => {
		e.preventDefault();
		const validation = validateCreate(type, state);
		setErrors(validation);
		const svErrors = validateServerVars(schemesResult.serverVars, state.serverVars);
		setServerVarErrors(svErrors);
		if (Object.keys(validation).length > 0 || Object.keys(svErrors).length > 0) return;

		const body = buildCreateBody(type, state);

		// Catalog APIs the user just picked may not be in the local registry
		// yet — fire the async import first. The import resolves the same
		// {vendor,name,version} triple at create time, so we don't have to wait
		// for it to complete; we wait only long enough to surface a failure.
		if (selectedApi?.source === 'catalog' && !selectedApi.registered && selectedApi.apiId) {
			try {
				await importMutation.mutateAsync(selectedApi.apiId);
			} catch {
				return;
			}
		}

		createMutation.mutate(body, {
			onSuccess: (data) => {
				const credName = state.name.trim();
				toast({
					title: 'Credential created',
					description: credName ? `${credName} is ready to use.` : undefined,
					variant: 'success',
				});
				onCreated({
					credentialId: data.credential.credential_id,
					type,
					provider: state.provider,
					// Only authorization-code style grants (which carry an authorize
					// URL) need a browser connect flow. client_credentials and other
					// non-redirect grants must not auto-connect.
					needsConnect:
						state.authorizeUrl.trim().length > 0 ||
						state.grantType.trim() === 'authorization_code',
				});
				// Closing the dialog triggers the open-watching effect which
				// resets state — no need to call reset() here directly.
			},
		});
	};

	// The picked-API summary banner shown atop the form step (label + the
	// vendor/name@version triple + whether saving will trigger a catalog import).
	const apiSummary = useMemo(() => {
		if (!selectedApi) return null;
		const triple = `${selectedApi.vendor}/${selectedApi.name}@${selectedApi.version}`;
		const willImport = selectedApi.source === 'catalog' && !selectedApi.registered;
		return { label: selectedApi.label, triple, willImport };
	}, [selectedApi]);

	const showManualType = manualMode || activeScheme == null || activeScheme.type === 'unknown';
	const usingPipedream = isOAuth2 && state.provider === 'pipedream';

	// Types to offer in the auth cards:
	//  - manual / unknown spec → all four (free choice)
	//  - spec-driven → the distinct types the schemes declared (often one),
	//    deduped & in canonical order; we still render the single card so the
	//    user sees what was detected and can confirm.
	const typeOptions = useMemo<CredentialType[]>(() => {
		if (showManualType) return [...CREDENTIAL_TYPE_ORDER];
		const fromSpec = schemesResult.options
			.map((o) => schemeTypeToCredentialType(o.type))
			.filter((t): t is CredentialType => t != null);
		const deduped = CREDENTIAL_TYPE_ORDER.filter((t) => fromSpec.includes(t));
		return deduped.length > 0 ? deduped : [type];
	}, [showManualType, schemesResult.options, type]);

	const detectedSingle = !showManualType && typeOptions.length === 1;

	const serverVars = manualMode ? [] : schemesResult.serverVars;

	// While a picked API's spec is still being fetched we don't yet know the
	// auth type — so we hold back the type selector + credential fields and
	// show a skeleton in their place (mirrors jentic-webapp, which gates the
	// auth UI behind `!isLoadingSchemes`). Manual mode has no spec to wait on.
	const specPending = !manualMode && !!selectedApi && schemesResult.loading;

	const titleSuffix = selectedApi?.label ? ` — ${selectedApi.label}` : '';
	const title = step === 'pick' ? 'Choose an API' : `Add credential${titleSuffix}`;
	const subtitle =
		step === 'pick' ? (
			<span>
				<span className="font-mono text-[10px] tracking-widest uppercase">Step 1 of 2</span>{' '}
				· Pick the API this credential will authenticate against
			</span>
		) : (
			<span>
				<span className="font-mono text-[10px] tracking-widest uppercase">Step 2 of 2</span>{' '}
				· Fill in the credential details
			</span>
		);

	const goBackToPick = (): void => {
		setStep('pick');
		setErrors({});
	};

	const footer =
		step === 'form' ? (
			<>
				<Button
					variant="secondary"
					onClick={goBackToPick}
					disabled={createMutation.isPending || importMutation.isPending}
					type="button"
					className="mr-auto"
				>
					<ArrowLeft className="h-4 w-4" />
					Back
				</Button>
				<Button
					variant="ghost"
					onClick={onClose}
					disabled={createMutation.isPending || importMutation.isPending}
					type="button"
				>
					Cancel
				</Button>
				<Button
					type="submit"
					form="create-credential-form"
					variant="primary"
					loading={createMutation.isPending || importMutation.isPending}
					disabled={specPending}
				>
					Create credential
				</Button>
			</>
		) : undefined;

	return (
		<Dialog
			open={open}
			onClose={onClose}
			title={title}
			subtitle={subtitle}
			size={step === 'pick' ? 'lg' : 'xl'}
			footer={footer}
			dismissOnBackdrop={false}
		>
			{step === 'pick' && (
				<ApiPicker onSelect={handlePickApi} onManualEntry={handleManualEntry} />
			)}

			{step === 'form' && (
				<form id="create-credential-form" onSubmit={handleSubmit} className="space-y-5">
					{apiSummary && (
						<div
							className="bg-muted/40 border-border flex items-center gap-3 rounded-xl border px-3 py-2.5"
							data-testid="selected-api-summary"
						>
							<div className="min-w-0 flex-1">
								<div className="flex items-center gap-2">
									<p className="text-foreground truncate text-sm font-medium">
										{apiSummary.label}
									</p>
									{specPending && (
										<span className="text-muted-foreground inline-flex shrink-0 items-center gap-1 text-[11px]">
											<Loader2 className="h-3 w-3 animate-spin" />
											reading spec…
										</span>
									)}
								</div>
								<p className="text-muted-foreground mt-0.5 flex items-center gap-1.5 truncate font-mono text-xs">
									{apiSummary.triple}
									{apiSummary.willImport && (
										<span className="text-muted-foreground/80 inline-flex items-center gap-1">
											<span aria-hidden>·</span>
											<Download className="h-3 w-3" />
											imports on save
										</span>
									)}
								</p>
							</div>
							<Button
								type="button"
								variant="ghost"
								size="sm"
								onClick={goBackToPick}
								className="text-muted-foreground hover:text-foreground shrink-0 text-xs"
							>
								Change
							</Button>
						</div>
					)}

					{manualMode && (
						<fieldset className="border-border space-y-3 rounded-lg border p-3">
							<legend className="text-muted-foreground px-1 text-xs font-medium">
								API reference
							</legend>
							<div className="space-y-1.5">
								<Label htmlFor={`${fieldId}-vendor`} required>
									Vendor
								</Label>
								<Input
									id={`${fieldId}-vendor`}
									value={state.apiVendor}
									onChange={(e): void => patch({ apiVendor: e.target.value })}
									placeholder="acme"
									error={errors.apiVendor}
								/>
							</div>
							<div className="grid grid-cols-2 gap-3">
								<div className="space-y-1.5">
									<Label htmlFor={`${fieldId}-apiname`}>API name</Label>
									<Input
										id={`${fieldId}-apiname`}
										value={state.apiName}
										onChange={(e): void => patch({ apiName: e.target.value })}
										placeholder="default"
									/>
								</div>
								<div className="space-y-1.5">
									<Label htmlFor={`${fieldId}-version`}>Version</Label>
									<Input
										id={`${fieldId}-version`}
										value={state.apiVersion}
										onChange={(e): void =>
											patch({ apiVersion: e.target.value })
										}
										placeholder="1.0.0"
									/>
								</div>
							</div>
						</fieldset>
					)}

					<div className="space-y-2">
						<FormSectionLabel>Credential details</FormSectionLabel>
						<div className="space-y-1.5">
							<Label htmlFor={`${fieldId}-name`} required>
								Name
							</Label>
							<Input
								id={`${fieldId}-name`}
								value={state.name}
								onChange={(e): void => {
									nameDirty.current = true;
									patch({ name: e.target.value });
								}}
								placeholder="Production API key"
								error={errors.name}
							/>
							<p className="text-muted-foreground text-xs">
								A label to recognise this credential later.
							</p>
						</div>
					</div>

					{/*
					 * Auth section. Three mutually-exclusive states, cross-faded
					 * so the dialog never janks:
					 *  - pending  → skeleton (we don't know the auth type yet)
					 *  - error    → "couldn't read spec" note + manual type fallback
					 *  - ready    → scheme pills / single-scheme chip + fields
					 */}
					<AnimatePresence mode="wait" initial={false}>
						{specPending ? (
							<motion.div
								key="auth-skeleton"
								initial={{ opacity: 0 }}
								animate={{ opacity: 1 }}
								exit={{ opacity: 0 }}
								transition={{ duration: 0.15 }}
								className="border-border border-t pt-5"
							>
								<AuthSectionSkeleton />
							</motion.div>
						) : (
							<motion.div
								key="auth-ready"
								initial={{ opacity: 0, y: 8 }}
								animate={{ opacity: 1, y: 0 }}
								exit={{ opacity: 0 }}
								transition={{ duration: 0.2, ease: 'easeOut' }}
								className="border-border space-y-5 border-t pt-5"
							>
								{!manualMode && schemesResult.error && (
									<p
										className="border-border bg-muted/40 text-muted-foreground rounded-lg border p-3 text-xs leading-snug"
										role="note"
									>
										We couldn&apos;t read the API spec — pick the type manually
										below.
									</p>
								)}

								<AuthTypeCards
									options={typeOptions}
									value={type}
									onChange={handleTypeChange}
									detected={detectedSingle}
								/>

								{serverVars.length > 0 && (
									<ServerVariablesSection
										variables={serverVars}
										values={state.serverVars}
										errors={serverVarErrors}
										onChange={(name, value): void => {
											setState((s) => ({
												...s,
												serverVars: { ...s.serverVars, [name]: value },
											}));
											setServerVarErrors((e) => {
												if (!e[name]) return e;
												const next = { ...e };
												delete next[name];
												return next;
											});
										}}
									/>
								)}

								<div className="space-y-4">
									<CredentialTypeFields
										type={type}
										state={state}
										onChange={patch}
										errors={errors}
										mode="create"
										scope={
											isOAuth2 && availableScopes.length > 0
												? {
														available: availableScopes,
														selected: selectedScopeList,
														onToggle: handleScopeToggle,
														onSelectAll: handleScopeSelectAll,
														onDeselectAll: handleScopeDeselectAll,
													}
												: undefined
										}
										flows={isOAuth2 ? oauth2Flows : undefined}
										activeFlowId={isOAuth2 ? activeFlowId : undefined}
										onFlowChange={isOAuth2 ? handleFlowChange : undefined}
										callbackUrl={isOAuth2 ? callbackUrl : undefined}
										providers={providersQuery.data?.providers}
									/>
								</div>

								{usingPipedream && (
									<div
										className="border-primary/20 bg-primary/5 text-primary/90 flex items-start gap-2 rounded-lg border p-3 text-xs leading-snug"
										role="note"
									>
										<Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
										<span>
											Pipedream handles the OAuth handshake. After creating,
											use <strong>Connect</strong> from the credentials list
											to sign in.
										</span>
									</div>
								)}
							</motion.div>
						)}
					</AnimatePresence>

					{importMutation.isError && <ErrorAlert message={importMutation.error} />}

					{createMutation.isError &&
						(() => {
							const friendly = managedProviderUnavailableMessage(
								state.provider,
								createMutation.error,
							);
							return friendly ? (
								<ErrorAlert message={friendly} />
							) : (
								<ErrorAlert message={createMutation.error} />
							);
						})()}
				</form>
			)}
		</Dialog>
	);
}

/**
 * Small uppercase section heading used to give the form a clear visual
 * ordering ("Credential details" → "Authentication"). Mirrors the section
 * headings used in the API picker so the wizard feels of-a-piece.
 */
function FormSectionLabel({ children }: { children: React.ReactNode }) {
	return (
		<p className="text-muted-foreground px-0.5 font-mono text-[10px] tracking-widest uppercase">
			{children}
		</p>
	);
}

/**
 * Placeholder shown in the form step while the picked API's OpenAPI spec is
 * being fetched. Mirrors the eventual layout (auth-method cards → field stack)
 * so the swap to real content doesn't shift the dialog height.
 */
function AuthSectionSkeleton() {
	return (
		<div className="space-y-5" aria-hidden>
			{/* Auth-method cards */}
			<div className="space-y-2">
				<Skeleton className="h-4 w-44" />
				<Skeleton className="h-3 w-64" />
				<div className="grid gap-2.5 sm:grid-cols-2">
					<Skeleton className="h-[4.5rem] rounded-xl" />
					<Skeleton className="h-[4.5rem] rounded-xl" />
				</div>
			</div>
			{/* Field stack */}
			<div className="space-y-4">
				<div className="space-y-1.5">
					<Skeleton className="h-3.5 w-20" />
					<Skeleton className="h-9 w-full rounded-lg" />
				</div>
				<div className="space-y-1.5">
					<Skeleton className="h-3.5 w-24" />
					<Skeleton className="h-9 w-full rounded-lg" />
				</div>
			</div>
		</div>
	);
}
