/**
 * ProvisioningRequestDialog — fulfil a provisioning-plan access request.
 *
 * A `--provision` request is an ENVELOPE describing the whole path to first
 * execution rather than a single last-mile binding (see `provisioningPlan.ts`):
 *
 *   toolkit:create        — create a toolkit that serves the API      (Step 1)
 *   credential:provision  — create a credential for the API           (Step 2, skipped if no-auth)
 *   credential:bind       — bind that credential to that toolkit + rules
 *   toolkit:bind          — bind the agent to that toolkit
 *
 * The two `create`/`provision` items are inert placeholders; a human fulfils
 * them here by CREATING the real toolkit/credential (reusing the shared
 * CreateCredentialDialog and toolkit create), then this wizard AMENDs the
 * resulting ids + confirmed rules onto the `credential:bind` item and APPROVES
 * the whole request — the existing bind effects do the real wiring.
 *
 * Orphans: the toolkit/credential are created before approval, so an abandoned
 * fulfilment can leave real objects. The wizard tracks what it created this
 * session and offers to discard them on cancel (client-side, best-effort).
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
	ArrowLeft,
	ArrowRight,
	CheckCircle2,
	KeyRound,
	MessageSquare,
	ShieldCheck,
	XCircle,
} from 'lucide-react';
import { Dialog } from '@/shared/ui/Dialog';
import { Button } from '@/shared/ui/Button';
import { Input } from '@/shared/ui/Input';
import { Label } from '@/shared/ui/Label';
import { Badge } from '@/shared/ui/Badge';
import { ActorLabel } from '@/shared/ui/ActorLabel';
import { AgentBadge } from '@/shared/ui/AgentBadge';
import { ErrorAlert } from '@/shared/ui/ErrorAlert';
import { PermissionRuleEditor, type PermissionRuleInput } from '@/shared/ui/PermissionRuleEditor';
import { CreateCredentialDialog } from '@/shared/credentials/components/CreateCredentialDialog';
import type { CreatedCredentialInfo } from '@/shared/credentials/components/CreateCredentialDialog';
import { CREDENTIAL_TYPE_LABELS, runConnectFlow } from '@/shared/credentials/api';
import { CredentialType } from '@/shared/api';
import {
	amendAccessRequest,
	decideAccessRequest,
	getAccessRequest,
	parseItemRules,
	ruleSummary,
	type AccessRequest,
} from '@/shared/lib/accessRequests';
import {
	findItem,
	isPlanGranted,
	planApiReference,
	planAuthType,
	planDenialReason,
	planIsNoAuth,
	type PlanApiReference,
} from '@/shared/lib/provisioningPlan';
import {
	createNoAuthCredential,
	createPlanToolkit,
	discardPlanCredential,
	discardPlanToolkit,
	suggestToolkitName,
} from '@/shared/lib/provisioningFulfilment';

type Step = 'toolkit' | 'credential' | 'rules' | 'review' | 'done';
type Outcome = 'granted' | 'error';

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

export function ProvisioningRequestDialog({
	open,
	request,
	onClose,
	onFulfilled,
}: ProvisioningRequestDialogProps) {
	const apiRef = useMemo(() => planApiReference(request), [request]);
	const noAuth = useMemo(() => planIsNoAuth(request), [request]);
	const detectedAuth = useMemo(() => planAuthType(request), [request]);
	const initialCredentialType = useMemo(
		() => authTypeToCredentialType(detectedAuth),
		[detectedAuth],
	);
	const bindItem = useMemo(() => findItem(request, 'credential', 'bind'), [request]);
	const agentBindItem = useMemo(() => findItem(request, 'toolkit', 'bind'), [request]);

	// Seed the rule editor from the agent's proposed rules on the bind item.
	const proposedRules = useMemo<PermissionRuleInput[]>(() => {
		if (!bindItem) return [];
		return parseItemRules(bindItem).map((r) => ({
			// The editor only authors allow/deny; collapse the rare require-approval.
			effect: (r.effect === 'require-approval'
				? 'deny'
				: r.effect) as PermissionRuleInput['effect'],
			methods: r.methods ?? null,
			path: r.path ?? null,
			operations: r.operations ?? null,
		}));
	}, [bindItem]);

	const [step, setStep] = useState<Step>('toolkit');
	const [toolkitId, setToolkitId] = useState<string | null>(null);
	const [toolkitName, setToolkitName] = useState('');
	const [credentialId, setCredentialId] = useState<string | null>(null);
	const [credentialType, setCredentialType] = useState<string | null>(null);
	const [rules, setRules] = useState<PermissionRuleInput[]>([]);
	const [credentialDialogOpen, setCredentialDialogOpen] = useState(false);
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const [outcome, setOutcome] = useState<Outcome | null>(null);
	// The request's status re-fetched on open. Callers pass a possibly-stale
	// snapshot from the list query; before showing the LIVE create/approve
	// controls we confirm the request is still pending, so an operator can't
	// re-fulfil a request that was decided/expired since the list was fetched
	// (which would strand a real toolkit/credential then fail at decide). Null
	// until the fetch resolves; the terminal gate falls back to the snapshot.
	const [freshStatus, setFreshStatus] = useState<string | null>(null);

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
				if (!cancelled) setFreshStatus(fresh.status);
			})
			.catch(() => {
				// Best-effort: on a fetch failure keep the snapshot status; the
				// decide step still re-fetches and will surface any real error.
			});
		return () => {
			cancelled = true;
		};
	}, [open, request.id]);

	// Seed name + rules once per request id (not on every open flip).
	useEffect(() => {
		setStep('toolkit');
		setToolkitId(null);
		setCredentialId(null);
		setCredentialType(null);
		setToolkitName(suggestToolkitName(apiRef?.vendor ?? '', apiRef?.name));
		setRules(proposedRules);
		setOutcome(null);
		setFreshStatus(null);
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [request.id]);

	const handleCreateToolkit = useCallback(async () => {
		setBusy(true);
		setError(null);
		try {
			const created = await createPlanToolkit(toolkitName.trim());
			setToolkitId(created.toolkitId);
			setStep(noAuth ? 'rules' : 'credential');
		} catch (e) {
			setError(e instanceof Error ? e.message : String(e));
		} finally {
			setBusy(false);
		}
	}, [toolkitName, noAuth]);

	const handleCredentialCreated = useCallback(async (info: CreatedCredentialInfo) => {
		setCredentialDialogOpen(false);
		setCredentialId(info.credentialId);
		setCredentialType(info.type);
		// An OAuth2 credential that needs a browser sign-in (authorization-code
		// with an authorize URL) has NO token until the connect flow completes —
		// binding it as-is makes the broker fail at execute with "No refresh
		// token available". So drive the connect flow now and only advance once
		// it's connected; on cancel/timeout/error, discard the dangling credential
		// and stay on this step so the operator can retry. Non-redirect grants
		// (client_credentials, static/manual tokens) are usable immediately.
		const needsConnect =
			info.type === CredentialType.OAUTH2 && info.provider !== 'static' && info.needsConnect;
		if (!needsConnect) {
			setStep('rules');
			return;
		}
		setBusy(true);
		setError(null);
		try {
			const outcome = await runConnectFlow(info.credentialId);
			if (outcome.status === 'connected' || outcome.status === 'redirected') {
				// 'redirected' = popup blocked, same-tab navigation in progress; the
				// callback will land on return. Treat both as "proceeding".
				setStep('rules');
			} else {
				await discardPlanCredential(info.credentialId);
				setCredentialId(null);
				setError(
					outcome.status === 'timeout'
						? 'Sign-in timed out — the unconnected credential was discarded. Try connecting again.'
						: 'Sign-in was cancelled — the unconnected credential was discarded. Try again.',
				);
			}
		} catch (e) {
			await discardPlanCredential(info.credentialId);
			setCredentialId(null);
			setError(e instanceof Error ? e.message : 'Could not complete sign-in.');
		} finally {
			setBusy(false);
		}
	}, []);

	const handleCancel = useCallback(async () => {
		// Orphan control: if we created a toolkit/credential but didn't approve,
		// offer to discard them so an abandoned fulfilment doesn't strand objects.
		if ((toolkitId || credentialId) && outcome !== 'granted') {
			const discard = window.confirm(
				'Discard the toolkit and credential created for this request? ' +
					'Choose Cancel to keep them and finish later.',
			);
			if (discard) {
				if (credentialId) await discardPlanCredential(credentialId);
				if (toolkitId) await discardPlanToolkit(toolkitId);
			}
		}
		onClose();
	}, [toolkitId, credentialId, outcome, onClose]);

	const handleSubmit = useCallback(async () => {
		if (!bindItem || !toolkitId || !apiRef) return;
		setBusy(true);
		setError(null);
		try {
			// A no-auth plan still needs a credential for the credential:bind
			// effect to attach the toolkit binding + rules to (the broker keys
			// rules on `(toolkit, credential)` and resolves a no_auth credential as
			// a no-op auth). We didn't ask the operator for one — auto-create a
			// NO_AUTH credential now and bind THAT. For an auth plan the operator
			// already connected a real credential (`credentialId`).
			let bindCredentialId = credentialId;
			if (noAuth && !bindCredentialId) {
				const created = await createNoAuthCredential(
					{ vendor: apiRef.vendor, name: apiRef.name, version: apiRef.version },
					`${toolkitName} (no-auth)`,
				);
				bindCredentialId = created.credentialId;
				setCredentialId(created.credentialId);
			}
			// 1. Amend the resolved toolkit + credential ids + confirmed rules onto
			//    the credential:bind item. Also stamp the concrete toolkit id onto
			//    the toolkit:bind item so it resolves by id — the credential->
			//    toolkit binding isn't visible to the reference join until the
			//    credential:bind effect applies later in the same decision, so
			//    resolving the agent binding by API reference would deny (see
			//    provisioning-plan e2e / #656 ordering).
			await amendAccessRequest(request.id, [
				{
					item_id: bindItem.id,
					to_id: toolkitId,
					...(bindCredentialId ? { resource_id: bindCredentialId } : {}),
					rules,
				},
				...(agentBindItem ? [{ item_id: agentBindItem.id, resource_id: toolkitId }] : []),
			]);
			// 2. Approve every pending item — the bind effects wire everything.
			const fresh = await getAccessRequest(request.id);
			const decisions = fresh.items
				.filter((it) => it.status === 'pending')
				.map((it) => ({ item_id: it.id, decision: 'approved' as const }));
			if (decisions.length === 0) {
				// Nothing left to decide — the request was already decided elsewhere
				// (e.g. a stale snapshot). Reflect the current server truth instead
				// of POSTing an empty decision.
				const alreadyGranted = isPlanGranted(fresh);
				setOutcome(alreadyGranted ? 'granted' : 'error');
				if (!alreadyGranted) {
					setError(planDenialReason(fresh) ?? 'This request was already decided.');
				}
				setStep('done');
				return;
			}
			const decided = await decideAccessRequest(request.id, decisions);
			// A plan is only truly granted when BOTH bind items that wire access
			// (credential:bind + toolkit:bind) end up approved. The aggregate
			// `partially_approved` is NOT success here: if one bind is denied the
			// agent still can't call the API, so surface it as an error with the
			// denied item's reason rather than a misleading "Access granted".
			const granted = isPlanGranted(decided);
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
		bindItem,
		agentBindItem,
		toolkitId,
		credentialId,
		rules,
		request.id,
		onFulfilled,
		noAuth,
		apiRef,
		toolkitName,
	]);

	if (!apiRef || !bindItem) {
		// Not a well-formed provisioning plan — the caller should have routed this
		// to the plain AccessRequestDialog. Render nothing rather than a broken UI.
		return null;
	}

	// A plan that's already been decided (approved / partially_approved / denied /
	// expired / withdrawn) must NOT show the live create/approve wizard — that
	// would let an operator re-fulfil a settled request (stranding orphan objects,
	// then failing at decide). Show a read-only outcome summary instead. The
	// wizard only drives PENDING plans. Prefer the freshly-fetched status over the
	// (possibly stale) snapshot so a request decided since the list was loaded is
	// caught before any create/amend runs.
	const effectiveStatus = freshStatus ?? request.status;
	if (effectiveStatus !== 'pending') {
		return (
			<TerminalSummaryDialog
				open={open}
				request={request}
				status={effectiveStatus}
				apiLabel={apiLabel(apiRef)}
				onClose={onClose}
			/>
		);
	}

	const credentialLabel = credentialType
		? (CREDENTIAL_TYPE_LABELS[credentialType as CredentialType] ?? credentialType)
		: null;
	const rulesGist = summarizeRules(rules);

	const stepFooter: Record<Step, React.ReactNode> = {
		toolkit: (
			<>
				<span />
				<Button
					variant="primary"
					onClick={handleCreateToolkit}
					loading={busy}
					disabled={busy || !toolkitName.trim()}
				>
					Create toolkit
					<ArrowRight className="h-4 w-4" />
				</Button>
			</>
		),
		credential: (
			<>
				<Button variant="ghost" onClick={() => setStep('toolkit')}>
					<ArrowLeft className="h-4 w-4" /> Back
				</Button>
				<Button variant="primary" onClick={() => setStep('rules')} disabled={!credentialId}>
					Continue <ArrowRight className="h-4 w-4" />
				</Button>
			</>
		),
		rules: (
			<>
				<Button variant="ghost" onClick={() => setStep(noAuth ? 'toolkit' : 'credential')}>
					<ArrowLeft className="h-4 w-4" /> Back
				</Button>
				<Button variant="primary" onClick={() => setStep('review')}>
					Review <ArrowRight className="h-4 w-4" />
				</Button>
			</>
		),
		review: (
			<>
				<Button variant="ghost" onClick={() => setStep('rules')}>
					<ArrowLeft className="h-4 w-4" /> Back
				</Button>
				<Button variant="primary" onClick={handleSubmit} loading={busy} disabled={busy}>
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

	return (
		<>
			<Dialog
				open={open && !credentialDialogOpen}
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
								name={request.actor_id}
								kind="Agent"
								size="sm"
							/>
							<ActorLabel
								actorId={request.actor_id}
								className="text-foreground font-medium"
							/>
							access to
							<Badge variant="default">{apiLabel(apiRef)}</Badge>
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
						<Stepper step={step} noAuth={noAuth} />
					</aside>
					<div className="flex min-h-[22rem] min-w-0 flex-1 flex-col">
						{error && (
							<div className="mb-5">
								<ErrorAlert message={error} />
							</div>
						)}

						{step === 'toolkit' && (
							<StepBody
								title="Create a toolkit"
								blurb={`A toolkit is the container that will serve ${apiLabel(apiRef)} to this agent. Give it a name — the default is fine.`}
							>
								<div className="max-w-md space-y-1.5">
									<Label htmlFor="pw-toolkit-name">Toolkit name</Label>
									<Input
										id="pw-toolkit-name"
										value={toolkitName}
										onChange={(e) => setToolkitName(e.target.value)}
										placeholder="e.g. googleapis-com/sheets"
									/>
								</div>
							</StepBody>
						)}

						{step === 'credential' && (
							<StepBody
								title="Connect a credential"
								blurb={
									<>
										{apiLabel(apiRef)} needs an account to call it
										{detectedAuth ? (
											<>
												{' '}
												(detected auth:{' '}
												<Badge variant="default">{detectedAuth}</Badge>)
											</>
										) : null}
										. <span className="text-foreground font-medium">You</span>{' '}
										enter the secret — the agent never sees it.
									</>
								}
							>
								{credentialId ? (
									<div className="border-success/30 bg-success/5 flex max-w-md items-center gap-2.5 rounded-lg border p-4 text-sm">
										<CheckCircle2 className="text-success h-5 w-5 shrink-0" />
										<span>
											{credentialLabel ?? 'Credential'} connected and ready.
										</span>
									</div>
								) : (
									<Button
										variant="primary"
										size="lg"
										onClick={() => setCredentialDialogOpen(true)}
									>
										<KeyRound className="h-4 w-4" /> Connect credential
									</Button>
								)}
							</StepBody>
						)}

						{step === 'rules' && (
							<StepBody
								title="Confirm what the agent can do"
								blurb="The agent proposed these permission rules from the API spec. Edit them if you like — with no rules, every call is blocked."
							>
								<PermissionRuleEditor rules={rules} onChange={setRules} />
							</StepBody>
						)}

						{step === 'review' && (
							<StepBody
								title="Review & grant"
								blurb="Approving wires this up and lets the agent call the API. Here's exactly what will happen:"
							>
								<dl className="border-border divide-border max-w-xl divide-y rounded-lg border text-sm">
									<SummaryRow label="API">{apiLabel(apiRef)}</SummaryRow>
									<SummaryRow label="Toolkit">{toolkitName}</SummaryRow>
									<SummaryRow label="Credential">
										{noAuth ? (
											<span className="text-muted-foreground">
												none — this API needs no auth
											</span>
										) : (
											(credentialLabel ?? 'connected')
										)}
									</SummaryRow>
									<SummaryRow label="Agent can">{rulesGist}</SummaryRow>
								</dl>
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
												{apiLabel(apiRef)} is now callable by this agent.
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

/**
 * Read-only summary shown when a provisioning plan is opened after it's already
 * been decided (or otherwise left `pending`). No create/approve controls. For an
 * approved plan it reconstructs WHAT was set up from the decided items (toolkit,
 * credential, the rules the agent got, when); for a denial it shows the reason
 * the agent reads back. Prevents re-fulfilling a settled request.
 */
