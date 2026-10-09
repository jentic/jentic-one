/**
 * Agents page — operator surface for the agent lifecycle.
 *
 * The header carries what the agent-scoped dock cannot: the org-wide credential
 * inventory (a sheet reached through `?credentials`, `=new` for the wizard, and
 * `?approve=<sid>` for an agent's connect approval link), the
 * fleet filter and `New agent`. It owns the keyboard map documented in `PageHelp`;
 * below it sit the "Waiting for you" connect requests and `FlatAgentsSection`,
 * which keeps its selection in `?agent=`.
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
import { ConnectRequestsSection } from '@/modules/agents/components/flat/ConnectRequestsSection';

/** The connect session the approve wizard is open on. */
interface ApprovalSession {
	sessionId: string;
	/** Only from an older link that still carries one; owners approve without it. */
	pollToken?: string;
}

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
	// Reported by the section below, so the header steps back exactly while the
	// zero-agents landing is on screen.
	const [firstRun, setFirstRun] = useState(false);

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

	// An agent-initiated connect session hands its owner an approval link (see
	// `connect_session_service.py::_approval_url_for`): `?approve=<sid>`. The
	// owner or an org admin approves without a poll token; an older link that
	// still carries `&poll_token=` keeps it for its holder. The params open the
	// inventory's wizard in approve mode and are stripped from the URL at once
	// (history, bookmarks and shared screens never keep them); the session is
	// held in state until the wizard closes. A reload drops the prompt, and the
	// "Waiting for you" section below re-offers it while the request is open.
	const approveParam = searchParams.get('approve');
	const pollTokenParam = searchParams.get('poll_token');
	const [approvalSession, setApprovalSession] = useState<ApprovalSession | undefined>();
	useEffect(() => {
		if (approveParam == null && pollTokenParam == null) return;
		if (approveParam) {
			setApprovalSession(
				pollTokenParam
					? { sessionId: approveParam, pollToken: pollTokenParam }
					: { sessionId: approveParam },
			);
			setInventoryOpen(true);
		}
		// `replace`, and only these keys: `?agent=` must survive the rewrite.
		setSearchParams(
			(prev) => {
				const next = new URLSearchParams(prev);
				next.delete('approve');
				next.delete('poll_token');
				return next;
			},
			{ replace: true },
		);
	}, [approveParam, pollTokenParam, setSearchParams]);

	return (
		// The surface mounts the fixed AgentDock, so the page pads its bottom to keep
		// the last row of tiles clear of it — and, below `md`, of the bottom nav.
		<PageShell className={FOOTER_ACTION_BAR_PAGE_PADDING}>
			<PageHeader
				title="Agents"
				subtitle="Approve, deny, and govern agents across their lifecycle."
				actions={
					<>
						<div className="relative min-w-0 flex-1 sm:flex-none">
							<SearchInput
								ref={filterRef}
								size="sm"
								value={agentFilter}
								onValueChange={setAgentFilter}
								icon={<Filter className="h-3.5 w-3.5" />}
								placeholder="Filter agents…"
								aria-label="Filter agents"
								// Fills the row's spare width on a phone; fixed from `sm`.
								className="w-full sm:w-40 lg:w-48"
							/>
							{!agentFilter && (
								<Kbd className="pointer-events-none absolute top-1/2 right-2 hidden -translate-y-1/2 sm:inline-flex">
									/
								</Kbd>
							)}
						</div>
						{/* Opens the New agent panel. Secondary while the landing is up: its
						    register card is the recommended way in, and the panel opens on
						    "Create here" there. */}
						<Button
							size="sm"
							variant={firstRun ? 'outline' : 'primary'}
							data-emphasis={firstRun ? 'secondary' : 'primary'}
							onClick={() => setAgentCreateOpen(true)}
						>
							<Plus className="h-4 w-4" />
							New agent
						</Button>
						{/* The org-wide inventory trigger — page level, not the dock, whose every
						    verb is agent-scoped. */}
						<Button
							variant="secondary"
							size="sm"
							onClick={() => setInventoryOpen(true)}
						>
							<Wallet className="h-4 w-4" />
							{/* Icon-only on a phone, so the header's verbs fit one row at
							    390px; the name stays for assistive tech. */}
							<span className="sr-only sm:not-sr-only">Credentials</span>
						</Button>
						<PageHelp
							title="About Agents"
							// The inventory sheet binds its own help while it's open.
							bindShortcut={!inventoryOpen}
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
								{
									heading: 'Looking for service accounts?',
									body: (
										<p>
											Service accounts have been retired. Active and disabled
											ones were migrated to agents that keep their
											permissions, credential bindings, and API key, so they
											appear in this list. Create an agent for any new
											non-human caller.
										</p>
									),
								},
							]}
							shortcuts={SHORTCUTS}
						/>
					</>
				}
			/>

			<ConnectRequestsSection />

			<FlatAgentsSection
				createOpen={agentCreateOpen}
				setCreateOpen={setAgentCreateOpen}
				filter={agentFilter}
				onLandingChange={setFirstRun}
			/>

			{/* The org-wide credential inventory — a page-level surface, since it is
			    not agent-scoped. */}
			<CredentialInventorySheet
				open={inventoryOpen}
				autoOpenCreate={inventoryWantsCreate}
				approvalSession={approvalSession}
				onApprovalClose={() => setApprovalSession(undefined)}
				onClose={() => {
					setInventoryOpen(false);
					setInventoryWantsCreate(false);
				}}
			/>
		</PageShell>
	);
}
