import { PageShell } from '@/shared/ui/PageShell';
import { PageHeader } from '@/shared/ui/PageHeader';

/**
 * Placeholder for nav slots whose feature PR hasn't landed yet. Feature PRs
 * register their real route in `shared/app/routes.ts` (moduleRoutes), which
 * takes precedence over this catch-all.
 *
 * Uses `PageShell` + `PageHeader` so the placeholder lays out exactly like a
 * real page (full-bleed header band, shared gutter) under the app shell.
 */
export function PlaceholderPage({ title }: { title: string }) {
	return (
		<PageShell>
			<PageHeader
				title={title}
				subtitle="This area is part of the UI migration and lands in a follow-up PR."
				animated={false}
			/>
		</PageShell>
	);
}
