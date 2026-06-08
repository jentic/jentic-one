import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { motion } from 'framer-motion';
import { Plus } from 'lucide-react';
import { api } from '@/api/client';
import { usePendingRequests } from '@/hooks/usePendingRequests';
import type { ToolkitCreate } from '@/api/types';
import { Dialog } from '@/components/ui/Dialog';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Textarea } from '@/components/ui/Textarea';
import { Label } from '@/components/ui/Label';
import { ErrorAlert } from '@/components/ui/ErrorAlert';
import { PageShell } from '@/components/layout/PageShell';
import { PageHeader } from '@/components/ui/PageHeader';
import { PageHelp } from '@/components/ui/PageHelp';
import { KeyboardShortcutsBar, MOD_KEY } from '@/components/ui/KeyboardShortcutsBar';
import {
	ToolkitCard,
	ToolkitsListSkeleton,
	type ToolkitCardData,
} from '@/components/toolkits/ToolkitCard';
import { ToolkitsEmptyState } from '@/components/toolkits/ToolkitsEmptyState';
import { ToolkitDetailSheet } from '@/components/toolkits/ToolkitDetailSheet';
import { useToolkitDetailSheet } from '@/hooks/useToolkitDetailSheet';
import { useToolkitCardEnrichment } from '@/hooks/useToolkitCardEnrichment';
import { isTypingTarget } from '@/lib/keyboard';

const gridVariants = {
	hidden: { opacity: 1 },
	visible: { opacity: 1, transition: { staggerChildren: 0.04 } },
};

function CreateModal({
	open,
	onClose,
	onCreated,
}: {
	open: boolean;
	onClose: () => void;
	onCreated: (id: string) => void;
}) {
	const queryClient = useQueryClient();
	const [name, setName] = useState('');
	const [description, setDescription] = useState('');
	const [simulate, setSimulate] = useState(false);
	const [error, setError] = useState<string | null>(null);

	const mutation = useMutation({
		mutationFn: (data: ToolkitCreate) => api.createToolkit(data),
		onSuccess: (t) => {
			queryClient.invalidateQueries({ queryKey: ['toolkits'] });
			onCreated(t.id);
		},
		onError: (e: Error) => setError(e.message),
	});

	return (
		<Dialog
			open={open}
			onClose={onClose}
			title="Create Toolkit"
			size="sm"
			footer={
				<>
					<Button variant="secondary" onClick={onClose}>
						Cancel
					</Button>
					<Button type="submit" form="create-toolkit-form" loading={mutation.isPending}>
						Create Toolkit
					</Button>
				</>
			}
		>
			<form
				id="create-toolkit-form"
				onSubmit={(e) => {
					e.preventDefault();
					setError(null);
					mutation.mutate({ name, description: description || null, simulate });
				}}
				className="space-y-4"
			>
				<div className="space-y-1.5">
					<Label htmlFor="tk-create-name" required>
						Name
					</Label>
					<Input
						id="tk-create-name"
						type="text"
						value={name}
						onChange={(e) => setName(e.target.value)}
						required
						autoFocus
					/>
				</div>
				<div className="space-y-1.5">
					<Label htmlFor="tk-create-description">Description</Label>
					<Textarea
						id="tk-create-description"
						value={description}
						onChange={(e) => setDescription(e.target.value)}
						rows={2}
						resizable="none"
					/>
				</div>
				<label htmlFor="tk-simulate" className="flex cursor-pointer items-center gap-3">
					{/* eslint-disable-next-line no-restricted-syntax -- No Checkbox primitive yet */}
					<input
						id="tk-simulate"
						type="checkbox"
						checked={simulate}
						onChange={(e) => setSimulate(e.target.checked)}
						className="border-border h-4 w-4 rounded"
					/>
					<div>
						<span className="text-foreground text-sm">Simulate mode</span>
						<p className="text-muted-foreground text-xs">
							Returns mock responses without calling real APIs
						</p>
					</div>
				</label>
				{error && <ErrorAlert message={error} />}
			</form>
		</Dialog>
	);
}

interface ToolkitsPageProps {
	createNew?: boolean;
}