function TerminalSummaryDialog({
	open,
	request,
	status,
	apiLabel,
	onClose,
}: {
	open: boolean;
	request: AccessRequest;
	status: string;
	apiLabel: string;
	onClose: () => void;
}) {
	const approved = status === 'approved' || status === 'partially_approved';
	const deniedItem = request.items.find((it) => it.status === 'denied');

	// Reconstruct what the approval actually wired, from the decided items.
	const bindItem = findItem(request, 'credential', 'bind');
	const agentBindItem = findItem(request, 'toolkit', 'bind');
	const toolkitId = agentBindItem?.resource_id ?? bindItem?.to_id ?? null;
	const credentialId = bindItem?.resource_id ?? null;
	const grantedRules = bindItem ? ruleSummary(parseItemRules(bindItem)) : null;
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
					<Badge variant="default">{apiLabel}</Badge>
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

				{/* What the approval actually wired — concrete, not filler. */}
				{approved && (toolkitId || credentialId || grantedRules) && (
					<dl className="border-border divide-border divide-y rounded-lg border text-sm">
						<SummaryRow label="API">{apiLabel}</SummaryRow>
						{toolkitId && <SummaryRow label="Toolkit">{toolkitId}</SummaryRow>}
						<SummaryRow label="Credential">
							{credentialId ?? (
								<span className="text-muted-foreground">none — no-auth API</span>
							)}
						</SummaryRow>
						{grantedRules && <SummaryRow label="Agent can">{grantedRules}</SummaryRow>}
						{decidedAt && (
							<SummaryRow label="Decided">
								{new Date(decidedAt).toLocaleString()}
							</SummaryRow>
						)}
					</dl>
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

function Stepper({ step, noAuth }: { step: Step; noAuth: boolean }) {
	const steps: { key: Step; label: string; hint: string }[] = [
		{ key: 'toolkit', label: 'Create toolkit', hint: 'A container that serves this API' },
		...(noAuth
			? []
			: [
					{
						key: 'credential' as Step,
						label: 'Connect credential',
						hint: 'You enter the secret',
					},
				]),
		{ key: 'rules', label: 'Confirm rules', hint: 'What the agent may call' },
		{ key: 'review', label: 'Review & approve', hint: 'Grant access' },
	];
	const order: Step[] = [...steps.map((s) => s.key), 'done'];
	const activeIndex = order.indexOf(step);

	return (
		<ol className="space-y-0">
			{steps.map((s, i) => {
				const done = step === 'done' || i < activeIndex;
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
								{done ? <CheckCircle2 className="h-4 w-4" /> : i + 1}
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
									active
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
