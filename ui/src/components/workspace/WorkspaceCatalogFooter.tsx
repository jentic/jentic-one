import { Compass } from 'lucide-react';
import { AppLink } from '@/components/ui/AppLink';

/**
 * Quiet, single-line catalog CTA rendered at the very bottom of the
 * Workspace page. The user came here for *their* stuff; if they didn't
 * find it the catalog is one click away — but we deliberately don't
 * shove cards or a second feed at them on this page.
 */
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
				href="/discover"
				className="text-primary hover:text-primary/80 inline-flex items-center gap-1 font-medium"
				data-testid="workspace-browse-catalog"
			>
				Browse the catalog in Discover →
			</AppLink>
		</div>
	);
}