export default function ToolkitsPage({ createNew = false }: ToolkitsPageProps) {
	const navigate = useNavigate();
	const [showCreate, setShowCreate] = useState(createNew);
	const detailSheet = useToolkitDetailSheet();

	// `n` → open the Create Toolkit modal (advertised in PageHelp /
	// KeyboardShortcutsBar). Skip while typing in a field, while a modifier is
	// held, or when the create modal / detail sheet is already open.
	const detailSheetOpen = detailSheet.open;
	useEffect(() => {
		const onKeyDown = (e: KeyboardEvent) => {
			if (e.key !== 'n' || e.metaKey || e.ctrlKey || e.altKey) return;
			if (isTypingTarget(e.target)) return;
			if (showCreate || detailSheetOpen) return;
			e.preventDefault();
			setShowCreate(true);
		};
		document.addEventListener('keydown', onKeyDown);
		return () => document.removeEventListener('keydown', onKeyDown);
	}, [showCreate, detailSheetOpen]);

	const {
		data: toolkitsRaw,
		isLoading,
		isError,
	} = useQuery({
		queryKey: ['toolkits'],
		queryFn: api.listToolkits,
		refetchInterval: 30000,
	});
	const toolkits = (Array.isArray(toolkitsRaw) ? toolkitsRaw : []) as ToolkitCardData[];

	const enrichment = useToolkitCardEnrichment(toolkits.map((t) => t.id));

	const { data: pendingRequests } = usePendingRequests();
	const pendingByToolkit = (pendingRequests ?? []).reduce<Record<string, number>>(
		(acc, req: any) => {
			if (req.toolkit_id) acc[req.toolkit_id] = (acc[req.toolkit_id] ?? 0) + 1;
			return acc;
		},
		{},
	);

	return (
		<>
			<PageShell spacing="space-y-5" className="md:pb-12">
				<PageHeader
					title="Toolkits"
					subtitle="Scoped bundles of credentials and policy your agents act through."
					actions={
						<>
							<Button onClick={() => setShowCreate(true)}>
								<Plus className="h-4 w-4" /> Create Toolkit
							</Button>
							<PageHelp
								title="About Toolkits"
								intro={
									<p>
										A toolkit is the unit of access you hand to an agent: a
										client API key, a set of bound credentials, and the policy
										that governs what calls are allowed. Agents call through the
										broker using the toolkit's key; the broker injects the right
										upstream credential per request and enforces the toolkit's
										rules.
									</p>
								}
								sections={[
									{
										heading: 'Reading a card',
										body: (
											<p>
												Each card shows the APIs the toolkit touches (from
												its bound credentials) and how many agents are
												granted it, so you can trace toolkits → agents at a
												glance. The <strong>default</strong> toolkit
												implicitly contains every credential, so it shows no
												API pile. Open a card to inspect keys, bound
												credentials, agents, and the kill switch.
											</p>
										),
									},
									{
										heading: 'Multiple credentials',
										body: (
											<p>
												A toolkit can hold several credentials at once — the
												broker disambiguates per request by API
												(longest-prefix match), by service name, or by an
												explicit <strong>X-Jentic-Credential</strong> alias.
												Binding the same credential twice is a no-op.
											</p>
										),
									},
									{
										heading: 'Suspending access',
										body: (
											<p>
												The kill switch on a toolkit's detail view suspends
												it entirely — both API key calls and agent
												executions are blocked with a clear error until you
												restore it.
											</p>
										),
									},
								]}
								shortcuts={[
									{ keys: ['n'], label: 'Create toolkit' },
									{ keys: [MOD_KEY, '/'], chord: true, label: 'Show this help' },
								]}
							/>
						</>
					}
				/>

				{isLoading ? (
					<ToolkitsListSkeleton />
				) : isError ? (
					<ErrorAlert message="Failed to load toolkits. Please try refreshing the page." />
				) : !toolkits || toolkits.length === 0 ? (
					<ToolkitsEmptyState onCreate={() => setShowCreate(true)} />
				) : (
					<motion.div
						variants={gridVariants}
						initial="hidden"
						animate="visible"
						className="grid grid-cols-1 gap-4 md:grid-cols-2"
					>
						{toolkits.map((toolkit) => {
							const enriched = enrichment.get(toolkit.id);
							return (
								<ToolkitCard
									key={toolkit.id}
									toolkit={{
										...toolkit,
										apiIds: enriched?.apiIds,
										agentCount: enriched?.agentCount,
									}}
									pendingCount={pendingByToolkit[toolkit.id] ?? 0}
									onOpen={detailSheet.openSheet}
								/>
							);
						})}
					</motion.div>
				)}

				<ToolkitDetailSheet
					toolkitId={detailSheet.stickyId}
					open={detailSheet.open}
					onClose={detailSheet.closeSheet}
					onAfterClose={detailSheet.clearSticky}
				/>

				<CreateModal
					open={showCreate}
					onClose={() => {
						setShowCreate(false);
						if (createNew) navigate('/toolkits');
					}}
					onCreated={(id) => {
						setShowCreate(false);
						if (createNew) navigate('/toolkits');
						detailSheet.openSheet(id);
					}}
				/>
			</PageShell>

			<KeyboardShortcutsBar
				shortcuts={[
					{ keys: ['n'], label: 'create' },
					{ keys: [MOD_KEY, '/'], chord: true, label: 'help' },
				]}
			/>
		</>
	);
}
