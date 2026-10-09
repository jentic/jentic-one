import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { ArrowLeft, Download, Info, Loader2, LockOpen, Upload, Users, X } from 'lucide-react';
import {
	Button,
	Dialog,
	ErrorAlert,
	Input,
	Label,
	Select,
	SheetBody,
	SheetFooter,
	SheetHeader,
	SheetPrimitive,
	Skeleton,
	toast,
} from '@/shared/ui';
import {
	OAuthAppRegistrationFlowKind,
	type AuthorizationCodeRegistrationCreateRequest,
	type DeviceAuthorizationRegistrationCreateRequest,
} from '@/shared/api';
import { useCreateOAuthAppRegistration } from '@/shared/credentials/oauth-app-registrations/api/hooks';
import {
	CREDENTIAL_TYPE_ORDER,
	CredentialType,
	useAllCredentials,
	useApiSchemes,
	useCreateCredential,
	useImportCatalogEntry,
	useProviders,
	type SelectedApi,
	type VendorSummary,
} from '@/shared/credentials/api';
import {
	CredentialTypeFields,
	EMPTY_FORM,
	type CredentialFormState,
} from '@/shared/credentials/components/CredentialTypeFields';
import {
	buildCreateBody,
	seedApiKeyFromScheme,
	seedFormFromSelectedApi,
	seedOAuth2FromScheme,
	seedServerVars,
	validateCreate,
	validateServerVars,
} from '@/shared/credentials/lib/formBody';
import { credentialNameClash } from '@/shared/credentials/lib/credentialIdentity';
import { CredentialNameClashNote } from '@/shared/credentials/components/CredentialNameClashNote';
import { managedProviderUnavailableMessage, providerOptions } from '@/shared/credentials/config';
import { ApiPicker } from '@/shared/credentials/components/ApiPicker';
import { ImportSpecDialog } from '@/shared/credentials/components/ImportSpecDialog';
import { AuthTypeCards } from '@/shared/credentials/components/AuthTypeCards';
import { ServerVariablesSection } from '@/shared/credentials/components/ServerVariablesSection';
import {
	CredentialVersionScope,
	pinnableVersionOf,
} from '@/shared/credentials/components/CredentialVersionScope';
import {
	VendorConnectFlow,
	type PostConnectInfo,
} from '@/shared/credentials/components/VendorConnectFlow';
import {
	apiKeyFieldsFromScheme,
	oauth2FlowsFromSchemes,
	schemeTypeToCredentialType,
	specDeclaresNoAuth,
	type OAuth2FlowDef,
	type SchemeOption,
} from '@/shared/credentials/lib/schemes';
import {
	enhancedScopesFromSchemes,
	getRecommendedScopes,
	scopesInGroup,
} from '@/shared/credentials/lib/scope-utils';

export interface CreatedCredentialInfo {
	credentialId: string;
	/** The saved label, so a binding caller can name it rather than report an
	 *  anonymous success. */
	name: string;
	type: CredentialType;
	provider: string;
	/**
	 * Whether the credential needs a browser-based connect flow before it can
	 * be used. True for authorization_code grants (browser redirect) and for
	 * device_code grants (RFC 8628 human step). `client_credentials` (and
	 * other non-interactive grants) have no user action and must NOT
	 * auto-connect.
	 */
	needsConnect: boolean;
}

interface CreateCredentialFlowProps {
	open: boolean;
	onClose: () => void;
	/** Called once the credential has been successfully created. */
	onCreated: (info: CreatedCredentialInfo) => void;
	/**
	 * Which container the flow renders in. `'sheet'` (the default) is a side drawer,
	 * keeping the surface it was opened from on screen. `'dialog'` is for a host that
	 * is itself a native modal `<dialog>`: those render in the top layer above every
	 * sheet, so a drawer opened from inside one would be invisible.
	 */
	surface?: 'sheet' | 'dialog';
	/**
	 * Pre-select this auth type when the dialog opens (the user can still change
	 * it), e.g. OAuth 2.0 for registering a shared app. A spec-driven selection
	 * (picking an API) still overrides it — the API's declared security schemes
	 * are more authoritative than a host's default.
	 */
	initialType?: CredentialType;
	/**
	 * Skip the pick step and create a credential for exactly this API — the setup
	 * queue is working through a batch already chosen in the tray, so the API is not
	 * this flow's question to re-ask. Hides the picker, Back and `Change`; everything
	 * downstream behaves as for a picked API.
	 */
	pinnedApi?: SelectedApi;
	/**
	 * Open on the form (step 2) for this API — a host that is already about one
	 * API (its hub) shouldn't make the operator find it again. Unlike
	 * `pinnedApi` it is only a starting point: Back and `Change` still reach the
	 * picker. Each (re)open starts from it; a different API re-seeds the flow.
	 * `pinnedApi` wins when both are set.
	 *
	 * Like every pick, the credential defaults to "Any version"; the form's
	 * "Use for" picker offers this API's registered version to pin instead.
	 */
	initialApi?: SelectedApi;
	/**
	 * When provided, the flow opens directly into the vendor connect in
	 * "approve" mode — landing here from the `approval_url` an agent handed its
	 * owner. It fetches the session, skips the picker + agent selection, and
	 * shows the agent-requested scopes for the human to review + confirm.
	 */
	approvalSession?: { sessionId: string; pollToken: string };
	/**
	 * When set, the vendor connect opens with this agent locked in as the
	 * binding target (``VendorConnectFlow``'s ``preselectedAgentId`` greys the
	 * picker out). Used when the flow is opened from one agent's surface.
	 */
	preselectedAgentId?: string;
	/**
	 * Render-prop threaded through to ``VendorConnectFlow`` — callers supply the
	 * "bind to more agents" CTA (``PostConnectBindMore``); the flow stays
	 * agnostic of what the extra content is.
	 */
	renderPostConnect?: (info: PostConnectInfo) => ReactNode;
	/**
	 * A pinned flow's way back to where the host came from, shown as the form's
	 * leading footer action (e.g. "Back to APIs" in the setup queue). Called with
	 * whether the operator has typed anything, so the host can ask before
	 * discarding a draft — the flow itself does not decide what "back" costs.
	 */
	back?: { label: string; onBack: (dirty: boolean) => void };
	/**
	 * Register an org-shared OAuth app instead of creating a credential — the
	 * shared-apps section's "Register shared app" action. Off by default, so the
	 * flow is the plain credential create; only a host that mounts the
	 * shared-apps section turns it on. Pair with
	 * `initialType={CredentialType.OAUTH2}` so manual entry lands on OAuth 2.0.
	 */
	registerSharedApp?: boolean;
}

