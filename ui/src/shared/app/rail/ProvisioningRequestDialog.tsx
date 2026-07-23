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
import { ArrowLeft, ArrowRight, CheckCircle2, KeyRound, ShieldCheck, XCircle } from 'lucide-react';
import { Dialog } from '@/shared/ui/Dialog';
import { Button } from '@/shared/ui/Button';
import { Input } from '@/shared/ui/Input';
import { Label } from '@/shared/ui/Label';
import { Badge } from '@/shared/ui/Badge';
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
	type AccessRequest,
} from '@/shared/lib/accessRequests';
import {
	findItem,
	planApiReference,
	planAuthType,
	planIsNoAuth,
	type PlanApiReference,
} from '@/shared/lib/provisioningPlan';
import {
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

export function ProvisioningRequestDialog({
	open,
	request,
	onClose,
	onFulfilled,
}: ProvisioningRequestDialogProps) {
	const apiRef = useMemo(() => planApiReference(request), [request]);
	const noAuth = useMemo(() => planIsNoAuth(request), [request]);
	const detectedAuth = useMemo(() => planAuthType(request), [request]);
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

	// Transient flags reset on every (re)open; the draft (created ids, rules)
	// persists between dismissals so a peek doesn't discard fulfilment progress.
	useEffect(() => {
		if (!open) return;
		setBusy(false);
		setError(null);
	}, [open]);

	// Seed name + rules once per request id (not on every open flip).
	useEffect(() => {
		setStep('toolkit');
		setToolkitId(null);
		setCredentialId(null);
		setCredentialType(null);
		setToolkitName(suggestToolkitName(apiRef?.vendor ?? '', apiRef?.name));
		setRules(proposedRules);
		setOutcome(null);
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
		if (!bindItem || !toolkitId) return;
		setBusy(true);
		setError(null);
		try {
			// 1. Amend the resolved toolkit/credential ids + confirmed rules onto
			//    the credential:bind item (credential is null for a no-auth plan —
			//    the bind then carries no credential and the toolkit:bind still
			//    grants the agent access to the no-auth toolkit). Also stamp the
			//    concrete toolkit id onto the toolkit:bind item so it resolves by
			//    id — the credential->toolkit binding isn't visible to the
			//    reference join until the credential:bind effect applies later in
			//    the same decision, so resolving the agent binding by API
			//    reference would deny (see provisioning-plan e2e / #656 ordering).
			await amendAccessRequest(request.id, [
				{
					item_id: bindItem.id,
					to_id: toolkitId,
					...(credentialId ? { resource_id: credentialId } : {}),
					rules,
				},
				...(agentBindItem ? [{ item_id: agentBindItem.id, resource_id: toolkitId }] : []),
			]);
			// 2. Approve every pending item — the bind effects wire everything.
			const fresh = await getAccessRequest(request.id);
			const decisions = fresh.items
				.filter((it) => it.status === 'pending')
				.map((it) => ({ item_id: it.id, decision: 'approved' as const }));
			const decided = await decideAccessRequest(request.id, decisions);
			const granted =
				decided.status === 'approved' || decided.status === 'partially_approved';
			setOutcome(granted ? 'granted' : 'error');
			if (!granted) {
				const denied = decided.items.find((it) => it.status === 'denied');
				setError(denied?.decision_reason ?? 'The request could not be fully approved.');
			} else {
				onFulfilled?.();
			}
			setStep('done');
		} catch (e) {
			setError(e instanceof Error ? e.message : String(e));
		} finally {
			setBusy(false);
		}
	}, [bindItem, agentBindItem, toolkitId, credentialId, rules, request.id, onFulfilled]);

	if (!apiRef || !bindItem) {
		// Not a well-formed provisioning plan — the caller should have routed this
		// to the plain AccessRequestDialog. Render nothing rather than a broken UI.
		return null;
	}

	const credentialLabel = credentialType
		? (CREDENTIAL_TYPE_LABELS[credentialType as CredentialType] ?? credentialType)
		: null;
	const rulesGist = summarizeRules(rules);

	return (
		<>
			<Dialog
				open={open && !credentialDialogOpen}
				onClose={handleCancel}
				title="Set up access"
				subtitle={
					<span className="flex items-center gap-1.5">
						Grant this agent access to
						<Badge variant="default">{apiLabel(apiRef)}</Badge>
					</span>
				}
			>
				<div className="flex flex-col gap-6 sm:flex-row">
					<div className="border-border shrink-0 sm:w-56 sm:border-r sm:pr-6">
						<Stepper step={step} noAuth={noAuth} />
					</div>
					<div className="min-w-0 flex-1">
						{error && (
							<div className="mb-4">
								<ErrorAlert message={error} />
							</div>
						)}

						{step === 'toolkit' && (
							<StepBody
								title="Create a toolkit"
								blurb={`A toolkit is the container that will serve ${apiLabel(apiRef)} to this agent. Give it a name — the default is fine.`}
							>
								<div className="space-y-1.5">
									<Label htmlFor="pw-toolkit-name">Toolkit name</Label>
									<Input
										id="pw-toolkit-name"
										value={toolkitName}
										onChange={(e) => setToolkitName(e.target.value)}
										placeholder="e.g. googleapis-com/sheets"
									/>
								</div>
								<StepActions>
									<Button
										variant="primary"
										onClick={handleCreateToolkit}
										loading={busy}
										disabled={busy || !toolkitName.trim()}
									>
										Create toolkit
										<ArrowRight className="h-4 w-4" />
									</Button>
								</StepActions>
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
									<div className="border-success/30 bg-success/5 flex items-center gap-2 rounded-lg border p-3 text-sm">
										<CheckCircle2 className="text-success h-4 w-4 shrink-0" />
										<span>
											{credentialLabel ?? 'Credential'} connected and ready.
										</span>
									</div>
								) : (
									<Button
										variant="primary"
										onClick={() => setCredentialDialogOpen(true)}
									>
										<KeyRound className="h-4 w-4" /> Connect credential
									</Button>
								)}
								<StepActions>
									<Button variant="ghost" onClick={() => setStep('toolkit')}>
										<ArrowLeft className="h-4 w-4" /> Back
									</Button>
									<Button
										variant="primary"
										onClick={() => setStep('rules')}
										disabled={!credentialId}
									>
										Continue <ArrowRight className="h-4 w-4" />
									</Button>
								</StepActions>
							</StepBody>
						)}

						{step === 'rules' && (
							<StepBody
								title="Confirm what the agent can do"
								blurb="The agent proposed these permission rules from the API spec. Edit them if you like — with no rules, every call is blocked."
							>
								<PermissionRuleEditor rules={rules} onChange={setRules} />
								<StepActions>
									<Button
										variant="ghost"
										onClick={() => setStep(noAuth ? 'toolkit' : 'credential')}
									>
										<ArrowLeft className="h-4 w-4" /> Back
									</Button>
									<Button variant="primary" onClick={() => setStep('review')}>
										Review <ArrowRight className="h-4 w-4" />
									</Button>
								</StepActions>
							</StepBody>
						)}

						{step === 'review' && (
							<StepBody
								title="Review & grant"
								blurb="Approving wires this up and lets the agent call the API. Here's exactly what will happen:"
							>
								<dl className="border-border divide-border divide-y rounded-lg border text-sm">
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
								<StepActions>
									<Button variant="ghost" onClick={() => setStep('rules')}>
										<ArrowLeft className="h-4 w-4" /> Back
									</Button>
									<Button
										variant="primary"
										onClick={handleSubmit}
										loading={busy}
										disabled={busy}
									>
										<ShieldCheck className="h-4 w-4" /> Approve &amp; grant
										access
									</Button>
								</StepActions>
							</StepBody>
						)}

						{step === 'done' && (
							<div className="flex flex-col items-center gap-4 py-4 text-center">
								{outcome === 'granted' ? (
									<>
										<div className="bg-success/10 flex h-12 w-12 items-center justify-center rounded-full">
											<CheckCircle2 className="text-success h-7 w-7" />
										</div>
										<div className="space-y-1">
											<p className="text-foreground font-medium">
												Access granted
											</p>
											<p className="text-muted-foreground text-sm">
												{apiLabel(apiRef)} is now callable by this agent.
											</p>
										</div>
									</>
								) : (
									<>
										<div className="bg-danger/10 flex h-12 w-12 items-center justify-center rounded-full">
											<XCircle className="text-danger h-7 w-7" />
										</div>
										<p className="text-danger max-w-sm text-sm">
											{error ?? 'The request could not be fully approved.'}
										</p>
									</>
								)}
								<Button variant="primary" onClick={onClose}>
									Done
								</Button>
							</div>
						)}
					</div>
				</div>
			</Dialog>

			<CreateCredentialDialog
				open={credentialDialogOpen}
				onClose={() => setCredentialDialogOpen(false)}
				onCreated={handleCredentialCreated}
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
		<section className="space-y-4">
			<div className="space-y-1">
				<h3 className="text-foreground text-sm font-semibold">{title}</h3>
				<p className="text-muted-foreground text-sm">{blurb}</p>
			</div>
			{children}
		</section>
	);
}

