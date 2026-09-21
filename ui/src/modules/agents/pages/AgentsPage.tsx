/**
 * Agents page — operator surface for the agent lifecycle (D19: agents only,
 * no service-accounts roster).
 *
 * One flat surface (`FlatAgentsSection`): an "Awaiting approval" band, the
 * agent pill strip (selection in `?agent=`), and the selected agent's API
 * tile grid. The per-agent console at `/agents/:id` stays reachable by deep
 * link, but the dock's sheets carry every fact it holds, so this surface
 * offers no jump-off to it. Service accounts keep only their detail page
 * (`/agents/service-accounts/:id`, URL-reachable).
 *
 * The page header also carries the org-wide `Credentials` control (D20):
 * the dock below is agent-scoped only, so the credential inventory sheet
 * opens from PAGE level — dock = this agent; page level = org-wide. Living
 * on the header (not inside the flat section) keeps the inventory reachable
 * even when the fleet is empty.
 *
 * The header's filter and `New agent` are page-level too, and the page owns
 * the surface's keyboard map: `/` focuses the filter, `n` opens the create
 * sheet, `a` (bound by the flat section, which owns that verb) adds APIs. The
 * map is documented in `PageHelp`, where the rest of the surface's explanation
 * lives — a permanent strip across the page foot costs every operator screen
 * height forever to state something each of them needs to read once.
 *
 * Because the inventory is a sheet and not a route, cross-module links reach
 * it through `?credentials` (`=new` to land on the create wizard), built by
 * `ROUTE_PATHS.credentialInventory`. The param is spent on arrival: it opens
 * the sheet once and is dropped from the URL, so dismissing the sheet isn't
 * undone by the link it came in on.
 */
import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router';
import { Filter, Plus, Wallet } from 'lucide-react';
import {
	Button,
	FOOTER_ACTION_BAR_PAGE_PADDING,
	Kbd,
	PageShell,
	PageHeader,
	PageHelp,
	SearchInput,
	type KeyboardShortcut,
} from '@/shared/ui';
import { useHotkey } from '@/shared/hooks';
import { FlatAgentsSection } from '@/modules/agents/components/flat/FlatAgentsSection';
import { CredentialInventorySheet } from '@/modules/agents/components/flat/CredentialInventorySheet';

/** The surface's whole keyboard map, listed on demand in `PageHelp`. */
const SHORTCUTS: KeyboardShortcut[] = [
	{ keys: ['a'], label: 'add API' },
	{ keys: ['n'], label: 'new agent' },
	{ keys: ['/'], label: 'search' },
	{ keys: ['Esc'], label: 'close' },
];

export default function AgentsPage() {
	const [agentCreateOpen, setAgentCreateOpen] = useState(false);
	const [inventoryOpen, setInventoryOpen] = useState(false);
	const [inventoryWantsCreate, setInventoryWantsCreate] = useState(false);

	// The fleet filter and "New agent" are PAGE-level controls, so they sit in
	// the header beside Credentials rather than inside the strip. The filter
	// text lives here and the strip consumes it.
	const [agentFilter, setAgentFilter] = useState('');
	const filterRef = useRef<HTMLInputElement | null>(null);
	useHotkey('/', () => filterRef.current?.focus());
	useHotkey('n', () => setAgentCreateOpen(true));

	const [searchParams, setSearchParams] = useSearchParams();
	const inventoryParam = searchParams.get('credentials');
	useEffect(() => {
		if (inventoryParam == null) return;
		setInventoryOpen(true);
		setInventoryWantsCreate(inventoryParam === 'new');
		// `replace`, and only this key: the selection in `?agent=` is the
		// operator's own place on the surface and must survive the rewrite.
		setSearchParams(
			(prev) => {
				const next = new URLSearchParams(prev);
				next.delete('credentials');
				return next;
			},
			{ replace: true },
		);
	}, [inventoryParam, setSearchParams]);

	return (
		// The surface mounts the fixed AgentDock (`FooterActionBar`), so the
		// page container pads its bottom to keep the last row of tiles clear of
		// the dock — and, below `md`, of the bottom nav (risk O2).
		<PageShell className={FOOTER_ACTION_BAR_PAGE_PADDING}>
			<PageHeader
				title="Agents"
				subtitle="Approve, deny, and govern agents across their lifecycle."
				actions={
					<>
						{/* The fleet's own controls lead: filter it, then add to
						    it. Both act on the strip below, which is why they
						    belong to the page and not to the strip's own row. */}
						<div className="relative">
							<SearchInput
								ref={filterRef}
								size="sm"
								value={agentFilter}
								onValueChange={setAgentFilter}
								icon={<Filter className="h-3.5 w-3.5" />}
								placeholder="Filter agents…"
								aria-label="Filter agents"
								className="w-40 lg:w-48"
							/>
							{!agentFilter && (
								<Kbd className="pointer-events-none absolute top-1/2 right-2 hidden -translate-y-1/2 sm:inline-flex">
									/
								</Kbd>
							)}
						</div>
						<Button size="sm" onClick={() => setAgentCreateOpen(true)}>
							<Plus className="h-4 w-4" />
							New agent
						</Button>
						{/* D20: the org-wide inventory trigger — page level, not
						    the dock (every dock verb is agent-scoped). PageHelp
						    keeps the right edge per the page-scaffold rule. */}
						<Button variant="outline" size="sm" onClick={() => setInventoryOpen(true)}>
							<Wallet className="h-4 w-4" />
							Credentials
						</Button>
						<PageHelp
							title="About Agents"
							intro={
								<p>
									Agents register themselves via dynamic client registration and
									land here as <strong>pending</strong>. Approve one to make it
									active, or deny it with a reason.
								</p>
							}
							sections={[
								{
									heading: 'Lifecycle',
									body: (
										<p>
											<strong>Pending</strong> → approve (→ active) or deny (→
											rejected). <strong>Active</strong> can be disabled;{' '}
											<strong>disabled</strong> can be re-enabled. Any
											non-archived actor can be archived (terminal).
										</p>
									),
								},
							]}
							shortcuts={SHORTCUTS}
						/>
					</>
				}
			/>

			<FlatAgentsSection
				createOpen={agentCreateOpen}
				setCreateOpen={setAgentCreateOpen}
				filter={agentFilter}
			/>

			{/* The org-wide credential inventory (plan §4.6), unchanged in
			    presentation — only its trigger moved to page level (D20). */}
			<CredentialInventorySheet
				open={inventoryOpen}
				autoOpenCreate={inventoryWantsCreate}
				onClose={() => {
					setInventoryOpen(false);
					setInventoryWantsCreate(false);
				}}
			/>
		</PageShell>
	);
}
