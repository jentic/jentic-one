/**
 * WorkspaceCatalogFooter — quiet, single-line catalog CTA at the very bottom of
 * the Workspace page. Faithful port of jentic-mini's footer: the user came here
 * for *their* APIs; if they didn't find one, the public catalog is one click
 * away in Discover — but we deliberately don't shove a second feed at them.
 */
import { Compass } from 'lucide-react';
import { AppLink } from '@/shared/ui';
import { ROUTES } from '@/shared/app/routes';

export function WorkspaceCatalogFooter() {
	return (
		<div
			className="border-border/40 text-muted-foreground flex items-center justify-between gap-3 border-t pt-4 text-sm"
			data-testid="workspace-catalog-footer"
		>
			<span className="inline-flex items-center gap-2">
				<Compass size={14} aria-hidden="true" className="text-muted-foreground/70" />
				Looking for something else?
			</span>
			<AppLink
				href={ROUTES.discover}
				className="text-primary hover:text-primary/80 inline-flex items-center gap-1 font-medium"
				data-testid="workspace-browse-catalog"
			>
				Browse the catalog in Discover →
			</AppLink>
		</div>
	);
}