type Step = 'pick' | 'form' | 'vendor';

/** Grant types an org-shared OAuth app can sign people in through. */
function isShareableGrant(grantType: string): boolean {
	const g = grantType.trim();
	return g === 'authorization_code' || g === 'device_code';
}

/** The server keys a shared app to a `<domain>/<api>` catalog id. */
function isShareableCatalogId(apiId: string): boolean {
	return /^[^/].*\/.*[^/]$/.test(apiId);
}

/**
 * The guided flow for creating a credential.
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
 * The shell is the only thing `surface` switches: a drawer (default) or a centred
 * dialog for a host in the top layer. Everything between the header and the action
 * row is one implementation, so the two surfaces cannot drift.
 *
 * The drawer closes on a backdrop click exactly as it does on Escape / X / Cancel:
 * every close wipes the draft (see the reset effect — a half-typed secret is not
 * kept around). Two exceptions keep the backdrop inert:
 *  - a live vendor / approval connect (`step === 'vendor'`, `approvalSession`):
 *    `VendorConnectFlow` opens a connect session on mount and cancels it on
 *    unmount, so a stray click would silently abandon a sign-in in progress.
 *    Escape and the flow's own Cancel still close it deliberately.
 *  - the `dialog` surface: the native-`<dialog>` backdrop test in `Dialog` can't
 *    tell a drag that ends outside from a click, so it would close mid-select.
 */
