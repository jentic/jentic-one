import { useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import { useNavigate } from 'react-router';
import { Boxes, Plus } from 'lucide-react';
import { Button, EmptyState, ErrorAlert, PageHeader, PageHelp, PageShell } from '@/shared/ui';
import { useToolkits } from '@/modules/toolkits/api';
import { CreateToolkitDialog } from '@/modules/toolkits/components/CreateToolkitDialog';
import { ToolkitCard, ToolkitsListSkeleton } from '@/modules/toolkits/components/ToolkitCard';
import {
	ToolkitsToolbar,
	type ToolkitStatusFilter,
} from '@/modules/toolkits/components/ToolkitsToolbar';
import { ROUTE_PATHS } from '@/shared/app/routes';

/**
 * `/app/toolkits` — the toolkit list. Lists first-party toolkits (cursor
 * paginated), each card deep-linking to `/app/toolkits/:id`. The "New toolkit"
 * dialog (`CreateToolkitDialog`) creates a toolkit — with optional inline
 * credential binds — and reveals the one-time plaintext key before handing
 * off to the detail page.
 */
export function ToolkitsPage() {
	const { data, isLoading, isError, error, refetch, isFetching } = useToolkits();
	const navigate = useNavigate();

	const [search, setSearch] = useState('');
	const [statusFilter, setStatusFilter] = useState<ToolkitStatusFilter>('all');
	const [createOpen, setCreateOpen] = useState(false);

	const toolkits = useMemo(() => data?.data ?? [], [data]);

	const filtered = useMemo(() => {
		const q = search.trim().toLowerCase();
		return toolkits.filter((t) => {
			if (statusFilter === 'active' && !t.active) return false;
			if (statusFilter === 'suspended' && t.active) return false;
			if (!q) return true;
			return (
				t.name.toLowerCase().includes(q) ||
				(t.description?.toLowerCase().includes(q) ?? false)
			);
		});
	}, [toolkits, search, statusFilter]);

	return (
		<PageShell spacing="space-y-0">
			<PageHeader
				title="Toolkits"
				subtitle="Group credentials and API keys into a scoped surface for your agents."
				actions={
					<div className="flex items-center gap-2">
						<Button onClick={() => setCreateOpen(true)}>
							<Plus className="h-4 w-4" /> New toolkit
						</Button>
						<PageHelp
							title="About Toolkits"
							intro="A toolkit bundles credentials and API keys behind a single scoped surface. Agents call the toolkit; the broker enforces its permission rules and kill switch on every request."
							sections={[
								{
									heading: 'API keys',
									body: 'Create static keys so an agent can call the toolkit without an interactive session. The plaintext key is shown once on creation and never again.',
								},
								{
									heading: 'Credential bindings',
									body: 'Bind credentials to a toolkit and attach per-credential permission rules (allow/deny by method and path). System safety rules are always appended by the broker.',
								},
								{
									heading: 'Kill switch',
									body: 'Suspending a toolkit blocks every call — both toolkit API keys and agent-identity callers — with 403 toolkit_suspended until restored.',
								},
							]}
						/>
					</div>
				}
			/>

			<ToolkitsToolbar
				query={search}
				onQueryChange={setSearch}
				filter={statusFilter}
				onFilterChange={setStatusFilter}
				onRefresh={() => void refetch()}
				refreshing={isFetching}
			/>

			<div className="mt-4">
				{isError ? (
					<ErrorAlert
						message={
							error instanceof Error ? error.message : 'Failed to load toolkits.'
						}
					/>
				) : isLoading ? (
					<ToolkitsListSkeleton />
				) : toolkits.length === 0 ? (
					<EmptyState
						icon={<Boxes className="h-10 w-10" />}
						title="No toolkits yet"
						description="Create a toolkit to group credentials and API keys into a scoped surface for your agents."
						action={
							<Button onClick={() => setCreateOpen(true)}>
								<Plus className="h-4 w-4" /> New toolkit
							</Button>
						}
					/>
				) : filtered.length === 0 ? (
					<EmptyState
						icon={<Boxes className="h-10 w-10" />}
						title="No matching toolkits"
						description="No toolkits match your current filter. Try a different search term or status."
					/>
				) : (
					<motion.div
						className="grid grid-cols-1 gap-4 md:grid-cols-2"
						initial="hidden"
						animate="visible"
						variants={{ visible: { transition: { staggerChildren: 0.04 } } }}
					>
						{filtered.map((toolkit) => (
							<ToolkitCard key={toolkit.toolkit_id} toolkit={toolkit} />
						))}
					</motion.div>
				)}
			</div>

			<CreateToolkitDialog
				open={createOpen}
				onClose={() => setCreateOpen(false)}
				onGoToToolkit={(toolkitId) => navigate(ROUTE_PATHS.toolkit(toolkitId))}
			/>
		</PageShell>
	);
}
