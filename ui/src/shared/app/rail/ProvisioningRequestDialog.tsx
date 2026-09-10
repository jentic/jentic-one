/**
 * ProvisioningRequestDialog — fulfil a provisioning-plan access request.
 *
 * A `--provision` request is an ENVELOPE describing the whole path to first
 * execution rather than a single last-mile binding (see `provisioningPlan.ts`):
 *
 *   credential:provision  — create a credential for the API   (Step 1, auto for no-auth)
 *   credential:bind       — bind the AGENT to that credential + permission rules
 *
 * The `provision` item is an inert placeholder; a human fulfils it here by
 * CREATING the real credential (reusing the shared CreateCredentialDialog),
 * then this wizard AMENDs the resulting credential id + confirmed rules onto
 * the `credential:bind` item and APPROVES the whole request — the existing
 * bind effect does the real wiring (agent ↔ credential + rules).
 *
 * Orphans: the credential is created before approval, so an abandoned
 * fulfilment can leave real objects. The wizard tracks what it created this
 * session and offers to discard them on cancel (client-side, best-effort).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
	ArrowLeft,
	ArrowRight,
	AlertTriangle,
	CheckCircle2,
	KeyRound,
	MessageSquare,
	ShieldCheck,
	Trash2,
	XCircle,
} from 'lucide-react';
import { Dialog } from '@/shared/ui/Dialog';
import { Button } from '@/shared/ui/Button';
import { Label } from '@/shared/ui/Label';
import { Select } from '@/shared/ui/Select';
import { Badge } from '@/shared/ui/Badge';
import { ActorLabel } from '@/shared/ui/ActorLabel';
import { AgentBadge } from '@/shared/ui/AgentBadge';
import { ErrorAlert } from '@/shared/ui/ErrorAlert';
import { PermissionRuleEditor, type PermissionRuleInput } from '@/shared/ui/PermissionRuleEditor';
import { useActorDirectory } from '@/shared/hooks';
import { CreateCredentialDialog } from '@/shared/credentials/components/CreateCredentialDialog';
import type { CreatedCredentialInfo } from '@/shared/credentials/components/CreateCredentialDialog';
import { CREDENTIAL_TYPE_LABELS, runConnectFlow } from '@/shared/credentials/api';
import {
	CredentialType,
	AgentsService,
	CredentialsService,
	getToken,
	subscribeToken,
	type CredentialRedactedResponse,
} from '@/shared/api';
import {
	amendAccessRequest,
	decideAccessRequest,
	getAccessRequest,
	parseItemRules,
	ruleSummary,
	type AccessRequest,
	type AccessRequestItem,
	type ItemAmendment,
	type ItemDecision,
} from '@/shared/lib/accessRequests';
import {
	chainAuthType,
	chainIsNoAuth,
	chainItems,
	isPlanGranted,
	planChains,
	planDenialReason,
	slugifyApiField,
	type PlanApiReference,
	type PlanChain,
} from '@/shared/lib/provisioningPlan';
import { createNoAuthCredential, discardPlanCredential } from '@/shared/lib/provisioningFulfilment';

type Step = 'credential' | 'rules' | 'review' | 'done';
type Outcome = 'granted' | 'error';

/** Per-chain fulfilment progress — one entry per provisioning chain. */
interface ChainProgress {
	/** The chain's `PlanChain.key` — aligns restored drafts to the right API. */
	key: string;
	credentialId: string | null;
	credentialType: string | null;
	/** Display name of an adopted credential (created ones show their type). */
	credentialName: string | null;
	/** The credential was adopted from existing credentials — no connect flow,
	 * excluded from orphan discard on cancel. */
	credentialAdopted: boolean;
	/** The adopted credential was never connected (OAuth `connected === false`
	 * at adoption time) — keeps the adopted-state panel warning instead of
	 * declaring a working reuse. */
	credentialUnconnected: boolean;
	rules: PermissionRuleInput[];
	/** Operator chose not to set this API up now; its items are denied at submit. */
	skipped: boolean;
}

/** The wizard's resumable progress for one request. */
interface WizardDraft {
	step: Step;
	chainIndex: number;
	chains: ChainProgress[];
}

/**
 * Session-scoped drafts keyed by request id. The wizard's only production
 * mount path (AccessRequestDecisionDialog) UNMOUNTS it on close, so component
 * state alone would evaporate on "Keep & finish later" — reopening would then
 * create a SECOND credential, accumulating exactly the orphans the discard
 * flow exists to prevent. Backed by sessionStorage (best-effort, per-tab,
 * gone when the tab closes): the OAuth connect flow can fall back to a
 * SAME-TAB redirect when the popup is blocked, and a module-scoped map would
 * not survive that full-page navigation — every chain's created credential id
 * would be lost mid-fulfilment.
 *
 * The `.v2` suffix retires drafts from the 4-step toolkit era: their step
 * names and chain fields no longer exist, so restoring one would misroute the
 * wizard. Old-key entries simply expire with the session.
 */
const DRAFTS_STORAGE_KEY = 'jentic.provisioningWizardDrafts.v2';

function loadDrafts(): Map<string, WizardDraft> {
	try {
		const raw = sessionStorage.getItem(DRAFTS_STORAGE_KEY);
		if (!raw) return new Map();
		return new Map(Object.entries(JSON.parse(raw) as Record<string, WizardDraft>));
	} catch {
		return new Map();
	}
}

function saveDrafts(drafts: Map<string, WizardDraft>): void {
	try {
		if (drafts.size === 0) {
			sessionStorage.removeItem(DRAFTS_STORAGE_KEY);
			return;
		}
		sessionStorage.setItem(DRAFTS_STORAGE_KEY, JSON.stringify(Object.fromEntries(drafts)));
	} catch {
		// Quota/privacy-mode failures degrade to in-memory-only drafts.
	}
}

const drafts = loadDrafts();

const wizardDrafts = {
	get(id: string): WizardDraft | undefined {
		return drafts.get(id);
	},
	set(id: string, draft: WizardDraft): void {
		drafts.set(id, draft);
		saveDrafts(drafts);
	},
	delete(id: string): void {
		if (drafts.delete(id)) saveDrafts(drafts);
	},
	clear(): void {
		drafts.clear();
		saveDrafts(drafts);
	},
};

// Drafts must not outlive the operator session: on a shared workstation the
// next sign-in would otherwise resume the previous operator's wizard (step,
// rule edits, created-object ids). The token store is the session boundary.
subscribeToken(() => {
	if (getToken() === null) wizardDrafts.clear();
});

/** Test-only: drop all drafts so cases sharing request fixtures stay isolated. */
export function resetProvisioningWizardDrafts(): void {
	wizardDrafts.clear();
}

export interface ProvisioningRequestDialogProps {
	open: boolean;
	request: AccessRequest;
	onClose: () => void;
	/** Called after a successful approval so the caller can refresh its list. */
	onFulfilled?: () => void;
}

function apiLabel(ref: PlanApiReference | null): string {
	if (!ref) return 'this API';
	const base = [ref.vendor, ref.name].filter(Boolean).join('/');
	return ref.version ? `${base}@${ref.version}` : base;
}

/**
 * Map the agent-declared `--auth` value carried on the plan's
 * `credential:provision` item (`security_scheme`, e.g. "bearer") to the
 * credential form's {@link CredentialType} so the form opens pre-selected.
 * Returns undefined for an unknown/absent scheme (form falls back to its
 * default), so a bad agent value never breaks the wizard.
 */
function authTypeToCredentialType(auth: string | null): CredentialType | undefined {
	switch (auth) {
		case 'bearer':
			return CredentialType.BEARER_TOKEN;
		case 'api_key':
			return CredentialType.API_KEY;
		case 'basic':
			return CredentialType.BASIC;
		case 'oauth2':
			return CredentialType.OAUTH2;
		default:
			return undefined;
	}
}

/** Seed the rule editor from the agent's proposed rules on a chain's bind item. */
function proposedChainRules(chain: PlanChain): PermissionRuleInput[] {
	if (!chain.credentialBind) return [];
	return parseItemRules(chain.credentialBind).map((r) => ({
		// The editor only authors allow/deny; collapse the rare require-approval.
		effect: (r.effect === 'require-approval'
			? 'deny'
			: r.effect) as PermissionRuleInput['effect'],
		methods: r.methods ?? null,
		path: r.path ?? null,
		operations: r.operations ?? null,
	}));
}

/**
 * The shared rule set attached to a chain's bind item, if any. When present it
 * — not inline rules — is the binding's effective policy (they are mutually
 * exclusive), so the wizard neither edits nor amends rules for the chain: it
 * preserves the pointer and shows it read-only.
 */
