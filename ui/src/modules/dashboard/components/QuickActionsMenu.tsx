/**
 * QuickActionsMenu — the old bottom "Quick actions" band folded into a single
 * dropdown in the page header. The four jump-offs (discover, credential,
 * toolkit, workspace) are setup/navigation shortcuts, not dashboard data —
 * parking them behind one header button returns the page's vertical space to
 * the information layers while keeping every shortcut one click away.
 *
 * Links by URL (root-relative client routes; the router `basename` adds
 * `/app`) rather than importing sibling modules — Dashboard never crosses a
 * module boundary. `ROUTES` is read at render time to dodge the
 * `@/shared/app` barrel's import-cycle TDZ (same reason the old band did).
 */
import { useState, type ComponentType } from 'react';
import { Boxes, ChevronDown, Compass, FolderOpen, KeyRound, Zap } from 'lucide-react';
import { AppLink, Button, MenuPanel, menuItemClass, useDismissable } from '@/shared/ui';
import { ROUTES } from '@/shared/app/routes';

interface QuickAction {
	href: string;
	label: string;
	icon: ComponentType<{ className?: string }>;
}

export function QuickActionsMenu() {
	const [open, setOpen] = useState(false);
	const ref = useDismissable<HTMLDivElement>(open, () => setOpen(false));

	const actions: QuickAction[] = [
		{ href: ROUTES.discover, label: 'Discover APIs', icon: Compass },
		{ href: ROUTES.credentials, label: 'Add credential', icon: KeyRound },
		{ href: ROUTES.toolkits, label: 'Create toolkit', icon: Boxes },
		{ href: ROUTES.workspace, label: 'Open workspace', icon: FolderOpen },
	];

	return (
		<div ref={ref} className="relative">
			<Button
				variant="secondary"
				size="sm"
				aria-haspopup="menu"
				aria-expanded={open}
				onClick={() => setOpen((v) => !v)}
			>
				<Zap className="h-4 w-4" aria-hidden="true" />
				Quick actions
				<ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
			</Button>
			{open && (
				<MenuPanel align="right">
					{actions.map((action) => {
						const Icon = action.icon;
						return (
							<AppLink
								key={action.href}
								href={action.href}
								role="menuitem"
								className={menuItemClass()}
								onClick={() => setOpen(false)}
							>
								<Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
								{action.label}
							</AppLink>
						);
					})}
				</MenuPanel>
			)}
		</div>
	);
}
