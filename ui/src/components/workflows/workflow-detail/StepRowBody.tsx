import { ChevronRight } from 'lucide-react';
import { MethodBadge } from '@/components/ui/Badge';
import { VendorIcon } from '@/components/discovery/VendorIcon';

interface StepRowBodyProps {
	title: string;
	summary?: string;
	operationId?: string;
	operationCall: { method: string; path: string } | null;
	apiId?: string;
	description?: string;
	paramsCount: number;
	outputsCount: number;
	criteriaCount: number;
	canNavigate: boolean;
}

/**
 * The visual content of a single Arazzo step row. Split out from
 * `StepRow` so the same body can be rendered inside either an
 * `<AppLink>` (when the step is navigable into Discover) or a plain
 * `<div>` (when it isn't), without duplicating the markup.
 */
export function StepRowBody({
	title,
	summary,
	operationId,
	operationCall,
	apiId,
	description,
	paramsCount,
	outputsCount,
	criteriaCount,
	canNavigate,
}: StepRowBodyProps) {
	return (
		<>
			<div className="flex min-w-0 items-center gap-2">
				{apiId ? (
					<VendorIcon name={apiId} vendor={apiId} size="sm" className="shrink-0" />
				) : null}
				<div className="min-w-0 flex-1">
					<p className="text-foreground truncate font-mono text-sm font-semibold">
						{title}
					</p>
					{apiId ? (
						<p className="text-muted-foreground mt-0.5 truncate text-[11px]">{apiId}</p>
					) : null}
				</div>
				{canNavigate ? (
					<ChevronRight
						size={14}
						aria-hidden="true"
						className="text-muted-foreground/30 group-hover:text-muted-foreground/80 mt-1 shrink-0 transition-colors"
					/>
				) : null}
			</div>

			{summary ? <p className="text-foreground/80 mt-2 text-sm">{summary}</p> : null}

			{operationCall || operationId ? (
				<div className="mt-2 flex min-w-0 items-center gap-2">
					{operationCall ? (
						<>
							<MethodBadge method={operationCall.method} />
							<code className="text-muted-foreground/90 min-w-0 truncate font-mono text-[11px]">
								{operationCall.path}
							</code>
						</>
					) : operationId ? (
						<code className="bg-muted text-muted-foreground/90 border-border/40 min-w-0 truncate rounded border px-1.5 py-0.5 font-mono text-[11px]">
							{operationId}
						</code>
					) : null}
				</div>
			) : null}

			{description ? (
				<p className="text-muted-foreground mt-2 text-xs leading-relaxed">{description}</p>
			) : null}

			{paramsCount + outputsCount + criteriaCount > 0 ? (
				<div
					className="text-muted-foreground/80 border-border/40 mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 border-t pt-2 text-[10px]"
					data-testid="workflow-step-meta"
				>
					{paramsCount > 0 ? (
						<span>
							<span className="text-foreground/80 font-medium">{paramsCount}</span>{' '}
							param{paramsCount === 1 ? '' : 's'}
						</span>
					) : null}
					{outputsCount > 0 ? (
						<span>
							<span className="text-foreground/80 font-medium">{outputsCount}</span>{' '}
							output{outputsCount === 1 ? '' : 's'}
						</span>
					) : null}
					{criteriaCount > 0 ? (
						<span>
							<span className="text-foreground/80 font-medium">{criteriaCount}</span>{' '}
							check{criteriaCount === 1 ? '' : 's'}
						</span>
					) : null}
				</div>
			) : null}
		</>
	);
}