function chainRuleSetId(chain: PlanChain): string | null {
	return chain.credentialBind?.rule_set_id ?? null;
}

/**
 * A chain the wizard cannot wire: without a `credential:bind` item there is
 * nothing to amend the created credential onto, so walking the operator
 * through creating one would throw that work away. Such chains (only
 * reachable via raw-API-filed requests) are seeded as skipped, locked, and
 * denied at submit with an explicit reason.
 */
function chainUnfulfillable(chain: PlanChain): boolean {
	return chain.credentialBind === undefined;
}

/** Fresh (untouched) progress for each of a request's chains. */
function seedChainProgress(chains: PlanChain[]): ChainProgress[] {
	return chains.map((chain) => ({
		key: chain.key,
		credentialId: null,
		credentialType: null,
		credentialName: null,
		credentialAdopted: false,
		credentialUnconnected: false,
		rules: proposedChainRules(chain),
		skipped: chainUnfulfillable(chain),
	}));
}

/** The first interactive step for a chain (no-auth chains have no credential step). */
function chainFirstStep(chain: PlanChain | undefined): Step {
	return chain !== undefined && !chainIsNoAuth(chain) ? 'credential' : 'rules';
}

export function ProvisioningRequestDialog({
	open,
	request,
	onClose,
	onFulfilled,
}: ProvisioningRequestDialogProps) {
	// The request split into provisioning chains (one per API) + plain extras
	// (binds to existing credentials, scope grants) decided alongside the chains.
	const shape = useMemo(() => planChains(request), [request]);
	const chains = shape.chains;

	// The requesting agent's display name, resolved from the actor directory.
	// Feeds the header badge. Resolves asynchronously; undefined until the
	// directory query lands or when the id is unknown.
	const directory = useActorDirectory();
	const directoryName = directory.resolve(request.actor_id);

	// Directory miss fallback: the directory is cached reference data, and the
	// normal CLI flow registers the agent SECONDS before this dialog opens — a
	// cached-before-registration directory misses it, which would leave the
	// raw `agnt_…` id in the header. Only once the directory has loaded AND
	// missed, fetch the agent directly by id (keyed to the id so a stale name
	// can never leak across requests).
	const [fetched, setFetched] = useState<{ id: string; name: string } | null>(null);
	const fetchedAgentName = fetched?.id === request.actor_id ? fetched.name : undefined;
	useEffect(() => {
		if (directoryName !== undefined || directory.isLoading) return;
		if (!request.actor_id.startsWith('agnt_')) return;
		if (fetched?.id === request.actor_id) return;
		let cancelled = false;
		void AgentsService.getAgent({ agentId: request.actor_id })
			.then((agent) => {
				if (!cancelled && agent.name) {
					setFetched({ id: request.actor_id, name: agent.name });
				}
			})
			.catch(() => {
				// Best-effort: the directory (or its invalidation on the live
				// agent event) remains the primary path; a failed lookup just
				// keeps the id fallback. No state write, so no retry loop.
			});
		return () => {
			cancelled = true;
		};
	}, [request.actor_id, directoryName, directory.isLoading, fetched]);

	const agentName = directoryName ?? fetchedAgentName;

	// Draft-aware initial state: a reopened request resumes where it left off
	// (see `wizardDrafts`). Lazy initializers so the FIRST render already
	// carries the draft — a post-mount restore effect would race the
	// draft-save effect below and clobber the draft with defaults. Draft
	// chains are realigned to the CURRENT chain order by `PlanChain.key` —
	// item order isn't guaranteed server-side, so a positional restore could
	// apply one API's credential/rules to another API's chain. A draft whose
	// keys no longer match (the request was amended elsewhere) is discarded
	// rather than mis-applied.
	const draftFor = (id: string): WizardDraft | undefined => {
		const draft = wizardDrafts.get(id);
		if (!draft || draft.chains.length !== chains.length) return undefined;
		const byKey = new Map(draft.chains.map((cs) => [cs.key, cs]));
		const aligned: ChainProgress[] = [];
		for (const chain of chains) {
			const cs = byKey.get(chain.key);
			if (!cs) return undefined;
			aligned.push(cs);
		}
		return { ...draft, chains: aligned };
	};
	const [step, setStep] = useState<Step>(
		() => draftFor(request.id)?.step ?? chainFirstStep(chains[0]),
	);
	const [chainIndex, setChainIndex] = useState(() => draftFor(request.id)?.chainIndex ?? 0);
	const [chainStates, setChainStates] = useState<ChainProgress[]>(
		() => draftFor(request.id)?.chains ?? seedChainProgress(chains),
	);
	// Set once the operator mutates anything, gating draft persistence — a
	// pristine peek at a plan should not accumulate map entries.
	const [touched, setTouched] = useState(() => draftFor(request.id) !== undefined);
	const [credentialDialogOpen, setCredentialDialogOpen] = useState(false);
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const [outcome, setOutcome] = useState<Outcome | null>(null);
	// In-dialog "discard orphaned credential?" confirmation shown when
	// cancelling a partially-fulfilled wizard (replaces a browser confirm()).
	const [confirmDiscard, setConfirmDiscard] = useState(false);
	const [discarding, setDiscarding] = useState(false);
	// The request re-fetched on open. Callers pass a possibly-stale snapshot
	// from the list query; before showing the LIVE create/approve controls we
	// confirm the request is still pending, so an operator can't re-fulfil a
	// request that was decided/expired since the list was fetched (which would
	// strand a real credential then fail at decide). The fresh copy also
	// carries the single-GET `already_satisfied` enrichment feeding the
	// "already in place" hints. Null until the fetch resolves; the terminal
	// gate falls back to the snapshot.
	const [freshRequest, setFreshRequest] = useState<AccessRequest | null>(null);

	// Transient flags reset on every (re)open; the draft (created ids, rules)
	// persists between dismissals so a peek doesn't discard fulfilment progress.
	// If a prior submit ended on the `done` screen with an ERROR (the request is
	// still pending — the decide failed), reopening should return the operator to
	// the review step to retry rather than stranding them on the error screen. A
	// GRANTED done screen is left as-is (the request is now decided; the terminal
	// gate re-routes it to the read-only summary on the next open anyway).
	useEffect(() => {
		if (!open) return;
		setBusy(false);
		setError(null);
		setStep((prev) => (prev === 'done' && outcome === 'error' ? 'review' : prev));
		if (outcome === 'error') setOutcome(null);
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [open]);

	// Confirm the request is still pending on open (the snapshot may be stale).
	// A superseded status flips the UI to the read-only terminal summary before
	// the operator can run create→amend→decide against a settled request.
	useEffect(() => {
		if (!open) return;
		let cancelled = false;
		void getAccessRequest(request.id)
			.then((fresh) => {
				if (!cancelled) setFreshRequest(fresh);
			})
			.catch(() => {
				// Best-effort: on a fetch failure keep the snapshot status; the
				// decide step still re-fetches and will surface any real error.
			});
		return () => {
			cancelled = true;
		};
	}, [open, request.id]);

	// Re-seed when the request id CHANGES while mounted (list-page reuse). The
	// initial mount is already seeded by the lazy `useState` initializers.
	const lastSeededId = useRef(request.id);
	useEffect(() => {
		if (lastSeededId.current === request.id) return;
		lastSeededId.current = request.id;
		const draft = draftFor(request.id);
		setStep(draft?.step ?? chainFirstStep(chains[0]));
		setChainIndex(draft?.chainIndex ?? 0);
		setChainStates(draft?.chains ?? seedChainProgress(chains));
		setTouched(draft !== undefined);
		setOutcome(null);
		setFreshRequest(null);
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [request.id]);

	// Persist the draft on every change so "Keep & finish later" genuinely
	// keeps progress across an unmount (see `wizardDrafts`). A terminal
	// outcome (granted OR denied) means the request was decided — stop
	// saving; the submit path already deleted the draft. A PRISTINE state
	// (nothing created, nothing edited, still on step 1) is not worth
	// resuming — store nothing, so merely peeking at plans doesn't
	// accumulate map entries for the rest of the session.
	const effectiveStatus = freshRequest?.status ?? request.status;
	useEffect(() => {
		if (outcome !== null || effectiveStatus !== 'pending') return;
		if (!touched) {
			wizardDrafts.delete(request.id);
			return;
		}
		wizardDrafts.set(request.id, {
			step: step === 'done' ? 'review' : step,
			chainIndex,
			chains: chainStates,
		});
	}, [request.id, step, chainIndex, chainStates, touched, outcome, effectiveStatus]);

	// If the request was decided ELSEWHERE (another operator, the rail's Deny
	// fast-path) while a draft existed, the draft can never be resumed — the
	// terminal gate below renders the read-only summary from now on. Drop the
	// entry so it doesn't sit in the map for the rest of the session. Any
	// credential created before the external decision stays (deleting server
	// objects on a status we merely OBSERVED is too destructive); operators
	// can remove them from the credentials page.
	useEffect(() => {
		if (effectiveStatus !== 'pending') wizardDrafts.delete(request.id);
	}, [effectiveStatus, request.id]);

	// The chain the per-chain steps currently operate on. `chain`/`progress`
	// are undefined only transiently (index race on a re-seed); guarded below.
	const chain = chains[chainIndex] as PlanChain | undefined;
	const progress = chainStates[chainIndex] as ChainProgress | undefined;
	const noAuth = chain ? chainIsNoAuth(chain) : true;
	const detectedAuth = chain ? chainAuthType(chain) : null;
	const initialCredentialType = authTypeToCredentialType(detectedAuth);
	// Locked chains can't be un-skipped: there is no credential:bind to amend,
	// so any credential the operator created would be thrown away.
	const chainLocked = chain !== undefined && chainUnfulfillable(chain);
	// The chain's policy is a shared rule set: rules are read-only here.
	const ruleSetId = chain ? chainRuleSetId(chain) : null;

	/** Mutate the current chain's progress (marks the wizard as touched). */
	const updateChain = useCallback(
		(patch: Partial<ChainProgress>, index?: number) => {
			const at = index ?? chainIndex;
			setTouched(true);
			setChainStates((prev) => prev.map((cs, i) => (i === at ? { ...cs, ...patch } : cs)));
		},
		[chainIndex],
	);

	// Per-item "already in effect" hints from the fresh single-request GET
	// (issue #826): true when the binding/grant an item asks for already
	// exists — e.g. the operator set things up manually outside the wizard.
	// Absent entries mean "not computed" (never "no"). `satisfiedByItemId`
	// carries the satisfying CREDENTIAL id for credential:bind items so the
	// nudge can name the exact object instead of waving at a bare boolean.
	const satisfiedItemIds = useMemo(() => {
		const ids = new Set<string>();
		for (const it of freshRequest?.items ?? []) {
			if (it.already_satisfied === true) ids.add(it.id);
		}
		return ids;
	}, [freshRequest]);
	const satisfiedByItemId = useMemo(() => {
		const map = new Map<string, string>();
		for (const it of freshRequest?.items ?? []) {
			if (it.already_satisfied === true && it.already_satisfied_by) {
				map.set(it.id, it.already_satisfied_by);
			}
		}
		return map;
	}, [freshRequest]);
	const chainAlreadyWired = useCallback(
		(c: PlanChain): boolean =>
			c.credentialBind !== undefined && satisfiedItemIds.has(c.credentialBind.id),
		[satisfiedItemIds],
	);

	// Existing credentials for the CURRENT chain's API vendor, cached per
	// vendor. Vendor-filtered server-side so the picker only offers
	// credentials that can actually serve this chain — the filter is an exact
	// match against slugified rows, so the raw filed vendor must be slugified
	// first (issue #656's mismatch). Disabled credentials are excluded; a
	// failed fetch is kept distinct from empty so the UI can offer a retry.
	const chainVendor = chain ? slugifyApiField(chain.apiRef.vendor) : undefined;
	const [existingCredentials, setExistingCredentials] = useState<
		Record<string, CredentialRedactedResponse[] | 'error'>
	>({});
	useEffect(() => {
		if (!open || step !== 'credential' || !chainVendor) return;
		if (existingCredentials[chainVendor] !== undefined) return;
		let cancelled = false;
		void CredentialsService.listCredentials({ vendor: chainVendor, limit: 100 })
			.then((res) => {
				if (!cancelled) {
					setExistingCredentials((prev) => ({
						...prev,
						[chainVendor]: res.data.filter((c) => c.active),
					}));
				}
			})
			.catch(() => {
				if (!cancelled) {
					setExistingCredentials((prev) => ({ ...prev, [chainVendor]: 'error' }));
				}
			});
		return () => {
			cancelled = true;
		};
	}, [open, step, chainVendor, existingCredentials]);
	const chainCredentialOptions = chainVendor ? (existingCredentials[chainVendor] ?? null) : null;
	// A never-connected OAuth credential (authorization_code whose interactive
	// sign-in was never completed — the backend's derived `connected` flag on
	// the redacted details) would fail at execute time if adopted. Flag it in
	// the picker so the operator is warned before adopting, not after (#890).
	// `connected` is absent for other types/grants, so only an explicit false
	// marks a credential as unconnected.
	const isUnconnectedOAuth = (cred: CredentialRedactedResponse) =>
		cred.type === CredentialType.OAUTH2 && cred.details?.connected === false;

	// Picker selections are staged locally and committed by an explicit
	// button: committing on <select> change is a keyboard trap (arrowing
	// through options on a closed native select fires change per keystroke —
	// WCAG 3.2.2). Reset whenever the step or chain changes.
	const [pendingCredentialChoice, setPendingCredentialChoice] = useState('');
	useEffect(() => {
		setPendingCredentialChoice('');
	}, [step, chainIndex]);
	// Derived before JSX (file convention — no closures in the tree): the
	// staged credential and whether it needs the unconnected-OAuth warning.
	const pendingCredential =
		chainCredentialOptions !== 'error' && chainCredentialOptions !== null
			? chainCredentialOptions.find((c) => c.credential_id === pendingCredentialChoice)
			: undefined;
	const pendingUnconnectedOAuth =
		pendingCredential !== undefined && isUnconnectedOAuth(pendingCredential);

	/**
	 * Adopt an existing credential — reused as-is, no connect flow. The picker
	 * only offers active credentials; a never-connected OAuth credential is
	 * flagged in the picker (see `isUnconnectedOAuth`) with a warning before
	 * commit, but adoption still trusts the operator's choice — warned, not
	 * blocked (a broken pick fails at execute time, same as it would for the
	 * manual setup being adopted). Adopting commits to this chain (clears a
	 * skip).
	 */
	const handleAdoptCredential = useCallback(
		(credentialId: string) => {
			const cred =
				chainCredentialOptions === 'error'
					? undefined
					: chainCredentialOptions?.find((c) => c.credential_id === credentialId);
			if (!cred) return;
			updateChain({
				credentialId: cred.credential_id,
				credentialType: cred.type,
				credentialName: cred.name,
				credentialAdopted: true,
				credentialUnconnected: isUnconnectedOAuth(cred),
				skipped: false,
			});
			setStep('rules');
		},
		[chainCredentialOptions, updateChain],
	);

	const handleClearAdoptedCredential = useCallback(() => {
		updateChain({
			credentialId: null,
			credentialType: null,
			credentialName: null,
			credentialAdopted: false,
			credentialUnconnected: false,
		});
	}, [updateChain]);

	/** Advance past the current chain: next chain's first step, or review. */
	const advanceChain = useCallback(() => {
		if (chainIndex + 1 < chains.length) {
			setChainIndex(chainIndex + 1);
			setStep(chainFirstStep(chains[chainIndex + 1]));
		} else {
			setStep('review');
		}
	}, [chainIndex, chains]);

	/** Skip the current chain (its items are denied at submit) and advance. */
	const handleSkipChain = useCallback(() => {
		updateChain({ skipped: true });
		advanceChain();
	}, [updateChain, advanceChain]);

	/** Include a previously skipped chain again (not offered on locked chains). */
	const handleUnskipChain = useCallback(() => {
		if (chainLocked) return;
		updateChain({ skipped: false });
	}, [chainLocked, updateChain]);

	const handleCredentialCreated = useCallback(
		async (info: CreatedCredentialInfo) => {
			setCredentialDialogOpen(false);
			// Creating a credential is committing to this chain — fulfilment
			// work always clears a skip, so the created object can't end up
			// silently stranded behind a still-skipped (denied) chain.
			updateChain({
				credentialId: info.credentialId,
				credentialType: info.type,
				credentialName: null,
				credentialAdopted: false,
				credentialUnconnected: false,
				skipped: false,
			});
			// An OAuth2 credential that needs a browser sign-in (authorization-code
			// with an authorize URL) has NO token until the connect flow completes —
			// binding it as-is makes the broker fail at execute with "No refresh
			// token available". So drive the connect flow now and only advance once
			// it's connected; on cancel/timeout/error, discard the dangling credential
			// and stay on this step so the operator can retry. Non-redirect grants
			// (client_credentials, static/manual tokens) are usable immediately.
			const needsConnect =
				info.type === CredentialType.OAUTH2 &&
				info.provider !== 'static' &&
				info.needsConnect;
			if (!needsConnect) {
				setStep('rules');
				return;
			}
			setBusy(true);
			setError(null);
			try {
				const flow = await runConnectFlow(info.credentialId);
				if (flow.status === 'connected' || flow.status === 'redirected') {
					// 'redirected' = popup blocked, same-tab navigation in progress; the
					// callback will land on return. Treat both as "proceeding".
					setStep('rules');
				} else {
					await discardPlanCredential(info.credentialId);
					updateChain({ credentialId: null });
					setError(
						flow.status === 'timeout'
							? 'Sign-in timed out — the unconnected credential was discarded. Try connecting again.'
							: 'Sign-in was cancelled — the unconnected credential was discarded. Try again.',
					);
				}
			} catch (e) {
				await discardPlanCredential(info.credentialId);
				updateChain({ credentialId: null });
				setError(e instanceof Error ? e.message : 'Could not complete sign-in.');
			} finally {
				setBusy(false);
			}
		},
		[updateChain],
	);

	// Everything the wizard CREATED this session, for orphan control on cancel.
	// Adopted (pre-existing) credentials are deliberately excluded — discarding
	// them would delete infrastructure the operator set up outside the wizard.
	const createdCredentialIds = chainStates
		.filter((cs) => cs.credentialId && !cs.credentialAdopted)
		.map((cs) => cs.credentialId!);

	const handleCancel = useCallback(() => {
		// Orphan control: if we created credentials but didn't approve, ask
		// whether to discard them so an abandoned fulfilment doesn't strand
		// objects. In-dialog (house style) — never a native browser confirm().
		if (createdCredentialIds.length > 0 && outcome !== 'granted') {
			setConfirmDiscard(true);
			return;
		}
		onClose();
	}, [createdCredentialIds.length, outcome, onClose]);

	/** "Keep & finish later" — close the wizard, leave the draft objects. */
	const handleKeepAndClose = useCallback(() => {
		setConfirmDiscard(false);
		onClose();
	}, [onClose]);

	/** "Discard" — best-effort delete of session-created objects, then close. */
	const handleDiscardAndClose = useCallback(async () => {
		setDiscarding(true);
		try {
			for (const id of createdCredentialIds) await discardPlanCredential(id);
		} finally {
			// Reset the draft so a reopen doesn't reference the deleted objects
			// (the draft otherwise persists across dismissals by design).
			wizardDrafts.delete(request.id);
			setChainStates(seedChainProgress(chains));
			setChainIndex(0);
			setStep(chainFirstStep(chains[0]));
			setTouched(false);
			setDiscarding(false);
			setConfirmDiscard(false);
			onClose();
		}
	}, [createdCredentialIds, onClose, chains, request.id]);

	// A chain counts as fulfilled when it wasn't skipped AND has everything its
	// bind item needs: a connected credential, unless the chain is no-auth
	// (that credential is auto-created at submit).
	const chainFulfilled = useCallback(
		(i: number): boolean => {
			const cs = chainStates[i];
			const c = chains[i];
			if (!cs || !c || cs.skipped) return false;
			return chainIsNoAuth(c) || cs.credentialId !== null;
		},
		[chains, chainStates],
	);
	const fulfilledCount = chains.reduce((n, _c, i) => n + (chainFulfilled(i) ? 1 : 0), 0);
	const skippedKeys = useMemo(
		() => new Set(chains.filter((_c, i) => chainStates[i]?.skipped).map((c) => c.key)),
		[chains, chainStates],
	);

	const handleSubmit = useCallback(async () => {
		if (fulfilledCount === 0 && shape.extras.length === 0) return;
		setBusy(true);
		setError(null);
		try {
			// 1. Amend every fulfilled chain's resolved credential id + confirmed
			//    rules onto its bind item in ONE call. A no-auth chain still needs
			//    a credential for the credential:bind effect to attach the agent
			//    binding + rules to (the broker keys rules on `(agent,
			//    credential)` and resolves a no_auth credential as a no-op auth) —
			//    auto-create a NO_AUTH credential per such chain now. When the
			//    bind item carries a shared `rule_set_id`, that set IS the policy
			//    (mutually exclusive with inline rules) — the amendment preserves
			//    the pointer by sending no `rules`.
			const amendments: ItemAmendment[] = [];
			for (let i = 0; i < chains.length; i++) {
				if (!chainFulfilled(i)) continue;
				const c = chains[i];
				const cs = chainStates[i];
				if (!c.credentialBind) continue;
				let bindCredentialId = cs.credentialId;
				if (chainIsNoAuth(c) && !bindCredentialId) {
					const created = await createNoAuthCredential(
						{
							vendor: c.apiRef.vendor,
							name: c.apiRef.name,
							version: c.apiRef.version,
						},
						// Credential names share the 255-char DB cap but the create
						// schema doesn't enforce it — clamp the API-derived base so
						// the suffix always fits and a long vendor/name can't
						// surface as an opaque server error at the FINAL step.
						`${apiLabel(c.apiRef).slice(0, 240)} (no-auth)`,
					);
					bindCredentialId = created.credentialId;
					updateChain({ credentialId: created.credentialId }, i);
				}
				amendments.push({
					item_id: c.credentialBind.id,
					...(bindCredentialId ? { resource_id: bindCredentialId } : {}),
					...(chainRuleSetId(c) ? {} : { rules: cs.rules }),
				});
				// Audit honesty (#897): stamp the fulfilled id onto the inert
				// placeholder too. Approving it is a recorded no-op either way,
				// but without the id the record can't say WHICH credential
				// fulfilled the "provision" intent — which matters most when the
				// operator adopted an EXISTING credential instead of creating one
				// (the agent's --wait then reads a full approval naming the
				// reused credential, not a phantom "provision" with no object).
				if (c.provision && bindCredentialId) {
					amendments.push({ item_id: c.provision.id, resource_id: bindCredentialId });
				}
			}
			if (amendments.length > 0) {
				await amendAccessRequest(request.id, amendments);
			}
			// 2. Decide every pending item: approve fulfilled chains + the plain
			//    extras, deny the chains the operator skipped (leaving them pending
			//    would hold the whole request open and the filing agent's --wait
			//    hanging). Item ids are re-read from the amended request and
			//    re-grouped so decisions key on the CHAIN, not item order. Locked
			//    (unfulfillable) chains carry their own reason so the agent reads
			//    back why, not just that, they were denied.
			const fresh = await getAccessRequest(request.id);
			const freshShape = planChains(fresh);
			const denialReasons = new Map<string, string>();
			for (const c of freshShape.chains) {
				if (!skippedKeys.has(c.key)) continue;
				const reason = chainUnfulfillable(c)
					? 'Cannot be fulfilled from the dashboard wizard: the request has no credential:bind item for this API.'
					: 'Skipped by the operator during fulfilment.';
				for (const it of chainItems(c)) denialReasons.set(it.id, reason);
			}
			const decisions: ItemDecision[] = fresh.items
				.filter((it) => it.status === 'pending')
				.map((it) => {
					const reason = denialReasons.get(it.id);
					return reason !== undefined
						? {
								item_id: it.id,
								decision: 'denied' as const,
								decision_reason: reason,
							}
						: { item_id: it.id, decision: 'approved' as const };
				});
			if (decisions.length === 0) {
				// Nothing left to decide — the request was already decided elsewhere
				// (e.g. a stale snapshot). Reflect the current server truth instead
				// of POSTing an empty decision.
				wizardDrafts.delete(request.id);
				const alreadyGranted = isPlanGranted(fresh);
				setOutcome(alreadyGranted ? 'granted' : 'error');
				if (!alreadyGranted) {
					setError(planDenialReason(fresh) ?? 'This request was already decided.');
				}
				setStep('done');
				return;
			}
			const decided = await decideAccessRequest(request.id, decisions);
			// Granted = everything the operator MEANT to grant actually wired: every
			// fulfilled chain's credential:bind approved and every extra item
			// approved. Skipped chains were denied on purpose and don't count
			// against it. The aggregate `partially_approved` is NOT the signal here
			// — one denied bind on a fulfilled chain means that agent still can't
			// call that API.
			const decidedShape = planChains(decided);
			const chainsOk = decidedShape.chains
				.filter((c) => !skippedKeys.has(c.key))
				.every((c) => c.credentialBind?.status === 'approved');
			const extrasOk = decidedShape.extras.every((it) => it.status === 'approved');
			const granted = chainsOk && extrasOk;
			// Decided either way — there is no draft left to resume.
			wizardDrafts.delete(request.id);
			setOutcome(granted ? 'granted' : 'error');
			if (!granted) {
				setError(planDenialReason(decided) ?? 'The request could not be fully approved.');
			} else {
				onFulfilled?.();
			}
			setStep('done');
		} catch (e) {
			setError(e instanceof Error ? e.message : String(e));
		} finally {
			setBusy(false);
		}
	}, [
		chains,
		chainStates,
		chainFulfilled,
		fulfilledCount,
		shape.extras.length,
		skippedKeys,
		request.id,
		onFulfilled,
		updateChain,
	]);

	// A usable wizard needs at least one chain that can be wired (its
	// credential:bind is what the amend targets). Otherwise the caller should
	// have routed this to the plain AccessRequestDialog — render nothing rather
	// than a broken UI.
	if (chains.length === 0 || !chains.some((c) => c.credentialBind !== undefined)) {
		return null;
	}

	// A plan that's already been decided (approved / partially_approved / denied /
	// expired / withdrawn) must NOT show the live create/approve wizard — that
	// would let an operator re-fulfil a settled request (stranding orphan objects,
	// then failing at decide). Show a read-only outcome summary instead. The
	// wizard only drives PENDING plans. `effectiveStatus` (computed above) prefers
	// the freshly-fetched status over the (possibly stale) snapshot so a request
	// decided since the list was loaded is caught before any create/amend runs.
	if (effectiveStatus !== 'pending') {
		return (
			<TerminalSummaryDialog
				open={open}
				request={request}
				status={effectiveStatus}
				onClose={onClose}
			/>
		);
	}

	const credentialLabel = progress?.credentialType
		? (CREDENTIAL_TYPE_LABELS[progress.credentialType as CredentialType] ??
			progress.credentialType)
		: null;
	const multiChain = chains.length > 1;
	const chainLabel = apiLabel(chain?.apiRef ?? null);

	// The credential that already satisfies this chain's credential:bind (from
	// the backend hint), if any — named in the nudge and floated to the top of
	// the picker so the operator can find THE wired credential among up to 100
	// name-only options. Plain derivation (no hook): we're past the early
	// returns, and the list caps at one page.
	const wiredCredentialId = chain?.credentialBind
		? satisfiedByItemId.get(chain.credentialBind.id)
		: undefined;
	const credentialPickerOptions =
		chainCredentialOptions === 'error' || chainCredentialOptions === null
			? chainCredentialOptions
			: [
					...chainCredentialOptions.filter((c) => c.credential_id === wiredCredentialId),
					...chainCredentialOptions.filter((c) => c.credential_id !== wiredCredentialId),
				];
	const wiredCredentialName =
		wiredCredentialId && chainCredentialOptions !== 'error'
			? chainCredentialOptions?.find((c) => c.credential_id === wiredCredentialId)?.name
			: undefined;
	const chainSkipped = progress?.skipped === true;
	// Skipping is only offered on a composite: skipping the ONLY chain would
	// leave nothing to approve, which is a deny — the rail already has a
	// dedicated deny fast-path for that. An already-skipped chain shows the
	// un-skip affordance instead.
	const canSkip = multiChain && !chainSkipped && step !== 'review' && step !== 'done';
	// Review can submit when everything non-skipped is fulfilled and at least
	// one chain (or extra) will actually be granted.
	const allSettled = chains.every((_c, i) => chainFulfilled(i) || chainStates[i]?.skipped);
	const canSubmit = allSettled && (fulfilledCount > 0 || shape.extras.length > 0);

	const skipButton = canSkip ? (
		<Button variant="ghost" onClick={handleSkipChain} disabled={busy}>
			Skip this API
		</Button>
	) : (
		<span />
	);
	/** Back within/between chains: previous step, or the previous chain's end. */
	const backToPrevious = (from: 'credential' | 'rules') => {
		if (from === 'rules' && !noAuth) {
			setStep('credential');
			return;
		}
		if (chainIndex > 0) {
			setChainIndex(chainIndex - 1);
			setStep(
				chainStates[chainIndex - 1]?.skipped
					? chainFirstStep(chains[chainIndex - 1])
					: 'rules',
			);
		}
	};

	// A skipped chain parks on its first step showing the skip notice; the
	// footer offers Keep skipped / Include (locked chains only Continue).
	const skippedFooter = (
		<span className="flex items-center gap-2">
			<Button variant="ghost" onClick={advanceChain}>
				{chainLocked ? (
					<>
						Continue <ArrowRight className="h-4 w-4" />
					</>
				) : (
					'Keep skipped'
				)}
			</Button>
			{!chainLocked && (
				<Button variant="primary" onClick={handleUnskipChain}>
					Include this API
				</Button>
			)}
		</span>
	);

	const stepFooter: Record<Step, React.ReactNode> = {
		credential: (
			<>
				<span className="flex items-center gap-2">
					{chainIndex > 0 && (
						<Button variant="ghost" onClick={() => backToPrevious('credential')}>
							<ArrowLeft className="h-4 w-4" /> Back
						</Button>
					)}
					{skipButton}
				</span>
				{chainSkipped ? (
					skippedFooter
				) : (
					<Button
						variant="primary"
						onClick={() => setStep('rules')}
						disabled={!progress?.credentialId}
					>
						Continue <ArrowRight className="h-4 w-4" />
					</Button>
				)}
			</>
		),
		rules: (
			<>
				<span className="flex items-center gap-2">
					{(chainIndex > 0 || !noAuth) && (
						<Button variant="ghost" onClick={() => backToPrevious('rules')}>
							<ArrowLeft className="h-4 w-4" /> Back
						</Button>
					)}
					{skipButton}
				</span>
				{chainSkipped ? (
					skippedFooter
				) : (
					<Button variant="primary" onClick={advanceChain}>
						{chainIndex + 1 < chains.length ? (
							<>
								Next API <ArrowRight className="h-4 w-4" />
							</>
						) : (
							<>
								Review <ArrowRight className="h-4 w-4" />
							</>
						)}
					</Button>
				)}
			</>
		),
		review: (
			<>
				<Button
					variant="ghost"
					onClick={() => {
						// Back into the LAST chain (skipped chains land on their
						// locked/skip notice, where "Include this API" un-skips).
						setChainIndex(chains.length - 1);
						setStep(
							chainStates[chains.length - 1]?.skipped
								? chainFirstStep(chains[chains.length - 1])
								: 'rules',
						);
					}}
				>
					<ArrowLeft className="h-4 w-4" /> Back
				</Button>
				<Button
					variant="primary"
					onClick={handleSubmit}
					loading={busy}
					disabled={busy || !canSubmit}
				>
					<ShieldCheck className="h-4 w-4" /> Approve &amp; grant access
				</Button>
			</>
		),
		done: (
			<>
				<span />
				<Button variant="primary" onClick={onClose}>
					Done
				</Button>
			</>
		),
	};

	// The skip notice replaces the step body when the chain is skipped
	// (rendered on whichever step the skipped chain is parked on).
	const skippedNotice = (
		<StepBody title={`${chainLabel} is skipped`} blurb="This API is not part of the grant.">
			<div className="border-border bg-muted/40 flex max-w-md items-start gap-2.5 rounded-lg border p-4 text-sm">
				<XCircle className="text-danger mt-0.5 h-5 w-5 shrink-0" />
				<span>
					{chainLocked ? (
						<>
							This API&rsquo;s items can&rsquo;t be fulfilled from this wizard (the
							request has no credential:bind item for it) and will be{' '}
							<span className="text-danger font-medium">denied</span> at approval.
						</>
					) : (
						<>
							You skipped this API — its items will be{' '}
							<span className="text-danger font-medium">denied</span> at approval.
							{progress?.credentialId && (
								<> The credential you already created will be kept.</>
							)}{' '}
							Use &ldquo;Include this API&rdquo; below to change your mind.
						</>
					)}
				</span>
			</div>
		</StepBody>
	);

	return (
		<>
			<Dialog
				open={open && !credentialDialogOpen && !confirmDiscard}
				onClose={handleCancel}
				title="Set up access"
				size="xl"
				className="sm:max-w-4xl"
				subtitle={
					<div className="space-y-2">
						<span className="flex flex-wrap items-center gap-1.5">
							Grant
							<AgentBadge
								id={request.actor_id}
								name={agentName ?? request.actor_id}
								kind="Agent"
								size="sm"
							/>
							<ActorLabel
								actorId={request.actor_id}
								resolvedName={agentName}
								className="text-foreground font-medium"
							/>
							access to
							{chains.map((c) => (
								<Badge key={c.key} variant="default">
									{apiLabel(c.apiRef)}
								</Badge>
							))}
							{shape.extras.length > 0 && (
								<span className="text-muted-foreground">
									+ {shape.extras.length} more item
									{shape.extras.length === 1 ? '' : 's'}
								</span>
							)}
						</span>
						{request.reason && (
							<span className="text-muted-foreground flex items-baseline gap-1.5">
								<MessageSquare
									className="relative top-0.5 h-3 w-3 shrink-0"
									aria-hidden="true"
								/>
								<span className="text-foreground italic">
									&ldquo;{request.reason}&rdquo;
								</span>
							</span>
						)}
					</div>
				}
				footer={
					<div className="flex w-full items-center justify-between">
						{stepFooter[step]}
					</div>
				}
			>
				<div className="flex flex-col gap-8 sm:flex-row">
					<aside className="bg-muted/40 border-border shrink-0 rounded-lg border p-5 sm:w-60">
						<Stepper
							step={step}
							chainIndex={chainIndex}
							chains={chains}
							chainStates={chainStates}
						/>
					</aside>
					<div className="flex min-h-[22rem] min-w-0 flex-1 flex-col">
						{error && (
							<div className="mb-5">
								<ErrorAlert message={error} />
							</div>
						)}

						{step === 'credential' &&
							(chainSkipped ? (
								skippedNotice
							) : (
								<StepBody
									title={
										multiChain
											? `Connect a credential for ${chainLabel}`
											: 'Connect a credential'
									}
									blurb={
										<>
											{chainLabel} needs an account to call it
											{detectedAuth ? (
												<>
													{' '}
													(detected auth:{' '}
													<Badge variant="default">{detectedAuth}</Badge>)
												</>
											) : null}
											.{' '}
											<span className="text-foreground font-medium">You</span>{' '}
											enter the secret — the agent never sees it. Approving
											binds the agent directly to this credential.
										</>
									}
								>
									{progress?.credentialId && progress.credentialAdopted ? (
										<div className="max-w-md space-y-3">
											{progress.credentialUnconnected ? (
												// The warning must not vanish at the moment it
												// becomes binding: a never-connected adoption
												// stays warning-toned, not a green success.
												<div
													className="border-warning/30 bg-warning/5 flex items-center gap-2.5 rounded-lg border p-4 text-sm"
													role="status"
												>
													<AlertTriangle className="text-warning h-5 w-5 shrink-0" />
													<span>
														Using existing credential{' '}
														<span className="font-medium">
															{progress.credentialName ??
																credentialLabel ??
																'credential'}
														</span>{' '}
														— it was never connected, so calls will fail
														until someone connects it from the
														Credentials page.
													</span>
												</div>
											) : (
												<div className="border-success/30 bg-success/5 flex items-center gap-2.5 rounded-lg border p-4 text-sm">
													<CheckCircle2 className="text-success h-5 w-5 shrink-0" />
													<span>
														Using existing credential{' '}
														<span className="font-medium">
															{progress.credentialName ??
																credentialLabel ??
																'credential'}
														</span>{' '}
														— reused as-is. Its sign-in isn’t
														re-verified; if it has stopped working,
														calls will fail until you reconnect it.
													</span>
												</div>
											)}
											<Button
												variant="ghost"
												onClick={handleClearAdoptedCredential}
											>
												Use a different credential
											</Button>
										</div>
									) : progress?.credentialId ? (
										<div className="border-success/30 bg-success/5 flex max-w-md items-center gap-2.5 rounded-lg border p-4 text-sm">
											<CheckCircle2 className="text-success h-5 w-5 shrink-0" />
											<span>
												{credentialLabel ?? 'Credential'} connected and
												ready.
											</span>
										</div>
									) : (
										<div className="max-w-md space-y-4">
											{chain?.credentialBind !== undefined &&
												satisfiedItemIds.has(chain.credentialBind.id) && (
													// #826: the agent is ALREADY bound to a
													// credential serving this API — the operator
													// most likely set it up manually. Nudge
													// towards adopting it instead of minting a
													// duplicate, naming the credential when the
													// picker has resolved it.
													<div className="border-warning/40 bg-warning/5 rounded-lg border p-3 text-sm">
														{wiredCredentialName ? (
															<>
																This agent is already wired to{' '}
																<span className="font-medium">
																	{wiredCredentialName}
																</span>{' '}
																for {chainLabel} — adopt it below
																instead of creating another one.
															</>
														) : (
															<>
																This agent is already wired to a
																credential serving {chainLabel} —
																prefer adopting that existing
																credential over creating another
																one.
															</>
														)}
													</div>
												)}
											<Button
												variant="primary"
												size="lg"
												onClick={() => setCredentialDialogOpen(true)}
											>
												<KeyRound className="h-4 w-4" /> Connect credential
											</Button>
											{credentialPickerOptions === 'error' && (
												// Failure ≠ empty — offer a retry instead of a
												// silent collapse.
												<div className="text-muted-foreground flex items-center gap-2 pt-2 text-sm">
													<span>
														Couldn’t load your existing credentials —
														you can still connect a new one, or retry.
													</span>
													<Button
														variant="secondary"
														size="sm"
														onClick={() =>
															setExistingCredentials((prev) => {
																const next = { ...prev };
																if (chainVendor) {
																	delete next[chainVendor];
																}
																return next;
															})
														}
													>
														Retry
													</Button>
												</div>
											)}
											{credentialPickerOptions !== 'error' &&
												credentialPickerOptions !== null &&
												credentialPickerOptions.length > 0 && (
													// Adopt-existing path (#826): an active
													// credential the operator already provisioned
													// for this vendor can be reused as-is — no
													// connect flow and no orphan discard. Staged
													// selection + explicit commit (a change-commit
													// select is a keyboard trap). The
													// already-wired credential, when known, is
													// floated to the top.
													<div className="space-y-1.5 pt-2">
														<Label htmlFor="pw-existing-credential">
															Or use an existing credential for{' '}
															{chainLabel}
														</Label>
														<Select
															id="pw-existing-credential"
															value={pendingCredentialChoice}
															onChange={(e) =>
																setPendingCredentialChoice(
																	e.target.value,
																)
															}
															disabled={busy}
														>
															<option value="">
																Choose an existing credential…
															</option>
															{credentialPickerOptions.map((cred) => (
																<option
																	key={cred.credential_id}
																	value={cred.credential_id}
																>
																	{cred.name} (
																	{credentialTypeLabel(
																		cred.type,
																	) ?? cred.type}
																	)
																	{cred.credential_id ===
																	wiredCredentialId
																		? ' — already linked to this agent'
																		: isUnconnectedOAuth(cred)
																			? ' — not connected yet'
																			: ''}
																</option>
															))}
														</Select>
														{pendingUnconnectedOAuth && (
															<p
																className="text-warning text-sm"
																role="status"
															>
																This OAuth credential was never
																connected, so calls using it will
																fail until someone connects it from
																the Credentials page.
															</p>
														)}
														{pendingCredentialChoice && (
															<Button
																variant="secondary"
																onClick={() =>
																	handleAdoptCredential(
																		pendingCredentialChoice,
																	)
																}
																disabled={busy}
															>
																Use this credential
															</Button>
														)}
													</div>
												)}
										</div>
									)}
								</StepBody>
							))}

						{step === 'rules' &&
							(chainSkipped ? (
								skippedNotice
							) : (
								<StepBody
									title={
										multiChain
											? `Confirm what the agent can do on ${chainLabel}`
											: 'Confirm what the agent can do'
									}
									blurb={
										ruleSetId
											? 'This binding is governed by a shared permission rule set — its ordered rules are the effective policy, managed outside this request.'
											: 'The agent proposed these permission rules from the API spec. Edit them if you like — with no rules, every call is blocked.'
									}
								>
									{ruleSetId ? (
										<div className="border-border bg-muted/40 max-w-md rounded-lg border p-4 text-sm">
											Shared rule set{' '}
											<span className="font-mono font-medium">
												{ruleSetId}
											</span>{' '}
											applies to this binding. Approving keeps it attached;
											edit the set itself to change what the agent can do.
										</div>
									) : (
										<PermissionRuleEditor
											rules={progress?.rules ?? []}
											onChange={(next) => updateChain({ rules: next })}
										/>
									)}
								</StepBody>
							))}

						{step === 'review' && (
							<StepBody
								title="Review & grant"
								blurb="Approving wires this up and lets the agent call the API. Here's exactly what will happen:"
							>
								<div className="max-w-xl space-y-4">
									{!canSubmit && allSettled && (
										<div className="border-border bg-muted/40 rounded-lg border p-4 text-sm">
											Every API was skipped, so there is nothing to grant. Go
											back and include at least one API — or cancel and deny
											the request from the requests list instead.
										</div>
									)}
									{chains.map((c, i) => {
										const cs = chainStates[i];
										const skipped = cs?.skipped === true;
										const locked = chainUnfulfillable(c);
										return (
											<dl
												key={c.key}
												className="border-border divide-border divide-y rounded-lg border text-sm"
											>
												<SummaryRow label="API">
													<span className="flex flex-wrap items-center gap-x-2 gap-y-1">
														<span
															className={
																skipped ? 'opacity-60' : undefined
															}
														>
															{apiLabel(c.apiRef)}
														</span>
														{skipped && (
															<span className="text-danger">
																{locked
																	? 'unfulfillable — will be denied'
																	: 'skipped — will be denied'}
															</span>
														)}
														{!locked && (
															<Button
																variant="ghost"
																size="sm"
																onClick={() => {
																	setChainIndex(i);
																	setStep(
																		skipped ||
																			!chainFulfilled(i)
																			? chainFirstStep(c)
																			: 'rules',
																	);
																}}
															>
																Edit
															</Button>
														)}
													</span>
												</SummaryRow>
												{skipped ? (
													cs?.credentialId && (
														<SummaryRow label="Note">
															<span className="text-warning">
																The credential created for this API
																will be kept even though it is
																denied — use Edit to include it, or
																delete it afterwards.
															</span>
														</SummaryRow>
													)
												) : (
													<>
														<SummaryRow label="Credential">
															{chainIsNoAuth(c) ? (
																<span className="text-muted-foreground">
																	none — this API needs no auth (a
																	no-auth credential is created
																	automatically)
																</span>
															) : cs?.credentialAdopted ? (
																<>
																	{cs.credentialName ??
																		credentialTypeLabel(
																			cs.credentialType,
																		) ??
																		'connected'}
																	<span className="text-muted-foreground ml-2">
																		(existing)
																	</span>
																</>
															) : (
																(credentialTypeLabel(
																	cs?.credentialType,
																) ?? 'connected')
															)}
														</SummaryRow>
														<SummaryRow label="Agent can">
															{chainRuleSetId(c) ? (
																<span>
																	governed by shared rule set{' '}
																	<span className="font-mono">
																		{chainRuleSetId(c)}
																	</span>
																</span>
															) : (
																summarizeRules(cs?.rules ?? [])
															)}
														</SummaryRow>
														{chainAlreadyWired(c) && (
															// Honest per-path copy: adoption
															// reuses the detected setup (but the
															// approve still REPLACES the
															// binding's permission rules with the
															// ones confirmed here — say so);
															// creating anyway wires the NEW
															// credential alongside it.
															<SummaryRow label="Note">
																<span className="text-muted-foreground">
																	{cs?.credentialAdopted
																		? 'This agent is already wired to this credential — approving reuses that setup and updates its permission rules to the ones you confirmed here. Nothing is duplicated.'
																		: 'This agent already has a credential wired for this API — approving will bind the new credential created here alongside that existing setup.'}
																</span>
															</SummaryRow>
														)}
													</>
												)}
											</dl>
										);
									})}
									{shape.extras.length > 0 && (
										<dl className="border-border divide-border divide-y rounded-lg border text-sm">
											{shape.extras.map((it) => (
												<SummaryRow key={it.id} label="Also approves">
													{extraItemLabel(it)}
													{satisfiedItemIds.has(it.id) && (
														<span className="text-success ml-2">
															already in place — approving records it
														</span>
													)}
												</SummaryRow>
											))}
										</dl>
									)}
								</div>
							</StepBody>
						)}

						{step === 'done' && (
							<div className="flex flex-1 flex-col items-center justify-center gap-4 text-center">
								{outcome === 'granted' ? (
									<>
										<div className="bg-success/10 flex h-14 w-14 items-center justify-center rounded-full">
											<CheckCircle2 className="text-success h-8 w-8" />
										</div>
										<div className="space-y-1">
											<p className="text-foreground text-base font-medium">
												Access granted
											</p>
											<p className="text-muted-foreground text-sm">
												{(() => {
													const granted = chains
														.filter((_c, i) => !chainStates[i]?.skipped)
														.map((c) => apiLabel(c.apiRef));
													if (granted.length === 0) {
														return 'The approved items are now active for this agent.';
													}
													return `${granted.join(', ')} ${granted.length === 1 ? 'is' : 'are'} now callable by this agent.`;
												})()}
											</p>
										</div>
									</>
								) : (
									<>
										<div className="bg-danger/10 flex h-14 w-14 items-center justify-center rounded-full">
											<XCircle className="text-danger h-8 w-8" />
										</div>
										<p className="text-danger max-w-sm text-sm">
											{error ?? 'The request could not be fully approved.'}
										</p>
									</>
								)}
							</div>
						)}
					</div>
				</div>
			</Dialog>

			<CreateCredentialDialog
				open={credentialDialogOpen}
				onClose={() => setCredentialDialogOpen(false)}
				onCreated={handleCredentialCreated}
				initialType={initialCredentialType}
			/>

			{/* Cancel-with-orphans confirmation — a styled in-dialog step rather
			    than a jarring, unstyled native browser confirm(). */}
			<Dialog
				open={confirmDiscard}
				onClose={() => setConfirmDiscard(false)}
				title="Keep this setup for later?"
				size="md"
				footer={
					<div className="flex w-full items-center justify-end gap-2">
						<Button variant="ghost" onClick={handleKeepAndClose} disabled={discarding}>
							Keep &amp; finish later
						</Button>
						<Button
							variant="danger"
							onClick={handleDiscardAndClose}
							loading={discarding}
							disabled={discarding}
						>
							<Trash2 className="h-4 w-4" /> Discard
						</Button>
					</div>
				}
			>
				<div className="space-y-3 text-sm">
					<p className="text-foreground">
						This setup already created{' '}
						<span className="font-medium">
							{countLabel(createdCredentialIds.length, 'credential')}
						</span>{' '}
						for this request, but access hasn&rsquo;t been granted yet.
					</p>
					<p className="text-muted-foreground">
						<span className="text-foreground font-medium">Keep &amp; finish later</span>{' '}
						leaves them in place so you can reopen this request and continue.{' '}
						<span className="text-foreground font-medium">Discard</span> deletes them —
						the request stays pending and you can start over any time.
					</p>
				</div>
			</Dialog>
		</>
	);
}

/** Consistent step frame: a title, a one-line explanation, then the content. */
function StepBody({
	title,
	blurb,
	children,
}: {
	title: string;
	blurb: React.ReactNode;
	children: React.ReactNode;
}) {
	return (
		<section className="space-y-5">
			<div className="space-y-1.5">
				<h3 className="text-foreground text-base font-semibold">{title}</h3>
				<p className="text-muted-foreground max-w-xl text-sm leading-relaxed">{blurb}</p>
			</div>
			{children}
		</section>
	);
}

/** One label/value row in the review summary. */
function SummaryRow({ label, children }: { label: string; children: React.ReactNode }) {
	return (
		<div className="flex items-start gap-4 px-4 py-3">
			<dt className="text-muted-foreground w-24 shrink-0 text-xs font-medium tracking-wide uppercase">
				{label}
			</dt>
			<dd className="text-foreground min-w-0 flex-1 break-words">{children}</dd>
		</div>
	);
}

/** "a credential" / "2 credentials" — for the discard confirmation copy. */
function countLabel(n: number, noun: string): string {
	return n === 1 ? `a ${noun}` : `${n} ${noun}s`;
}

/** Credential-type display label, or null for an absent/unknown type. */
function credentialTypeLabel(type: string | null | undefined): string | null {
	if (!type) return null;
	return CREDENTIAL_TYPE_LABELS[type as CredentialType] ?? type;
}

/** Human label for a plain (non-chain) item on the review step. */
function extraItemLabel(it: AccessRequestItem): string {
	const key = `${it.resource_type}:${it.action}`;
	if (key === 'scope:grant') return `scope ${it.resource_id ?? ''}`.trim();
	const ref = it.resource_reference;
	const target =
		it.resource_id ??
		(ref && typeof ref.vendor === 'string'
			? [ref.vendor, typeof ref.name === 'string' ? ref.name : null].filter(Boolean).join('/')
			: null);
	if (key === 'credential:bind') {
		return `bind agent to credential ${target ?? ''}`.trim();
	}
	// Retired verb, historical rows only: render the stored strings, never file.
	if (key === 'toolkit:bind') {
		return `bind to toolkit ${target ?? ''}`.trim();
	}
	return key;
}

/**
 * Read-only summary shown when a provisioning plan is opened after it's already
 * been decided (or otherwise left `pending`). No create/approve controls. For an
 * approved plan it reconstructs WHAT was set up from the decided items (per
 * chain: credential, the rules the agent got, when); for a denial it shows the
 * reason the agent reads back. Prevents re-fulfilling a settled request.
 */
function TerminalSummaryDialog({
	open,
	request,
	status,
	onClose,
}: {
	open: boolean;
	request: AccessRequest;
	status: string;
	onClose: () => void;
}) {
	const approved = status === 'approved' || status === 'partially_approved';
	const deniedItem = request.items.find((it) => it.status === 'denied');
	const { chains } = planChains(request);
	const decidedAt = request.items.find((it) => it.decided_at)?.decided_at ?? null;

	const STATUS_COPY: Record<string, string> = {
		approved: 'The agent can now call this API.',
		partially_approved: 'Some items were approved; others were denied (see below).',
		denied: 'The agent was not granted access.',
		expired: 'This request expired before it was decided.',
		withdrawn: 'The agent withdrew this request before a decision.',
	};

	return (
		<Dialog
			open={open}
			onClose={onClose}
			title="Access request"
			size="lg"
			subtitle={
				<span className="flex flex-wrap items-center gap-1.5">
					<ActorLabel
						actorId={request.actor_id}
						className="text-foreground font-medium"
					/>
					· for
					{chains.map((c) => (
						<Badge key={c.key} variant="default">
							{apiLabel(c.apiRef)}
						</Badge>
					))}
				</span>
			}
			footer={
				<Button variant="primary" onClick={onClose}>
					Close
				</Button>
			}
		>
			<div className="space-y-5">
				<div className="flex items-center gap-3">
					<div
						className={
							approved
								? 'bg-success/10 flex h-12 w-12 shrink-0 items-center justify-center rounded-full'
								: 'bg-danger/10 flex h-12 w-12 shrink-0 items-center justify-center rounded-full'
						}
					>
						{approved ? (
							<CheckCircle2 className="text-success h-7 w-7" />
						) : (
							<XCircle className="text-danger h-7 w-7" />
						)}
					</div>
					<div>
						<p className="text-foreground text-base font-medium capitalize">
							{status.replace('_', ' ')}
						</p>
						<p className="text-muted-foreground text-sm">
							{STATUS_COPY[status] ?? 'This request is no longer pending.'}
						</p>
					</div>
				</div>

				{/* What the approval actually wired — concrete, per chain. */}
				{approved &&
					chains.map((c) => {
						const credentialId = c.credentialBind?.resource_id ?? null;
						// HISTORICAL: rows decided before toolkits were retired
						// carry the toolkit the credential was bound to on
						// `to_id` — render the stored string read-only.
						const legacyToolkitId = c.credentialBind?.to_id ?? null;
						const ruleSet = c.credentialBind?.rule_set_id ?? null;
						const grantedRules = c.credentialBind
							? ruleSummary(parseItemRules(c.credentialBind))
							: null;
						const chainDenied = chainItems(c).some((it) => it.status === 'denied');
						return (
							<dl
								key={c.key}
								className="border-border divide-border divide-y rounded-lg border text-sm"
							>
								<SummaryRow label="API">
									{apiLabel(c.apiRef)}
									{chainDenied && (
										<span className="text-danger ml-2">denied</span>
									)}
								</SummaryRow>
								{!chainDenied && (
									<>
										<SummaryRow label="Credential">
											{credentialId ?? (
												<span className="text-muted-foreground">
													none — no-auth API
												</span>
											)}
										</SummaryRow>
										{legacyToolkitId && (
											<SummaryRow label="Toolkit">
												{legacyToolkitId}
											</SummaryRow>
										)}
										{ruleSet ? (
											<SummaryRow label="Agent can">
												<span>
													governed by shared rule set{' '}
													<span className="font-mono">{ruleSet}</span>
												</span>
											</SummaryRow>
										) : (
											grantedRules && (
												<SummaryRow label="Agent can">
													{grantedRules}
												</SummaryRow>
											)
										)}
									</>
								)}
							</dl>
						);
					})}
				{approved && decidedAt && (
					<p className="text-muted-foreground text-xs">
						Decided {new Date(decidedAt).toLocaleString()}
					</p>
				)}

				{deniedItem?.decision_reason && (
					<div className="border-border bg-muted/40 rounded-lg border p-3">
						<p className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
							Reason
						</p>
						<p className="text-foreground mt-0.5 text-sm">
							{deniedItem.decision_reason}
						</p>
					</div>
				)}
			</div>
		</Dialog>
	);
}

/**
 * Plain-English gist of the rule set for the review summary — so the operator
 * reads "GET any path" rather than counting raw rule objects.
 */
function summarizeRules(rules: PermissionRuleInput[]): string {
	if (rules.length === 0) return 'nothing yet — add a rule or every call is blocked';
	const parts = rules.map((r) => {
		const verb = r.effect === 'allow' ? 'Allow' : 'Block';
		const methods = r.methods?.length ? r.methods.join(', ') : 'any method';
		const path = r.path?.trim() ? r.path : 'any path';
		return `${verb} ${methods} on ${path}`;
	});
	return parts.join('; ');
}

function Stepper({
	step,
	chainIndex,
	chains,
	chainStates,
}: {
	step: Step;
	chainIndex: number;
	chains: PlanChain[];
	chainStates: ChainProgress[];
}) {
	// Flatten every chain's steps (credential? → rules) plus the global review
	// into one ordered list. With several chains each step is suffixed with its
	// API so the rail reads as a per-API checklist; a skipped chain collapses
	// to a single dimmed entry.
	const multi = chains.length > 1;
	interface RailStep {
		key: string;
		label: string;
		hint: string;
		skipped?: boolean;
		/** Matches the wizard's (chainIndex, step) position, -1 for never-active. */
		chain: number;
		step: Step | null;
	}
	const steps: RailStep[] = [];
	chains.forEach((c, i) => {
		const suffix = multi ? ` — ${apiLabel(c.apiRef)}` : '';
		if (chainStates[i]?.skipped) {
			steps.push({
				key: `${c.key}:skipped`,
				label: `Skipped${suffix}`,
				hint:
					c.credentialBind === undefined
						? 'Unfulfillable — will be denied'
						: 'This API will be denied',
				skipped: true,
				chain: i,
				step: null,
			});
			return;
		}
		if (!chainIsNoAuth(c)) {
			steps.push({
				key: `${c.key}:credential`,
				label: `Connect credential${suffix}`,
				hint: 'You enter the secret',
				chain: i,
				step: 'credential',
			});
		}
		steps.push({
			key: `${c.key}:rules`,
			label: `Confirm rules${suffix}`,
			hint: 'What the agent may call',
			chain: i,
			step: 'rules',
		});
	});
	steps.push({
		key: 'review',
		label: 'Review & approve',
		hint: 'Grant access',
		chain: chains.length,
		step: 'review',
	});

	const activeIndex =
		step === 'done'
			? steps.length
			: steps.findIndex(
					(s) =>
						(s.step === 'review' && step === 'review') ||
						// A skipped chain collapses to one rail entry; when the
						// wizard is positioned inside it (Back from review, or
						// skipping forward), that entry is the active one.
						(s.chain === chainIndex && (s.step === step || s.skipped)),
				);

	return (
		<ol className="space-y-0">
			{steps.map((s, i) => {
				const done =
					!s.skipped && (step === 'done' || (activeIndex >= 0 && i < activeIndex));
				const active = i === activeIndex && step !== 'done';
				const last = i === steps.length - 1;
				return (
					<li key={s.key} className="flex items-start gap-3">
						<div className="flex flex-col items-center self-stretch">
							<span
								className={
									done
										? 'bg-primary text-background flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-semibold'
										: active
											? 'border-primary text-primary bg-card flex h-7 w-7 shrink-0 items-center justify-center rounded-full border-2 text-xs font-semibold'
											: 'border-border text-muted-foreground flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-xs'
								}
							>
								{done ? (
									<CheckCircle2 className="h-4 w-4" aria-hidden="true" />
								) : (
									i + 1
								)}
								{done && <span className="sr-only">done</span>}
							</span>
							{!last && (
								<span
									className={
										done ? 'bg-primary w-px flex-1' : 'bg-border w-px flex-1'
									}
									aria-hidden="true"
								/>
							)}
						</div>
						<div className={last ? 'pb-0' : 'pb-6'}>
							<p
								className={
									s.skipped
										? 'text-muted-foreground text-sm line-through'
										: active
											? 'text-foreground text-sm font-semibold'
											: done
												? 'text-foreground text-sm font-medium'
												: 'text-muted-foreground text-sm'
								}
							>
								{s.label}
							</p>
							<p className="text-muted-foreground mt-0.5 text-xs leading-relaxed">
								{s.hint}
							</p>
						</div>
					</li>
				);
			})}
		</ol>
	);
}
