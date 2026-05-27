import { StepRowBody } from './StepRowBody';
import { AppLink } from '@/components/ui/AppLink';

interface StepRowProps {
	index: number;
	isLast: boolean;
	step: any;
	involvedApis: string[];
}

/**
 * One row in the Overview Steps list. Each step is an Arazzo step
 * (`stepId` + an underlying OpenAPI operation), so the row's title is
 * the `stepId` itself — never "Step N", which loses information. Below
 * the title we surface the operationId, the API it calls into, and
 * quiet counters for parameters / outputs / successCriteria so a long
 * workflow scans like a checklist.
 *
 * When we can resolve which API the step targets, the whole row is a
 * link into Discover's API detail sheet (`/discover?inspect=<api>&op=
 * <operationId>`), letting the user drill from "what does this
 * workflow do" to "what does this individual operation look like".
 */
export function StepRow({ index, isLast, step, involvedApis }: StepRowProps) {
	// Workflow steps come back from the API in the shape
	// `{ id, operation, description }` (see WorkflowStep in types.ts),
	// but some Arazzo flavours use camelCase (`stepId`, `operationId`,
	// `operationPath`). We accept both so this row works against either
	// the digested workflow payload and the raw Arazzo document.
	const stepId: string | undefined =
		step.stepId ?? step.step_id ?? step.id ?? step.name ?? undefined;
	const operationId: string | undefined = step.operationId ?? step.operation_id ?? undefined;
	const operationPath: string | undefined =
		step.operationPath ?? step.operation_path ?? step.operation ?? undefined;
	const description: string | undefined =
		typeof step.description === 'string' ? step.description : undefined;
	const summary: string | undefined =
		typeof step.summary === 'string' && step.summary !== stepId ? step.summary : undefined;

	// Headline = stepId (the actual identifier the workflow author chose),
	// falling back to summary or "Step N" only when the step doesn't carry
	// a stepId (rare; happens for malformed Arazzo).
	const title = stepId ?? summary ?? `Step ${index + 1}`;
	const showSummaryAsCaption = summary && summary !== title;

	// Try to resolve the API host the step calls into. Two shapes we
	// see in real Arazzo: dotted `vendor.operationId` or a JSON Pointer
	// in `operationPath` of the form `$sourceDescriptions.<vendor>.url#…`.
	// The raw key (`zendesk_api`, `slack`) isn't useful for VendorIcon
	// lookup though — we need the involved-APIs domain (`zendesk.com`).
	const rawApiHint: string | undefined = (() => {
		if (operationId && operationId.includes('.')) return operationId.split('.')[0];
		if (operationPath) {
			const m = operationPath.match(/\$sourceDescriptions\.([^.]+)\.url/);
			if (m) return m[1];
		}
		return undefined;
	})();

	// Match the raw hint to one of the workflow's declared `involved_apis`
	// so we can render the VendorIcon and proper domain label. Uses the
	// SLD as the join key (zendesk_api → zendesk.com via "zendesk").
	const apiId: string | undefined = (() => {
		if (!rawApiHint) return undefined;
		const needle = rawApiHint.toLowerCase().replace(/[_-]?api$/i, '');
		const exact = involvedApis.find((a) => a.toLowerCase() === rawApiHint.toLowerCase());
		if (exact) return exact;
		const byPrefix = involvedApis.find((a) => a.toLowerCase().startsWith(needle));
		return byPrefix ?? rawApiHint;
	})();

	// When a step references a raw OpenAPI operation by path/method
	// (e.g. `…#/paths/~1api~1v2~1tickets~1{ticket_id}/get`) we don't
	// have a real operationId to display, but we *can* surface a
	// `MethodBadge + path` token so the row reads like a Discover row.
	const operationCall: { method: string; path: string } | null = (() => {
		if (!operationPath) return null;
		const m = operationPath.match(/#\/paths\/([^/]+)\/([a-z]+)$/i);
		if (!m) return null;
		const path = decodeURIComponent(m[1].replace(/~1/g, '/').replace(/~0/g, '~'));
		return { method: m[2].toUpperCase(), path };
	})();

	// `parameters` is an array of {name, in, value}; `successCriteria` is
	// an array of {condition}; `outputs` is a map name→JSONPath. Any of
	// them may be missing on minimally-specified steps.
	const paramsCount = Array.isArray(step.parameters) ? step.parameters.length : 0;
	const criteriaCount = Array.isArray(step.successCriteria) ? step.successCriteria.length : 0;
	const outputsCount =
		step.outputs && typeof step.outputs === 'object' ? Object.keys(step.outputs).length : 0;

	const canNavigate = Boolean(apiId && operationId);
	const href = canNavigate
		? `/discover?inspect=${encodeURIComponent(apiId!)}&op=${encodeURIComponent(operationId!)}`
		: undefined;

	const rowBodyClass = canNavigate
		? 'group border-border/50 bg-background/40 hover:border-primary/40 hover:bg-muted/40 focus-visible:border-primary/40 focus-visible:bg-muted/40 block min-w-0 rounded-lg border p-3 text-left no-underline transition-colors focus-visible:outline-none'
		: 'border-border/50 bg-background/40 block min-w-0 rounded-lg border p-3 text-left';

	const body = (
		<StepRowBody
			title={title}
			summary={showSummaryAsCaption ? summary : undefined}
			operationId={operationId}
			operationCall={operationCall}
			apiId={apiId}
			description={description}
			paramsCount={paramsCount}
			outputsCount={outputsCount}
			criteriaCount={criteriaCount}
			canNavigate={canNavigate}
		/>
	);

	return (
		<li
			className="relative grid grid-cols-[24px_1fr] gap-3"
			data-testid="workflow-step"
			data-step-id={stepId}
		>
			<div className="relative flex justify-center pt-3">
				<span className="bg-muted text-muted-foreground border-border/60 relative z-10 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full border font-mono text-[11px] font-semibold">
					{index + 1}
				</span>
				{!isLast ? (
					<span
						aria-hidden="true"
						className="bg-border/60 absolute top-10 bottom-[-8px] left-1/2 w-px -translate-x-1/2"
					/>
				) : null}
			</div>

			{canNavigate ? (
				<AppLink
					href={href!}
					className={rowBodyClass}
					aria-label={`Open ${title} in Discover`}
					data-testid="workflow-step-body"
				>
					{body}
				</AppLink>
			) : (
				<div className={rowBodyClass} data-testid="workflow-step-body">
					{body}
				</div>
			)}
		</li>
	);
}
