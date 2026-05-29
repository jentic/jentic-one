import { Skeleton } from '@/components/ui/Skeleton';
import { WorkflowRow } from '@/components/ui/WorkflowRow';
import { SectionTitle } from '@/components/discovery/SectionTitle';

interface WorkflowsSectionProps {
	workflows: any[] | undefined;
	isLoading: boolean;
}

/**
 * Renders the "Workflows" block on the API detail surface. The block
 * is hidden entirely when no workflows touch this API (vs. showing an
 * empty state) — discovery of new workflows happens elsewhere.
 */
export function WorkflowsSection({ workflows, isLoading }: WorkflowsSectionProps) {
	if (isLoading) {
		return (
			<section>
				<SectionTitle>Workflows</SectionTitle>
				<div className="mt-3 space-y-2">
					<Skeleton className="h-14 w-full rounded-lg" />
					<Skeleton className="h-14 w-full rounded-lg" />
				</div>
			</section>
		);
	}

	if (!workflows || workflows.length === 0) return null;

	return (
		<section>
			<SectionTitle count={workflows.length}>Workflows</SectionTitle>
			<ul className="divide-border/40 -mx-2 mt-3 divide-y">
				{workflows.map((wf: any) => (
					<li key={wf.slug}>
						<WorkflowRow
							name={wf.name || wf.slug}
							description={wf.description}
							stepsCount={
								Array.isArray(wf.steps) ? wf.steps.length : (wf.steps_count ?? null)
							}
							href={`/workspace/workflows/${wf.slug}`}
						/>
					</li>
				))}
			</ul>
		</section>
	);
}
