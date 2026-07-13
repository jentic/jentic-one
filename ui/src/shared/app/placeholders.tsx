import { PageShell } from '@/shared/ui/PageShell';
import { PageHeader } from '@/shared/ui/PageHeader';
import { useHealth } from '@/shared';

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

/** Dashboard home (placeholder) — also surfaces the live health probe. */
export function DashboardPlaceholder() {
	const state = useHealth();
	return (
		<PageShell>
			<PageHeader
				title="Dashboard"
				subtitle="Foundation shell. Feature pages land next."
				animated={false}
			/>
			<div className="border-border bg-card rounded-lg border p-4">
				{state.status === 'loading' && (
					<p className="text-muted-foreground text-sm">Checking admin surface…</p>
				)}
				{state.status === 'error' && (
					<p className="text-destructive text-sm" role="alert">
						Admin surface unreachable: {state.error.message}
					</p>
				)}
				{state.status === 'ready' && (
					<p className="text-sm">
						Admin surface: <span className="font-semibold">{state.data.status}</span> (
						{state.data.surface})
					</p>
				)}
			</div>
		</PageShell>
	);
}
