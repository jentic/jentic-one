import { StepRow } from './StepRow';
import { AppLink } from '@/components/ui/AppLink';
import { VendorIcon } from '@/components/discovery/VendorIcon';

interface OverviewBodyProps {
	steps: any[];
	involvedApis: string[];
}

/**
 * Two-column overview pane for the workflow detail surface. Left
 * column is the ordered step list; right rail shows the APIs the
 * workflow touches. Each API chip links into Discover so the user can
 * drill into the underlying OpenAPI document without leaving the
 * workflow context.
 */
export function OverviewBody({ steps, involvedApis }: OverviewBodyProps) {
	return (
		<div className="grid gap-4 md:grid-cols-3" data-testid="workflow-overview">
			<section
				className="border-border/60 bg-card rounded-xl border p-5 md:col-span-2"
				data-testid="workflow-overview-steps-card"
			>
				<div className="flex items-baseline justify-between">
					<h2 className="text-foreground text-sm font-semibold">Operations</h2>
					<span className="text-muted-foreground text-xs">
						{steps.length} step{steps.length === 1 ? '' : 's'}
					</span>
				</div>

				{steps.length === 0 ? (
					<p className="text-muted-foreground mt-4 text-xs italic">
						This workflow has no steps yet.
					</p>
				) : (
					<ol className="mt-4 space-y-2" data-testid="workflow-overview-steps">
						{steps.map((step, i) => (
							<StepRow
								key={step.stepId ?? step.step_id ?? step.id ?? step.name ?? i}
								index={i}
								isLast={i === steps.length - 1}
								step={step}
								involvedApis={involvedApis}
							/>
						))}
					</ol>
				)}
			</section>

			<aside className="space-y-4">
				<section
					className="border-border/60 bg-card rounded-xl border p-5"
					data-testid="workflow-overview-apis"
				>
					<div className="flex items-baseline justify-between">
						<h2 className="text-foreground text-sm font-semibold">APIs involved</h2>
						<span className="text-muted-foreground text-xs">{involvedApis.length}</span>
					</div>
					{involvedApis.length === 0 ? (
						<p className="text-muted-foreground mt-3 text-xs italic">
							No APIs declared.
						</p>
					) : (
						<ul className="mt-3 space-y-1.5">
							{involvedApis.map((apiId) => (
								<li key={apiId}>
									<AppLink
										href={`/discover?inspect=${encodeURIComponent(apiId)}`}
										className="border-border/40 hover:border-primary/40 hover:bg-muted/50 group flex items-center gap-2.5 rounded-lg border p-2 transition-colors"
										data-testid="workflow-overview-api-chip"
										data-api-id={apiId}
									>
										<VendorIcon name={apiId} vendor={apiId} size="sm" />
										<span className="text-foreground group-hover:text-primary truncate text-xs font-medium">
											{apiId}
										</span>
									</AppLink>
								</li>
							))}
						</ul>
					)}
				</section>
			</aside>
		</div>
	);
}
