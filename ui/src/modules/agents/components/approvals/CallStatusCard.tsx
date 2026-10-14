import { ArrowUpRight, Check } from 'lucide-react';
import { AppLink, Badge } from '@/shared/ui';
import { ROUTE_PATHS } from '@/shared/app/routes';
import {
	CALL_STATUS_LABELS,
	CALL_STATUS_VARIANT,
	CALL_STEPS,
	callStep,
	describeCallStatus,
	toCallStatus,
	type CallStatusValue,
	type CallStep,
} from '@/modules/agents/lib/approvalState';
import { ReviewPanel, ReviewSection } from '@/modules/agents/components/approvals/ReviewLayout';

/** The last path segment of a HAL link (`/executions/exec_1` → `exec_1`). */
function idFromLink(link: string | null | undefined): string | null {
	if (!link) return null;
	const last = link.split('?')[0].split('/').filter(Boolean).pop();
	return last ? decodeURIComponent(last) : null;
}

const STEP_LABELS: Record<Exclude<CallStep, 'outcome'>, string> = {
	held: 'Held',
	queued: 'Approved',
	running: 'Running',
};

interface CallStatusCardProps {
	/** The decision (`pending`, `approved`, `denied`, `expired`, `withdrawn`). */
	state: string;
	/** Where the call stands (`held` … `completed`); null when the server does not say. */
	executionStatus: string | null | undefined;
	/** The execution record id, once the approved call has run. */
	executionId: string | null | undefined;
	/** The held execution's `_links.execution`, present once the record exists. */
	executionLink: string | null | undefined;
}

/**
 * Where a held call stands: its status as a badge with a one-line reading,
 * the steps of an approved call (held, approved, running, its outcome), and
 * the execution record once the call has run.
 */
export function CallStatusCard({
	state,
	executionStatus,
	executionId,
	executionLink,
}: CallStatusCardProps) {
	const approved = state === 'approved';
	const reported = toCallStatus(executionStatus);
	// An approved call the worker has not picked up yet can still read `held`.
	const status: CallStatusValue | null = approved && reported === 'held' ? 'queued' : reported;
	// The execution record exists once the server links it.
	const recordId = executionLink ? (executionId ?? idFromLink(executionLink)) : null;
	const current: CallStep | null = status ? callStep(status) : null;

	return (
		<ReviewSection
			title="Status"
			action={
				status && (approved || state === 'pending') ? (
					<CallStatusBadge status={status} />
				) : null
			}
		>
			<ReviewPanel className="flex flex-col gap-3">
				<p className="text-sm">{describeCallStatus(state, status)}</p>
				{approved && current && (
					<ol aria-label="Call progress" className="flex flex-wrap items-center gap-2">
						{CALL_STEPS.map((step, i) => {
							const currentIndex = CALL_STEPS.indexOf(current);
							const done = i < currentIndex;
							const isCurrent = i === currentIndex;
							const label =
								step === 'outcome'
									? status && callStep(status) === 'outcome'
										? CALL_STATUS_LABELS[status]
										: 'Finished'
									: STEP_LABELS[step];
							return (
								<li
									key={step}
									aria-current={isCurrent ? 'step' : undefined}
									className="flex items-center gap-2"
								>
									{isCurrent && status ? (
										<Badge
											variant={
												step === 'queued'
													? 'success'
													: CALL_STATUS_VARIANT[status]
											}
										>
											{label}
										</Badge>
									) : (
										<span
											className={
												done
													? 'text-foreground inline-flex items-center gap-1 text-xs'
													: 'text-muted-foreground text-xs'
											}
										>
											{done && (
												<Check className="h-3 w-3" aria-hidden="true" />
											)}
											{label}
										</span>
									)}
									{i < CALL_STEPS.length - 1 && (
										<span
											aria-hidden="true"
											className="text-muted-foreground text-xs"
										>
											→
										</span>
									)}
								</li>
							);
						})}
					</ol>
				)}
				{recordId && (
					<AppLink
						href={ROUTE_PATHS.monitorExecution(recordId)}
						className="text-primary inline-flex items-center gap-1 self-start text-sm font-medium hover:underline"
						aria-label={`View execution ${recordId}`}
					>
						View execution
						<ArrowUpRight className="h-3.5 w-3.5" aria-hidden="true" />
					</AppLink>
				)}
			</ReviewPanel>
		</ReviewSection>
	);
}

/** A call status as a badge, from the module's single status vocabulary. */
export function CallStatusBadge({ status }: { status: CallStatusValue }) {
	return <Badge variant={CALL_STATUS_VARIANT[status]}>{CALL_STATUS_LABELS[status]}</Badge>;
}
