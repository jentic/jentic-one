/**
 * FirstRunChecklist — what the Agents page (the app's home) shows while the
 * workspace has NO agents. An empty fleet tells a new operator nothing; this is
 * the shortest path to a working gateway, in the order the concepts build on
 * each other (API → credential → agent, whose create flow grants the APIs).
 *
 * Deliberately dumb about progress: it renders only while the fleet is empty,
 * so there is no per-step "done" state to track — creating (or registering)
 * the first agent swaps the page to the fleet view on the next refetch.
 */
import type { ComponentType, ReactNode } from 'react';
import { ArrowUpRight, Bot, Compass, KeyRound, Rocket } from 'lucide-react';
import { AppLink, Card } from '@/shared/ui';
import { ROUTES, ROUTE_PATHS } from '@/shared/app';

interface SetupStep {
	/** A link to another surface, or — for the step this page owns — an action. */
	target: { href: string } | { onClick: () => void };
	title: string;
	description: string;
	icon: ComponentType<{ className?: string }>;
}

const stepCardClass = 'flex h-full flex-col gap-3 p-4 text-left';

function StepCard({ index, step }: { index: number; step: SetupStep }) {
	const Icon = step.icon;
	const body: ReactNode = (
		<>
			<span className="flex items-center justify-between">
				<span className="bg-muted text-muted-foreground ring-border group-hover:text-primary flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ring-1 transition-colors">
					<Icon className="h-5 w-5" aria-hidden="true" />
				</span>
				<span className="text-muted-foreground font-mono text-xs">Step {index + 1}</span>
			</span>
			<span className="flex-1">
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
			</span>
		</>
	);
	if ('href' in step.target) {
		return (
			<AppLink href={step.target.href} className="group block h-full">
				<Card hoverable className={stepCardClass}>
					{body}
				</Card>
			</AppLink>
		);
	}
	// A real button (not a Card in one): a <div> can't sit inside a <button>.
	return (
		<button
			type="button"
			onClick={step.target.onClick}
			className="group bg-card border-border shadow-card card-lift hover:border-primary/50 hover:shadow-card-hover hover:bg-muted/40 focus-visible:ring-ring h-full w-full rounded-xl border focus-visible:ring-2 focus-visible:outline-none"
		>
			<span className={stepCardClass}>{body}</span>
		</button>
	);
}

export function FirstRunChecklist({ onCreateAgent }: { onCreateAgent: () => void }) {
	const steps: SetupStep[] = [
		{
			target: { href: ROUTES.discover },
			title: 'Discover an API',
			description: 'Browse the catalog and register the APIs your agents will call.',
			icon: Compass,
		},
		{
			// The inventory is a sheet on this page, and this step's whole job
			// is to produce a credential, so it opens the wizard.
			target: { href: ROUTE_PATHS.credentialInventory({ create: true }) },
			title: 'Add a credential',
			description: 'Store the API key or OAuth secret the gateway will inject.',
			icon: KeyRound,
		},
		{
			target: { onClick: onCreateAgent },
			title: 'Create an agent',
			description:
				'Give it the APIs it may call in the same flow — or let it register itself.',
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
						No agents yet. Work through these steps and this page becomes your fleet:
						each agent, the APIs it can call, and what it's doing.
					</p>
				</div>
			</div>
			<ol className="grid grid-cols-1 gap-3 sm:grid-cols-3">
				{steps.map((step, index) => (
					<li key={step.title}>
						<StepCard index={index} step={step} />
					</li>
				))}
			</ol>
		</section>
	);
}
