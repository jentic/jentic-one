import { StepRowBody } from './StepRowBody';
import { AppLink } from '@/components/ui/AppLink';

interface StepRowProps {
	index: number;
	isLast: boolean;
	step: any;
	involvedApis: string[];
	/**
	 * APIs that exist in the user's workspace. Steps targeting one of
	 * these route into the workspace API view; everything else falls
	 * back to the Discover catalog sheet so the user can inspect or
	 * import the missing API.
	 */
	workspaceApiIds: Set<string>;
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
 * link. If the API exists in the workspace we link into the workspace
 * detail view (`/workspace/apis/<api>`) since the user has already
 * imported it; otherwise we fall back to the Discover catalog sheet
 * (`/discover?inspect=<api>&op=<operationId>`) so the row stays
 * useful for browsing un-imported workflows from the catalog.
 */
export function StepRow({ index, isLast, step, involvedApis, workspaceApiIds }: StepRowProps) {
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
	// Pick the destination based on workspace state. If the API is
	// imported, drop the user on the workspace API view so they stay
	// in their own context. If it isn't, the Discover sheet is the
	// only place that can render this op (and it offers an Import
	// button), so fall back there. We deliberately don't deep-link
	// into a specific operation in the workspace — see the workspace
	// API view for tag/filter affordances instead.
	const isWorkspaceApi = canNavigate && workspaceApiIds.has(apiId!);
	const href = canNavigate
		? isWorkspaceApi
			? `/workspace/apis/${encodeURIComponent(apiId!)}`
			: `/discover?inspect=${encodeURIComponent(apiId!)}&op=${encodeURIComponent(operationId!)}`
		: undefined;
	const ariaLabel = canNavigate
		? isWorkspaceApi
			? `Open ${title} in workspace`
			: `Open ${title} in Discover`
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
					aria-label={ariaLabel}
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
