/**
 * ApisSectionNav — the switch between the two halves of the APIs surface:
 * the workspace (APIs this instance has registered, shared by everyone in it)
 * and the public catalog (APIs that could be imported).
 *
 * They are separate modules (`workspace`, `discover`) that mirror separate
 * backend resources (`/apis`, `/catalog`), so each page renders this at the
 * top instead of one page embedding the other. It lives in the shell because
 * modules can't import each other; the nav's single "APIs" entry stays lit
 * on both (`alsoActiveOn`).
 *
 * Real links (not tabs over one panel): each view has its own URL, so a
 * reload, a bookmark, or a shared link lands on the view it names.
 */
import { NavLink } from 'react-router';
import { cn } from '@/shared/lib/utils';
import { ROUTES } from '@/shared/app/routes';

export function ApisSectionNav({ className }: { className?: string }) {
	// Built per render, not at module level: the pages that render this are
	// imported by `routes.ts` itself, so a module-level read of ROUTES would
	// hit it before it's initialised (import cycle).
	const sections = [
		{ to: ROUTES.workspace, label: 'Workspace', hint: 'Registered in this instance' },
		{ to: ROUTES.discover, label: 'Catalog', hint: 'Public APIs you can import' },
	];
	return (
		<nav aria-label="APIs" className={cn('border-border flex gap-1 border-b', className)}>
			{sections.map((section) => (
				<NavLink
					key={section.to}
					to={section.to}
					title={section.hint}
					// Exact on the list path; the per-API detail pages carry
					// their own back link instead of a lit section tab.
					end
					className={({ isActive }) =>
						cn(
							'focus-visible:ring-ring relative -mb-px rounded-t-md border-b-2 px-3 py-2 text-sm font-medium transition-colors focus-visible:ring-2 focus-visible:outline-none',
							isActive
								? 'border-primary text-foreground'
								: 'text-muted-foreground hover:text-foreground hover:bg-muted/50 border-transparent',
						)
					}
				>
					{section.label}
				</NavLink>
			))}
		</nav>
	);
}
