import type { ComponentType } from 'react';
import { Compass, KeyRound, Boxes, FolderOpen, ArrowUpRight } from 'lucide-react';
import { Card, AppLink } from '@/shared/ui';
import { ROUTES } from '@/shared/app/routes';

interface QuickAction {
	href: string;
	label: string;
	icon: ComponentType<{ className?: string }>;
}

/**
 * Jump-off links to the other surfaces. Paths are root-relative client routes
 * (the router `basename` adds the `/app` prefix). We link by URL rather than
 * importing sibling modules — Dashboard never crosses a module boundary.
 *
 * Built inside the component (not a module-level const) so the `ROUTES` reads
 * happen at render time; reading them at module-eval time can hit a temporal
 * dead zone under the `@/shared/app` barrel's import cycle.
 */
export function QuickActions() {
	const actions: QuickAction[] = [
		{
			href: ROUTES.discover,
			label: 'Discover APIs',
			icon: Compass,
		},
		{
			href: ROUTES.credentials,
			label: 'Add credential',
			icon: KeyRound,
		},
		{
			href: ROUTES.toolkits,
			label: 'Create toolkit',
			icon: Boxes,
		},
		{
			href: ROUTES.workspace,
			label: 'Open workspace',
			icon: FolderOpen,
		},
	];
	return (
		<div>
			<h2 className="font-heading text-foreground mb-3 font-semibold">Quick actions</h2>
			<div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
				{actions.map((action) => {
					const Icon = action.icon;
					return (
						<AppLink key={action.href} href={action.href} className="group block">
							<Card
								hoverable
								className="group-focus-visible:border-primary/50 flex items-center gap-3 px-4 py-3.5"
							>
								<span className="bg-muted text-muted-foreground ring-border group-hover:text-primary flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ring-1 transition-colors">
									<Icon className="h-5 w-5" aria-hidden="true" />
								</span>
								<span className="text-foreground flex-1 text-sm font-medium">
									{action.label}
								</span>
								<ArrowUpRight
									className="text-muted-foreground h-4 w-4 shrink-0 opacity-0 transition-all group-hover:translate-x-0.5 group-hover:-translate-y-0.5 group-hover:opacity-100"
									aria-hidden="true"
								/>
							</Card>
						</AppLink>
					);
				})}
			</div>
		</div>
	);
}
