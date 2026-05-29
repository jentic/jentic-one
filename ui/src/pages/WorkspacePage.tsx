import { useCallback, useState } from 'react';
import { PageShell } from '@/components/layout/PageShell';
import { PageHeader } from '@/components/ui/PageHeader';
import { PageHelp } from '@/components/ui/PageHelp';
import { KeyboardShortcutsBar, MOD_KEY } from '@/components/ui/KeyboardShortcutsBar';
import {
	ImportSourceDialog,
	type ImportTab,
	WorkspaceAddButton,
	WorkspaceView,
} from '@/components/workspace';

/**
 * Workspace page — the user's home base.
 *
 * Intentionally a *home* surface, not a catalog browser:
 *
 *  - Top-of-page **stats strip** answers "how big is my workspace and
 *    when did anything last happen here?" at a glance — no toggle to
 *    flip, no source axis to learn.
 *  - **`+ Add` button** in the page header opens a single import dialog
 *    that handles both APIs (OpenAPI) and workflows (Arazzo). The same
 *    dialog is also reachable from each section's empty state, so
 *    first-run users see an obvious next action.
 *  - Two stacked sections, **APIs** and **Workflows**, both visible by
 *    default. The Workflows section now stays mounted even with zero
 *    workflows — the empty block carries a primary CTA that re-opens
 *    the dialog targeted at the right tab.
 *  - In-memory **filter** scoped to the workspace. Catalog-wide
 *    semantic search lives at `/discover`; we don't conflate the two.
 *  - **Browse the catalog →** link as a single quiet line at the very
 *    bottom of the page, never as a competing feed of cards.
 *
 * This page does NOT mount `<DiscoveryView>` — that's intentional. The
 * two pages serve different jobs and previously trying to share one
 * component kept collapsing them into the same visual surface.
 */
export default function WorkspacePage() {
	// Single open-state for both entry points (header button + empty-state
	// CTAs). The dialog is mounted once at the page root and re-targeted
	// via `defaultTab` so we don't end up with overlapping dialogs.
	const [importOpen, setImportOpen] = useState(false);
	const [importTab, setImportTab] = useState<ImportTab>('api');

	const requestImport = useCallback((tab: ImportTab) => {
		setImportTab(tab);
		setImportOpen(true);
	}, []);

	return (
		<>
			<PageShell className="md:pb-12">
				<PageHeader
					title="Workspace"
					subtitle="Your APIs and workflows, at a glance."
					actions={
						<>
							<WorkspaceAddButton onSelect={requestImport} />
							<PageHelp
								title="About Workspace"
								intro={
									<p>
										<strong>Workspace</strong> is your home base. Everything
										you've imported into Jentic Mini lives here — APIs you've
										added credentials for and the workflows that orchestrate
										them. The catalog is one click away when you need it, but it
										doesn't fight for attention on this page.
									</p>
								}
								sections={[
									{
										heading: 'Reading the page',
										body: (
											<p>
												The strip at the top shows your{' '}
												<strong>API count</strong>,{' '}
												<strong>workflow count</strong>,{' '}
												<strong>toolkit count</strong>, and{' '}
												<strong>most recent activity</strong>. Below it, two
												grids list your APIs and workflows. Click an API
												tile to open its detail sheet; click a workflow tile
												to jump to the workflow editor.
											</p>
										),
									},
									{
										heading: 'Adding things',
										body: (
											<p>
												Use <strong>+ Add</strong> in the page header to
												import an OpenAPI spec or an Arazzo workflow — by
												URL, paste, or file upload. Each section's empty
												state offers the same shortcut. To import from the
												public catalog instead, open{' '}
												<strong>Discover</strong> from the sidebar.
											</p>
										),
									},
									{
										heading: 'Filtering',
										body: (
											<p>
												The filter box narrows the tiles you can see{' '}
												<em>right now</em> — it's an in-memory match against
												name and description, not a catalog search. To
												search the public catalog by intent, open{' '}
												<strong>Discover</strong> from the sidebar (or use
												the link at the bottom of this page).
											</p>
										),
									},
								]}
								shortcuts={[
									{ keys: ['/'], label: 'Focus filter' },
									{
										keys: ['↑', '↓', '←', '→'],
										label: 'Move focus between tiles',
									},
									{ keys: ['Home', 'End'], label: 'Jump to first / last tile' },
									{ keys: ['Enter'], label: 'Open the focused tile' },
									{ keys: ['Esc'], label: 'Clear filter' },
									{ keys: [MOD_KEY, '/'], chord: true, label: 'Show this help' },
								]}
							/>
						</>
					}
				/>
				<WorkspaceView onRequestImport={requestImport} />
				<ImportSourceDialog
					open={importOpen}
					onClose={() => setImportOpen(false)}
					defaultTab={importTab}
				/>
			</PageShell>

			<KeyboardShortcutsBar
				shortcuts={[
					{ keys: ['/'], label: 'filter' },
					{ keys: ['↑', '↓', '←', '→'], label: 'navigate' },
					{ keys: ['Enter'], label: 'open' },
					{ keys: ['Esc'], label: 'clear' },
					{ keys: [MOD_KEY, '/'], chord: true, label: 'help' },
				]}
			/>
		</>
	);
}
