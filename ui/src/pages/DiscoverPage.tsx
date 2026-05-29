import { PageShell } from '@/components/layout/PageShell';
import { PageHeader } from '@/components/ui/PageHeader';
import { PageHelp } from '@/components/ui/PageHelp';
import { MOD_KEY } from '@/components/ui/KeyboardShortcutsBar';
import { DiscoveryView } from '@/components/discovery';

/**
 * Discover page — the public directory catalog only. The Source axis is
 * hard-coded to `directory` so users come here specifically to find
 * something *new* (not yet in their workspace) and import it. The
 * "import bridge" already exists in `ApiDetailSheet`'s credentials
 * section: opening a directory API and adding a credential moves the
 * API into the workspace.
 *
 * This is a thin shell — all the discovery logic (toolbar, sticky search,
 * filter chips, browse + search modes, sheet, recents, infinite scroll,
 * keyboard shortcuts) is owned by `<DiscoveryView>` and is shared
 * verbatim with `/workspace`. The only differences between the two
 * pages are the page chrome (title/subtitle/help) and the `forcedSource`
 * value passed below.
 */
export default function DiscoverPage() {
	return (
		<PageShell className="md:pb-12" spacing="space-y-0">
			<PageHeader
				title="Discover"
				subtitle="Browse the Jentic public catalog and import what you need."
				actions={
					<PageHelp
						title="About Discover"
						intro={
							<p>
								<strong>Discover</strong> shows the Jentic public catalog — APIs and
								workflows you can import into your workspace. Everything you've
								already imported lives in <strong>Workspace</strong>.
							</p>
						}
						sections={[
							{
								heading: 'Search the catalog',
								body: (
									<p>
										Type an API name or ID in the search bar to filter the
										catalog. Results update as you type.
									</p>
								),
							},
							{
								heading: 'Browse the catalog',
								body: (
									<p>
										With an empty search you see every API as a card grid. The
										list loads more entries as you scroll. Click any card to
										open a side sheet with its description, operations, and
										workflows.
									</p>
								),
							},
							{
								heading: 'Importing into your workspace',
								body: (
									<p>
										Open any card's side sheet and use the{' '}
										<strong>Import to workspace</strong> button. Once imported,
										the API's operations become executable and its workflows are
										available in your workspace.
									</p>
								),
							},
						]}
						shortcuts={[
							{ keys: ['/'], label: 'Focus search' },
							{ keys: ['Esc'], label: 'Clear search (or close sheet)' },
							{ keys: ['↑', '↓', '←', '→'], label: 'Move focus between cards' },
							{ keys: ['Home', 'End'], label: 'Jump to first / last card' },
							{ keys: ['Enter'], label: 'Open the focused card' },
							{ keys: [MOD_KEY, '/'], chord: true, label: 'Show this help' },
						]}
					/>
				}
			/>
			<DiscoveryView forcedSource="directory" />
		</PageShell>
	);
}
