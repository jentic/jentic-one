/**
 * FirstRunChecklist — the landing view for a workspace with NO agents and NO
 * executions yet. A fresh install rendering four empty queue cards and a blank
 * health section tells the operator nothing; this replaces those layers with
 * the shortest path to a working gateway, in the order the concepts build on
 * each other (API → credential → toolkit → agent).
 *
 * Deliberately dumb about progress: it renders only when BOTH probes are empty
 * (`useHasAgents`, recent executions), so there is no per-step "done" state to
 * track — completing any step registers activity and the real dashboard takes
 * over on the next refetch.
 */
import type { ComponentType } from 'react';
import { ArrowUpRight, Boxes, Bot, Compass, KeyRound, Rocket } from 'lucide-react';
import { AppLink, Card } from '@/shared/ui';
import { ROUTES } from '@/shared/app/routes';

interface SetupStep {
	href: string;
	title: string;
	description: string;
	icon: ComponentType<{ className?: string }>;
}

export function FirstRunChecklist() {
	const steps: SetupStep[] = [
		{
			href: ROUTES.discover,
			title: 'Discover an API',
			description: 'Browse the catalog and register the APIs your agents will call.',
			icon: Compass,
		},
		{
			href: ROUTES.credentials,
			title: 'Add a credential',
			description: 'Store the API key or OAuth secret the gateway will inject.',
			icon: KeyRound,
		},
		{
			href: ROUTES.toolkits,
			title: 'Create a toolkit',
			description: 'Bundle operations into the capability an agent may use.',
			icon: Boxes,
		},
		{
			href: ROUTES.agents,
			title: 'Register an agent',
			description: 'Point your agent at the gateway and approve its identity.',
			icon: Bot,
		},
	];

	return (
		<section
			aria-label="Set up your workspace"
			className="border-border/70 from-muted/60 to-card animate-rise rounded-xl border border-dashed bg-gradient-to-b p-6 sm:p-8"
		>
			<div className="mb-6 flex items-start gap-4">
				<div className="text-primary/80 ring-primary/15 bg-primary/5 flex h-12 w-12 shrink-0 items-center justify-center rounded-full ring-1">
					<Rocket className="h-6 w-6" aria-hidden="true" />
				</div>
				<div>
					<h2 className="font-heading text-foreground text-lg font-semibold">
						Set up your workspace
					</h2>
					<p className="text-muted-foreground mt-1 max-w-xl text-sm leading-relaxed">
						Nothing has run through the gateway yet. Work through these four steps and
						this page becomes a live overview of your agents and their API traffic.
					</p>
				</div>
			</div>
			<ol className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
				{steps.map((step, index) => {
					const Icon = step.icon;
					return (
						<li key={step.href}>
							<AppLink href={step.href} className="group block h-full">
								<Card hoverable className="flex h-full flex-col gap-3 p-4">
									<div className="flex items-center justify-between">
										<span className="bg-muted text-muted-foreground ring-border group-hover:text-primary flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ring-1 transition-colors">
											<Icon className="h-5 w-5" aria-hidden="true" />
										</span>
										<span className="text-muted-foreground font-mono text-xs">
											Step {index + 1}
										</span>
									</div>
									<div className="flex-1">
										<span className="text-foreground flex items-center gap-1 text-sm font-medium">
											{step.title}
											<ArrowUpRight
												className="h-3.5 w-3.5 shrink-0 opacity-0 transition-all group-hover:translate-x-0.5 group-hover:-translate-y-0.5 group-hover:opacity-100"
												aria-hidden="true"
											/>
										</span>
										<span className="text-muted-foreground mt-1 block text-xs leading-relaxed">
											{step.description}
										</span>
									</div>
								</Card>
							</AppLink>
						</li>
					);
				})}
			</ol>
		</section>
	);
}