/** The footer action row for a step (Back left, primary right). */
function StepActions({ children }: { children: React.ReactNode }) {
	return <div className="flex items-center justify-between pt-1">{children}</div>;
}

/** One label/value row in the review summary. */
function SummaryRow({ label, children }: { label: string; children: React.ReactNode }) {
	return (
		<div className="flex items-start gap-4 px-3 py-2">
			<dt className="text-muted-foreground w-24 shrink-0 text-xs font-medium tracking-wide uppercase">
				{label}
			</dt>
			<dd className="text-foreground min-w-0 flex-1 break-words">{children}</dd>
		</div>
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
		<ol className="space-y-1">
			{steps.map((s, i) => {
				const done = step === 'done' || i < activeIndex;
				const active = i === activeIndex && step !== 'done';
				return (
					<li key={s.key} className="flex items-start gap-3">
						<div className="flex flex-col items-center">
							<span
								className={
									done
										? 'bg-primary text-background flex h-6 w-6 items-center justify-center rounded-full text-xs font-semibold'
										: active
											? 'border-primary text-primary flex h-6 w-6 items-center justify-center rounded-full border-2 text-xs font-semibold'
											: 'border-border text-muted-foreground flex h-6 w-6 items-center justify-center rounded-full border text-xs'
								}
							>
								{done ? <CheckCircle2 className="h-4 w-4" /> : i + 1}
							</span>
							{i < steps.length - 1 && (
								<span
									className={done ? 'bg-primary h-5 w-px' : 'bg-border h-5 w-px'}
									aria-hidden="true"
								/>
							)}
						</div>
						<div className="pb-1">
							<p
								className={
									active
										? 'text-foreground text-sm font-medium'
										: done
											? 'text-foreground text-sm'
											: 'text-muted-foreground text-sm'
								}
							>
								{s.label}
							</p>
							<p className="text-muted-foreground text-xs">{s.hint}</p>
						</div>
					</li>
				);
			})}
		</ol>
	);
}
