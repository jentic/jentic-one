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
import { ArrowLeft, ArrowRight, CheckCircle2, Loader2, ShieldCheck } from 'lucide-react';
import { Dialog } from '@/shared/ui/Dialog';
import { Button } from '@/shared/ui/Button';
import { Input } from '@/shared/ui/Input';
import { ErrorAlert } from '@/shared/ui/ErrorAlert';
import { PermissionRuleEditor, type PermissionRuleInput } from '@/shared/ui/PermissionRuleEditor';
import { CreateCredentialDialog } from '@/shared/credentials/components/CreateCredentialDialog';
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

	const handleCredentialCreated = useCallback((info: { credentialId: string }) => {
		setCredentialId(info.credentialId);
		setCredentialDialogOpen(false);
		setStep('rules');
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

	return (
		<>
			<Dialog
				open={open && !credentialDialogOpen}
				onClose={handleCancel}
				title="Set up access"
				subtitle={`Make ${apiLabel(apiRef)} executable for this agent`}
			>
				<div className="space-y-5">
					<Stepper step={step} noAuth={noAuth} />
					{error && <ErrorAlert message={error} />}

					{step === 'toolkit' && (
						<section className="space-y-3">
							<p className="text-muted-foreground text-sm">
								Step 1 — create a toolkit to serve {apiLabel(apiRef)}.
							</p>
							<Input
								aria-label="Toolkit name"
								value={toolkitName}
								onChange={(e) => setToolkitName(e.target.value)}
								placeholder="Toolkit name"
							/>
							<div className="flex justify-end">
								<Button
									variant="primary"
									onClick={handleCreateToolkit}
									disabled={busy || !toolkitName.trim()}
								>
									{busy ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
									Create toolkit
								</Button>
							</div>
						</section>
					)}

					{step === 'credential' && (
						<section className="space-y-3">
							<p className="text-muted-foreground text-sm">
								Step 2 — connect a credential for {apiLabel(apiRef)}
								{detectedAuth ? ` (detected: ${detectedAuth})` : ''}. You enter the
								secret; the agent never sees it.
							</p>
							<div className="flex justify-between">
								<Button variant="ghost" onClick={() => setStep('toolkit')}>
									<ArrowLeft className="h-4 w-4" /> Back
								</Button>
								<Button
									variant="primary"
									onClick={() => setCredentialDialogOpen(true)}
								>
									{credentialId ? 'Credential connected ✓' : 'Connect credential'}
									<ArrowRight className="h-4 w-4" />
								</Button>
							</div>
							{credentialId && (
								<div className="flex justify-end">
									<Button variant="secondary" onClick={() => setStep('rules')}>
										Continue <ArrowRight className="h-4 w-4" />
									</Button>
								</div>
							)}
						</section>
					)}

					{step === 'rules' && (
						<section className="space-y-3">
							<p className="text-muted-foreground text-sm">
								Confirm the permission rules — the agent proposed these from the
								spec. Edit as needed; with no rules every call is denied.
							</p>
							<PermissionRuleEditor rules={rules} onChange={setRules} />
							<div className="flex justify-between">
								<Button
									variant="ghost"
									onClick={() => setStep(noAuth ? 'toolkit' : 'credential')}
								>
									<ArrowLeft className="h-4 w-4" /> Back
								</Button>
								<Button variant="primary" onClick={() => setStep('review')}>
									Review <ArrowRight className="h-4 w-4" />
								</Button>
							</div>
						</section>
					)}

					{step === 'review' && (
						<section className="space-y-3">
							<p className="text-muted-foreground text-sm">
								Approving will bind the credential to the toolkit with these rules
								and bind the agent to the toolkit.
							</p>
							<ul className="text-foreground space-y-1 text-sm">
								<li>Toolkit: {toolkitName}</li>
								<li>
									Credential:{' '}
									{noAuth ? 'none (no-auth API)' : (credentialId ?? '—')}
								</li>
								<li>Rules: {rules.length} defined</li>
							</ul>
							<div className="flex justify-between">
								<Button variant="ghost" onClick={() => setStep('rules')}>
									<ArrowLeft className="h-4 w-4" /> Back
								</Button>
								<Button variant="primary" onClick={handleSubmit} disabled={busy}>
									{busy ? (
										<Loader2 className="h-4 w-4 animate-spin" />
									) : (
										<ShieldCheck className="h-4 w-4" />
									)}
									Approve &amp; grant access
								</Button>
							</div>
						</section>
					)}

					{step === 'done' && (
						<section className="space-y-4 text-center">
							{outcome === 'granted' ? (
								<>
									<CheckCircle2 className="text-success mx-auto h-10 w-10" />
									<p className="text-foreground text-sm">
										Access granted. {apiLabel(apiRef)} is now executable for
										this agent.
									</p>
								</>
							) : (
								<p className="text-danger text-sm">
									{error ?? 'The request could not be fully approved.'}
								</p>
							)}
							<Button variant="primary" onClick={onClose}>
								Done
							</Button>
						</section>
					)}
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

function Stepper({ step, noAuth }: { step: Step; noAuth: boolean }) {
	const steps: { key: Step; label: string }[] = [
		{ key: 'toolkit', label: 'Toolkit' },
		...(noAuth ? [] : [{ key: 'credential' as Step, label: 'Credential' }]),
		{ key: 'rules', label: 'Rules' },
		{ key: 'review', label: 'Review' },
	];
	const activeIndex = steps.findIndex((s) => s.key === step);
	return (
		<ol className="flex items-center gap-2 text-xs">
			{steps.map((s, i) => (
				<li
					key={s.key}
					className={
						i <= activeIndex || step === 'done'
							? 'text-primary font-medium'
							: 'text-muted-foreground'
					}
				>
					{i + 1}. {s.label}
					{i < steps.length - 1 ? ' →' : ''}
				</li>
			))}
		</ol>
	);
}