export function CreateCredentialFlow({
	open,
	onClose,
	onCreated,
	initialType,
	pinnedApi,
	initialApi,
	surface = 'sheet',
	approvalSession,
	preselectedAgentId,
	renderPostConnect,
	back,
	registerSharedApp = false,
}: CreateCredentialFlowProps) {
	// The API the flow starts on: fixed (`pinnedApi`) or just preselected
	// (`initialApi`). Only `pinnedApi` hides the way back to the picker.
	const seedApi = pinnedApi ?? initialApi;
	// Every credential starts on "Any version"; a workspace API's registered
	// version is offered as the pin (a catalog pick has no real one yet).
	const seedVersion = seedApi ? pinnableVersionOf(seedApi) : '';
	const seedForm = (): CredentialFormState =>
		seedApi ? seedFormFromSelectedApi(baseForm, seedApi, false) : baseForm;
	const [step, setStep] = useState<Step>(seedApi ? 'form' : 'pick');
	const [selectedApi, setSelectedApi] = useState<SelectedApi | null>(seedApi ?? null);
	/** The registry version the form's "Use for" picker can pin to (`''` hides it). */
	const [pinnableVersion, setPinnableVersion] = useState(seedVersion);
	const [selectedVendor, setSelectedVendor] = useState<VendorSummary | null>(null);
	const [manualMode, setManualMode] = useState(false);
	/** Spec upload from the pick step — "the API isn't listed" is otherwise a dead end. */
	const [uploadOpen, setUploadOpen] = useState(false);
	const [type, setType] = useState<CredentialType>(initialType ?? CredentialType.BEARER_TOKEN);
	// Register mode submits to POST /oauth-app-registrations instead of
	// creating a personal credential, so anyone on the instance can sign in
	// through the resulting shared OAuth app.
	const registrationMutation = useCreateOAuthAppRegistration();
	/** When non-null, the spec drove the type (UI hides the manual toggle). */
	const [activeScheme, setActiveScheme] = useState<SchemeOption | null>(null);
	// The empty form's provider must fit the initial type — ``static`` is no
	// OAuth2 provider, and a spec with no OAuth scheme never re-derives it.
	const baseForm: CredentialFormState = {
		...EMPTY_FORM,
		provider: providerOptions(initialType ?? CredentialType.BEARER_TOKEN)[0].id,
	};
	/**
	 * The operator chose to set up authentication although the spec declares
	 * none — a spec can be wrong, so "no authentication" is a default, not a lock.
	 */
	const [authOverride, setAuthOverride] = useState(false);
	const [state, setState] = useState<CredentialFormState>(seedForm);
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
	/**
	 * Whether the operator has typed into the form. Set from the form's `input`
	 * events only, so spec seeding and scope auto-selection never count as edits.
	 */
	const formTouched = useRef(false);

	// Stable id prefix for wiring <Label htmlFor> to the manual-entry + name
	// controls (the shared Input/Select auto-generate ids, but a label can't
	// see those, so we own the ids here).
	const fieldId = useId();
	/** Names the drawer; the dialog surface derives its own from `title`. */
	const headingId = `${fieldId}-title`;

	const schemesResult = useApiSchemes(selectedApi);
	const createMutation = useCreateCredential();
	const importMutation = useImportCatalogEntry();
	const providersQuery = useProviders();
	const credentialsSource = useAllCredentials({ enabled: open });

	// A name another credential for this API already holds. Once saved, the new
	// credential is in the list itself and would clash with its own name while the
	// flow closes.
	const nameClash = useMemo(
		() =>
			createMutation.isSuccess
				? null
				: credentialNameClash(
						credentialsSource.items,
						{ vendor: state.apiVendor, name: state.apiName },
						state.name,
					),
		[
			createMutation.isSuccess,
			credentialsSource.items,
			state.apiVendor,
			state.apiName,
			state.name,
		],
	);

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

	// A spec that was read and declares no security scheme (and no global
	// requirement) is an API called without authentication — not an unknown
	// one. Offering a token for it would make the operator invent a secret the
	// API never checks; an unreadable spec still falls back to free choice.
	const declaresNoAuth =
		!manualMode &&
		!!selectedApi &&
		!schemesResult.loading &&
		!schemesResult.error &&
		schemesResult.spec != null &&
		specDeclaresNoAuth(schemesResult.spec);
	const noAuthDetected = declaresNoAuth && !authOverride;

	useEffect(() => {
		if (noAuthDetected) setType(CredentialType.NO_AUTH);
		else if (type === CredentialType.NO_AUTH)
			setType(initialType ?? CredentialType.BEARER_TOKEN);
		// `type` is read, not tracked: only a change in what the spec says moves it.
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [noAuthDetected]);

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
		// A pinned or preselected API is the caller's premise, not a user choice, so
		// a reset returns to that API's empty form rather than to the picker.
		setStep(seedApi ? 'form' : 'pick');
		setSelectedApi(seedApi ?? null);
		setSelectedVendor(null);
		setManualMode(false);
		setUploadOpen(false);
		setActiveScheme(null);
		setState(seedForm());
		setPinnableVersion(seedVersion);
		setErrors({});
		setServerVarErrors({});
		setOAuth2Flows([]);
		setActiveFlowId(null);
		hasUserInteractedWithScopes.current = false;
		nameDirty.current = false;
		formTouched.current = false;
		setAuthOverride(false);
		setType(initialType ?? CredentialType.BEARER_TOKEN);
		createMutation.reset();
		importMutation.reset();
		registrationMutation.reset();
	};

	// Closing the dialog must always reset internal state — otherwise reopening
	// would land mid-wizard with stale data. We do this with an effect rather
	// than via Dialog's `onAfterClose` since the shared Dialog primitive
	// doesn't expose a post-close hook today.
	useEffect(() => {
		if (!open) reset();
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [open]);

	// Seed-from-props: a host that stays mounted while its API changes (one hub
	// to another) re-seeds only when the preselected API itself changes — never
	// on an `open` flip, which the effect above already covers.
	const seedKey = seedApi ? `${seedApi.vendor}\u0000${seedApi.name}\u0000${seedApi.version}` : '';
	const lastSeedKey = useRef(seedKey);
	useEffect(() => {
		if (lastSeedKey.current === seedKey) return;
		lastSeedKey.current = seedKey;
		reset();
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [seedKey]);

	const handlePickApi = (api: SelectedApi): void => {
		setSelectedApi(api);
		setSelectedVendor(null);
		setManualMode(false);
		setAuthOverride(false);
		setPinnableVersion(pinnableVersionOf(api));
		setState((s) => seedFormFromSelectedApi(s, api, nameDirty.current));
		setStep('form');
	};

	/** A verified vendor is the one-click path: hand off to the vendor connect. */
	const handlePickVendor = (vendor: VendorSummary): void => {
		setSelectedVendor(vendor);
		setSelectedApi(null);
		setManualMode(false);
		setStep('vendor');
	};

	const handleManualEntry = (): void => {
		setSelectedApi(null);
		setSelectedVendor(null);
		setManualMode(true);
		setPinnableVersion('');
		setState({ ...EMPTY_FORM, provider: providerOptions(type)[0].id });
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

	// Non-blocking warning when the typed api_key field name diverges from the
	// spec's declared parameter name (a wrong binding causes upstream 401s —
	// #589). Only meaningful in spec mode with an apiKey scheme resolved.
	const fieldNameWarning = useMemo(() => {
		if (manualMode || type !== CredentialType.API_KEY || activeScheme == null) return undefined;
		const { fieldName: expected } = apiKeyFieldsFromScheme(
			schemesResult.schemes,
			activeScheme.name,
		);
		const typed = state.fieldName.trim();
		if (!expected || !typed || typed === expected) return undefined;
		return `The API spec expects "${expected}" — a different name will likely fail authentication.`;
	}, [manualMode, type, activeScheme, schemesResult.schemes, state.fieldName]);

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

	// Whether the picked API can back a shared OAuth app. Only the
	// direct_oauth2 path (admin brings their own client_id + endpoints) is
	// eligible — the managed Pipedream flow and the platform-shipped-vendor
	// path have no admin-owned OAuth app to share. A shared app is keyed to a
	// catalog API, so manual entry (no catalog id) can't be shared. A spec that
	// declares only non-interactive grants (e.g. client_credentials) has nothing
	// to sign in through; a spec with no OAuth flows at all lets the admin pick
	// the flow by hand.
	const specHasShareableFlow = oauth2Flows.some((f) => isShareableGrant(f.grantType));
	const canShareWithOrg =
		!manualMode &&
		!!selectedApi?.apiId &&
		isShareableCatalogId(selectedApi.apiId) &&
		type === CredentialType.OAUTH2 &&
		state.provider === 'direct_oauth2' &&
		(oauth2Flows.length === 0 || specHasShareableFlow);
	const sharing = registerSharedApp && canShareWithOrg;
	/** The spec's grant selector offers a non-shareable flow that's currently picked. */
	const sharingNeedsShareableGrant = sharing && !isShareableGrant(state.grantType);
	// Without spec flows there's no grant-type selector, so an empty grant type
	// would otherwise leave the shared app with no flow — default it.
	useEffect(() => {
		if (sharing && oauth2Flows.length === 0 && !isShareableGrant(state.grantType)) {
			setState((s) => ({ ...s, grantType: 'authorization_code' }));
		}
	}, [sharing, oauth2Flows.length, state.grantType]);

	// Register-mode submit path: creates a shared OAuth app registration instead of a personal credential.
	// Field mapping mirrors CredentialFormState onto the registration API:
	// authorize_url / token_url on auth-code, authorization_endpoint /
	// token_endpoint on device flow. Grant type discriminates flow_kind.
	const submitAsRegistration = async (): Promise<void> => {
		const grantType = state.grantType.trim();
		if (!isShareableGrant(grantType)) return;
		const scopes = state.scopes
			.split(/\s+/)
			.map((s) => s.trim())
			.filter(Boolean);
		const commonMissing: Partial<Record<keyof CredentialFormState, string>> = {};
		if (!state.name.trim()) commonMissing.name = 'Required for an org-shared registration.';
		if (!state.apiVendor.trim()) commonMissing.apiVendor = 'Required.';
		if (!state.clientId.trim()) commonMissing.clientId = 'Required.';
		if (!state.authorizeUrl.trim()) commonMissing.authorizeUrl = 'Required.';
		if (!state.tokenUrl.trim()) commonMissing.tokenUrl = 'Required.';
		if (grantType === 'authorization_code' && !state.clientSecret.trim()) {
			commonMissing.clientSecret = 'Required for authorization-code flow.';
		}
		if (Object.keys(commonMissing).length > 0) {
			setErrors(commonMissing);
			return;
		}
		// The catalog API id + family label ride off the picked API;
		// ``canShareWithOrg`` already required a shareable id, and an API that
		// lacks one is explained up front (``shareBlockedReason``).
		if (!selectedApi?.apiId) return;
		const catalogApiId = selectedApi.apiId;
		const displayName = selectedApi.label;

		try {
			if (grantType === 'authorization_code') {
				const body: AuthorizationCodeRegistrationCreateRequest = {
					name: state.name.trim(),
					api_vendor: state.apiVendor.trim(),
					catalog_api_id: catalogApiId,
					display_name: displayName,
					flow_kind: OAuthAppRegistrationFlowKind.AUTHORIZATION_CODE,
					client_id: state.clientId.trim(),
					client_secret: state.clientSecret,
					authorize_url: state.authorizeUrl.trim(),
					token_url: state.tokenUrl.trim(),
					default_scopes: scopes.length ? scopes : null,
				};
				await registrationMutation.mutateAsync(body);
			} else {
				const body: DeviceAuthorizationRegistrationCreateRequest = {
					name: state.name.trim(),
					api_vendor: state.apiVendor.trim(),
					catalog_api_id: catalogApiId,
					display_name: displayName,
					flow_kind: OAuthAppRegistrationFlowKind.DEVICE_AUTHORIZATION,
					client_id: state.clientId.trim(),
					// The dialog reuses `authorizeUrl` / `tokenUrl` as the device
					// flow's authorization + token endpoints (see the
					// device-code branch in CredentialTypeFields).
					authorization_endpoint: state.authorizeUrl.trim(),
					token_endpoint: state.tokenUrl.trim(),
					default_scopes: scopes.length ? scopes : null,
				};
				await registrationMutation.mutateAsync(body);
			}
			toast({
				title: 'Shared OAuth app registered',
				description: `${state.name.trim()} is now available to everyone in the organization.`,
				variant: 'success',
			});
			onClose();
		} catch {
			// registrationMutation.error surfaces below the form.
		}
	};

	const handleSubmit = async (e: React.FormEvent): Promise<void> => {
		e.preventDefault();
		const validation = validateCreate(type, state);
		setErrors(validation);
		const svErrors = validateServerVars(schemesResult.serverVars, state.serverVars);
		setServerVarErrors(svErrors);
		if (Object.keys(validation).length > 0 || Object.keys(svErrors).length > 0) return;

		if (sharing) {
			await submitAsRegistration();
			return;
		}

		const body = buildCreateBody(type, state);

		// Catalog APIs the user just picked may not be in the local registry
		// yet — fire the async import first. We wait only long enough to
		// surface a failure, not for the job to land.
		const importQueued =
			selectedApi?.source === 'catalog' && !selectedApi.registered && !!selectedApi.apiId;
		if (importQueued && selectedApi?.apiId) {
			try {
				await importMutation.mutateAsync(selectedApi.apiId);
			} catch {
				return;
			}
		}

		createMutation.mutate(body, {
			onSuccess: (data) => {
				const credName = state.name.trim();
				// The server warns when the credential's API scope matches no
				// imported API (executions through it would fail). Right after
				// queuing that API's import the warning is expected and is
				// settled server-side once the import lands, so only surface it
				// when no import is in flight.
				const warning = importQueued ? undefined : data.warnings?.[0];
				if (warning) {
					toast({
						title: 'Credential created — check its API scope',
						description: warning,
						variant: 'warning',
						durationMs: 12000,
					});
				} else {
					toast({
						title: 'Credential created',
						description: credName ? `${credName} is ready to use.` : undefined,
						variant: 'success',
					});
				}
				onCreated({
					credentialId: data.credential.credential_id,
					// The server's stored label, not the draft field: an empty Name is
					// filled in by the backend's default.
					name: data.credential.name,
					type,
					provider: state.provider,
					// User-interactive grants (authorization_code, device_code) need a
					// connect flow before they're usable. client_credentials and other
					// non-interactive grants must not auto-connect.
					needsConnect:
						state.authorizeUrl.trim().length > 0 ||
						state.grantType.trim() === 'authorization_code' ||
						state.grantType.trim() === 'device_code',
				});
				// Closing the dialog triggers the open-watching effect which
				// resets state — no need to call reset() here directly.
			},
		});
	};

	// The picked-API summary banner shown atop the form step (label + the
	// vendor/name triple + whether saving will trigger a catalog import). The
	// version shown is the one the credential is saved with: every pick starts
	// unpinned (`apiVersion: ''`, see `seedFormFromSelectedApi`), so it reads
	// "any version" until the operator pins the API's version in "Use for".
	const pinnedVersion = state.apiVersion.trim();
	const apiSummary = useMemo(() => {
		if (!selectedApi) return null;
		const triple = `${selectedApi.vendor}/${selectedApi.name}${pinnedVersion ? `@${pinnedVersion}` : ' · any version'}`;
		const willImport = selectedApi.source === 'catalog' && !selectedApi.registered;
		return { label: selectedApi.label, triple, willImport };
	}, [selectedApi, pinnedVersion]);

	const showManualType = manualMode || activeScheme == null || activeScheme.type === 'unknown';
	const usingPipedream = isOAuth2 && state.provider === 'pipedream';

	// Types to offer in the auth cards:
	//  - manual / unknown spec → all four (free choice)
	//  - spec-driven → the distinct types the schemes declared (often one),
	//    deduped & in canonical order; we still render the single card so the
	//    user sees what was detected and can confirm.
	const typeOptions = useMemo<CredentialType[]>(() => {
		if (noAuthDetected) return [CredentialType.NO_AUTH];
		if (showManualType) return [...CREDENTIAL_TYPE_ORDER];
		const fromSpec = schemesResult.options
			.map((o) => schemeTypeToCredentialType(o.type))
			.filter((t): t is CredentialType => t != null);
		const deduped = CREDENTIAL_TYPE_ORDER.filter((t) => fromSpec.includes(t));
		return deduped.length > 0 ? deduped : [type];
	}, [noAuthDetected, showManualType, schemesResult.options, type]);

	const detectedSingle = !showManualType && typeOptions.length === 1;

	const serverVars = manualMode ? [] : schemesResult.serverVars;

	// While a picked API's spec is still being fetched we don't yet know the
	// auth type — so we hold back the type selector + credential fields and
	// show a skeleton in their place (mirrors jentic-webapp, which gates the
	// auth UI behind `!isLoadingSchemes`). Manual mode has no spec to wait on.
	const specPending = !manualMode && !!selectedApi && schemesResult.loading;

	// In register mode the picked API either can be shared, or we say why it
	// can't — the flow never quietly falls back to creating a personal credential.
	const shareBlockedReason = ((): string | null => {
		if (!registerSharedApp || step !== 'form' || specPending || canShareWithOrg) return null;
		if (manualMode || !selectedApi?.apiId) {
			return 'Shared OAuth apps are registered against a catalog API. Go back and pick the API to share an app for.';
		}
		if (!isShareableCatalogId(selectedApi.apiId)) {
			return `Shared OAuth apps need a catalog API id of the form <domain>/<api> — "${selectedApi.apiId}" can't be shared yet.`;
		}
		if (type !== CredentialType.OAUTH2) {
			return typeOptions.includes(CredentialType.OAUTH2)
				? 'Shared apps sign people in with OAuth 2.0 — pick OAuth 2.0 below.'
				: `${selectedApi.label} doesn't offer OAuth 2.0 sign-in, so it can't be registered as a shared app.`;
		}
		if (state.provider !== 'direct_oauth2') {
			return 'Shared apps use your own OAuth client — pick the direct provider below.';
		}
		return `${selectedApi.label} only offers non-interactive OAuth grants (such as client credentials). Shared apps need an authorization code or device code flow.`;
	})();
	const titleSuffix = selectedApi?.label ? ` — ${selectedApi.label}` : '';
	const title = approvalSession
		? 'Approve integration'
		: step === 'pick'
			? registerSharedApp
				? 'Register shared OAuth app'
				: 'Add credential'
			: step === 'vendor' && selectedVendor
				? `Connect ${selectedVendor.display_name}`
				: registerSharedApp
					? `Register shared OAuth app${titleSuffix}`
					: `Add credential${titleSuffix}`;
	const formStepHint = registerSharedApp
		? 'Fill in the shared app details'
		: 'Fill in the credential details';
	// A pinned API removes the pick step, so the step counter would be lying. The
	// vendor path is its own two-step flow, so it drops the counter too.
	const subtitle = approvalSession ? (
		<span>An agent is asking to connect on your behalf.</span>
	) : step === 'vendor' ? (
		<span>Pick an agent and the access it needs.</span>
	) : pinnedApi ? (
		<span>
			{formStepHint} for {pinnedApi.label}
		</span>
	) : step === 'pick' ? (
		<span>
			<span className="text-[10.5px] font-bold tracking-[0.08em] uppercase">Step 1 of 2</span>{' '}
			·{' '}
			{registerSharedApp
				? 'Pick the API the shared app signs in to'
				: 'Choose a one-click sign-in, or pick an API to authenticate against'}
		</span>
	) : (
		<span>
			<span className="text-[10.5px] font-bold tracking-[0.08em] uppercase">Step 2 of 2</span>{' '}
			· {formStepHint}
		</span>
	);

	const goBackToPick = (): void => {
		setStep('pick');
		setErrors({});
	};

	const openUpload = (): void => setUploadOpen(true);

	// A successful upload is a pick: straight on to the form for that API. A
	// result that couldn't be read leaves the picker, whose list the import
	// refreshed, so the API is one search away.
	const handleImported = (apis: SelectedApi[]): void => {
		if (apis[0]) handlePickApi(apis[0]);
	};

	// Approve mode and the vendor step render VendorConnectFlow's own inline
	// action bar, so the flow's footer stands down for both.
	const footer =
		approvalSession || step === 'vendor' ? undefined : step === 'pick' ? (
			// Always reachable, not only from no-results: an operator who knows the
			// API isn't catalogued shouldn't have to search first.
			<Button
				variant="ghost"
				size="sm"
				onClick={openUpload}
				type="button"
				className="mr-auto"
			>
				<Upload className="h-3.5 w-3.5" />
				Upload an API
			</Button>
		) : step === 'form' ? (
			<>
				{pinnedApi && back && (
					<Button
						variant="secondary"
						onClick={(): void => back.onBack(formTouched.current)}
						disabled={createMutation.isPending || importMutation.isPending}
						type="button"
						className="mr-auto"
					>
						<ArrowLeft className="h-4 w-4" />
						{back.label}
					</Button>
				)}
				{!pinnedApi && (
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
				)}
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
					loading={
						createMutation.isPending ||
						importMutation.isPending ||
						registrationMutation.isPending
					}
					disabled={
						specPending || sharingNeedsShareableGrant || shareBlockedReason != null
					}
				>
					{registerSharedApp
						? 'Register shared app'
						: noAuthDetected
							? 'Add without a secret'
							: 'Create credential'}
				</Button>
			</>
		) : undefined;

	const body = approvalSession ? (
		<VendorConnectFlow
			mode="approve"
			sessionId={approvalSession.sessionId}
			pollToken={approvalSession.pollToken}
			renderPostConnect={renderPostConnect}
			onBack={onClose}
			onDone={onClose}
		/>
	) : (
		<>
			{step === 'pick' && (
				<ApiPicker
					onSelect={handlePickApi}
					// One-click sign-in tiles connect a personal credential, which
					// isn't what "Register shared app" is for.
					onVendorSelect={registerSharedApp ? undefined : handlePickVendor}
					onManualEntry={handleManualEntry}
					emptyAction={
						<Button variant="secondary" size="sm" onClick={openUpload} type="button">
							<Upload className="h-4 w-4" />
							Upload an API
						</Button>
					}
				/>
			)}

			{step === 'vendor' && selectedVendor && (
				<VendorConnectFlow
					mode="self"
					vendor={selectedVendor}
					preselectedAgentId={preselectedAgentId}
					renderPostConnect={renderPostConnect}
					onBack={goBackToPick}
					onDone={onClose}
				/>
			)}

			{/* Mounted across steps so its close survives the jump to the form. A
			    native `<dialog>` renders in the top layer, over this flow. */}
			<ImportSpecDialog
				open={uploadOpen}
				onClose={(): void => setUploadOpen(false)}
				onImported={handleImported}
			/>

			{step === 'form' && (
				<form
					id="create-credential-form"
					onSubmit={handleSubmit}
					onInput={(): void => {
						formTouched.current = true;
					}}
					className="space-y-5"
				>
					{sharing && (
						<div className="bg-muted/40 border-border space-y-3 rounded-lg border p-3">
							<div className="text-sm">
								<p className="text-foreground flex items-center gap-1.5 font-medium">
									<Users className="h-3.5 w-3.5" />
									Shared OAuth app
								</p>
								<p className="text-muted-foreground mt-0.5 text-xs leading-snug">
									Everyone in the organization can connect through this app. Each
									person signs in with their own account — tokens are never
									shared.
								</p>
							</div>
							{oauth2Flows.length === 0 && (
								<div className="space-y-1.5">
									<Label htmlFor={`${fieldId}-share-flow`}>Sign-in flow</Label>
									<Select
										id={`${fieldId}-share-flow`}
										value={state.grantType}
										onChange={(e): void => patch({ grantType: e.target.value })}
									>
										<option value="authorization_code">
											Authorization code (browser redirect)
										</option>
										<option value="device_code">
											Device code (enter a code)
										</option>
									</Select>
								</div>
							)}
							{sharingNeedsShareableGrant && (
								<p className="text-danger text-xs" role="alert">
									Shared apps sign people in interactively — pick an authorization
									code or device code grant type below.
								</p>
							)}
						</div>
					)}

					{shareBlockedReason && (
						<p
							className="border-border bg-muted/40 text-muted-foreground rounded-lg border p-3 text-xs leading-snug"
							role="note"
						>
							{shareBlockedReason}
						</p>
					)}

					{apiSummary && (
						<div
							className="bg-surface-inset flex items-center gap-3 rounded-lg px-3 py-2.5"
							data-testid="selected-api-summary"
						>
							<div className="min-w-0 flex-1">
								<div className="flex items-center gap-2">
									<p className="text-foreground-name truncate text-sm font-semibold">
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
										<span className="text-muted-foreground inline-flex items-center gap-1">
											<span aria-hidden>·</span>
											<Download className="h-3 w-3" />
											imports on save
										</span>
									)}
								</p>
								{pinnableVersion && (
									<div className="mt-2">
										<CredentialVersionScope
											version={pinnableVersion}
											value={pinnedVersion ? 'pinned' : 'any'}
											onChange={(scope): void =>
												patch({
													apiVersion:
														scope === 'pinned' ? pinnableVersion : '',
												})
											}
										/>
									</div>
								)}
							</div>
							{!pinnedApi && (
								<Button
									type="button"
									variant="ghost"
									size="xs"
									onClick={goBackToPick}
									className="shrink-0"
								>
									Change
								</Button>
							)}
						</div>
					)}

					{manualMode && (
						<fieldset className="bg-surface-inset space-y-3 rounded-lg p-3">
							<legend className="text-foreground-sub float-left mb-3 w-full text-xs font-medium">
								API reference
							</legend>
							<div className="clear-both space-y-1.5">
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
										placeholder="Any version"
									/>
								</div>
							</div>
						</fieldset>
					)}

					<div className="space-y-2">
						<FormSectionLabel>
							{sharing ? 'Registration details' : 'Credential details'}
						</FormSectionLabel>
						<div className="space-y-1.5">
							<Label htmlFor={`${fieldId}-name`} required>
								{sharing ? 'Registration name' : 'Name'}
							</Label>
							<Input
								id={`${fieldId}-name`}
								value={state.name}
								onChange={(e): void => {
									nameDirty.current = true;
									patch({ name: e.target.value });
								}}
								placeholder={sharing ? 'MyOrg Google' : 'Production API key'}
								error={errors.name}
								aria-describedby={nameClash ? `${fieldId}-name-clash` : undefined}
							/>
							{nameClash ? (
								<CredentialNameClashNote
									id={`${fieldId}-name-clash`}
									{...nameClash}
									onUseSuggestion={(name): void => {
										nameDirty.current = true;
										patch({ name });
									}}
								/>
							) : (
								<p className="text-muted-foreground text-xs">
									{sharing
										? "Admin-facing label for this shared OAuth app (e.g. 'MyOrg Google')."
										: 'A label to recognise this credential later.'}
								</p>
							)}
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
								className="border-hairline border-t pt-5"
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
								className="border-hairline space-y-5 border-t pt-5"
							>
								{!manualMode && schemesResult.error && (
									<p
										className="bg-surface-inset text-foreground-sub rounded-lg p-3 text-xs leading-snug"
										role="note"
									>
										We couldn&apos;t read the API spec — pick the type manually
										below.
									</p>
								)}

								{noAuthDetected ? (
									<NoAuthNote
										onSetUpAnyway={(): void => {
											setAuthOverride(true);
											handleTypeChange(
												initialType ?? CredentialType.BEARER_TOKEN,
											);
										}}
									/>
								) : (
									<AuthTypeCards
										options={typeOptions}
										value={type}
										onChange={handleTypeChange}
										detected={detectedSingle}
									/>
								)}

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
										fieldNameWarning={fieldNameWarning}
									/>
								</div>

								{usingPipedream && (
									<div
										className="bg-primary/5 text-primary/90 flex items-start gap-2 rounded-lg p-3 text-xs leading-snug"
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

					{registrationMutation.isError && (
						<ErrorAlert message={registrationMutation.error} />
					)}
				</form>
			)}
		</>
	);

	/** A connect session is open (created on `VendorConnectFlow` mount). */
	const connectInPlay = !!approvalSession || step === 'vendor';

	if (surface === 'dialog') {
		return (
			<Dialog
				open={open}
				onClose={onClose}
				title={title}
				subtitle={subtitle}
				size={approvalSession || step !== 'form' ? 'lg' : 'xl'}
				footer={footer}
				dismissOnBackdrop={false}
			>
				{body}
			</Dialog>
		);
	}

	return (
		<SheetPrimitive
			open={open}
			onClose={onClose}
			ariaLabelledBy={headingId}
			// Same close as Escape / X (see the doc comment); inert only while a
			// vendor / approval connect session is live.
			dismissOnBackdrop={!connectInPlay}
			className="sm:w-[640px] xl:w-[760px]"
		>
			<div className="flex h-full flex-col">
				<SheetHeader className="justify-between">
					<div className="min-w-0">
						<h2
							id={headingId}
							className="font-heading text-foreground-name text-lg leading-tight font-semibold"
						>
							{title}
						</h2>
						<div className="text-foreground-sub mt-1 text-xs">{subtitle}</div>
					</div>
					<Button
						variant="ghost"
						size="icon"
						aria-label="Close"
						onClick={onClose}
						className="-mt-1 -mr-1.5 shrink-0"
					>
						<X className="h-4 w-4" />
					</Button>
				</SheetHeader>

				{/* The connect flow draws its own action band flush with the body's
				    bottom edge, so the body keeps a matching bottom padding then. */}
				<SheetBody className={connectInPlay ? 'pb-4' : undefined}>{body}</SheetBody>

				{footer && <SheetFooter className="flex-wrap gap-2">{footer}</SheetFooter>}
			</div>
		</SheetPrimitive>
	);
}

/**
 * Small uppercase section heading used to give the form a clear visual
 * ordering ("Credential details" → "Authentication"). Mirrors the section
 * headings used in the API picker so the wizard feels of-a-piece.
 */
function FormSectionLabel({ children }: { children: React.ReactNode }) {
	return (
		<p className="text-foreground-faint px-0.5 text-[10.5px] font-bold tracking-[0.08em] uppercase">
			{children}
		</p>
	);
}

/**
 * The auth section for an API whose spec declares no authentication. States
 * only what is known — the spec declares none, not that the API needs none —
 * says what saving makes (a credential with no secret, for binding), that
 * agents still need access granted, and offers the method picker for a spec
 * that leaves its auth out.
 */
function NoAuthNote({ onSetUpAnyway }: { onSetUpAnyway: () => void }) {
	return (
		<div
			className="bg-surface-tonal flex items-start gap-3 rounded-lg p-3.5"
			data-testid="credential-no-auth-note"
		>
			<span
				aria-hidden="true"
				className="bg-surface-field text-foreground-sub grid h-8 w-8 shrink-0 place-items-center rounded-full"
			>
				<LockOpen className="h-4 w-4" />
			</span>
			<div className="min-w-0 flex-1">
				<p className="text-foreground text-sm font-medium">No authentication declared</p>
				<p className="text-muted-foreground mt-0.5 text-xs leading-snug">
					This API&apos;s spec lists no way to sign in, so there&apos;s no secret to
					enter. Saving adds a credential without one, which you can then bind to agents —
					they still need access granted before they can call it.
				</p>
				<p className="text-muted-foreground mt-2.5 text-xs">
					Does the API actually need a key or token?{' '}
					<button
						type="button"
						onClick={onSetUpAnyway}
						className="text-primary rounded-sm font-semibold underline-offset-2 hover:underline focus-visible:underline focus-visible:outline-none"
						data-testid="credential-no-auth-override"
					>
						Choose an authentication method
					</button>
				</p>
			</div>
		</div>
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
					<Skeleton className="h-[4.5rem] rounded-lg" />
					<Skeleton className="h-[4.5rem] rounded-lg" />
				</div>
			</div>
			{/* Field stack */}
			<div className="space-y-4">
				<div className="space-y-1.5">
					<Skeleton className="h-3.5 w-20" />
					<Skeleton className="rounded-field h-9 w-full" />
				</div>
				<div className="space-y-1.5">
					<Skeleton className="h-3.5 w-24" />
					<Skeleton className="rounded-field h-9 w-full" />
				</div>
			</div>
		</div>
	);
}
