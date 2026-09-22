/**
 * Agents page — operator surface for the agent lifecycle.
 *
 * The header carries what the agent-scoped dock cannot: the org-wide credential
 * inventory (a sheet reached through `?credentials`, `=new` for the wizard), the
 * fleet filter and `New agent`. It owns the keyboard map documented in `PageHelp`;
 * everything else is `FlatAgentsSection`, which keeps its selection in `?agent=`.
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

	// The fleet filter and "New agent" are PAGE-level controls; the filter text
	// lives here and the strip consumes it.
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
		// The surface mounts the fixed AgentDock, so the page pads its bottom to keep
		// the last row of tiles clear of it — and, below `md`, of the bottom nav.
		<PageShell className={FOOTER_ACTION_BAR_PAGE_PADDING}>
			<PageHeader
				title="Agents"
				subtitle="Approve, deny, and govern agents across their lifecycle."
				actions={
					<>
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
						{/* The org-wide inventory trigger — page level, not the dock, whose every
						    verb is agent-scoped. */}
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

			{/* The org-wide credential inventory — a page-level surface, since it is
			    not agent-scoped. */}
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
