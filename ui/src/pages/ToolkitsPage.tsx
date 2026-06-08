import React, { useState } from 'react';
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
import {
	ToolkitCard,
	ToolkitsListSkeleton,
	type ToolkitCardData,
} from '@/components/toolkits/ToolkitCard';
import { ToolkitsEmptyState } from '@/components/toolkits/ToolkitsEmptyState';
import { ToolkitDetailSheet } from '@/components/toolkits/ToolkitDetailSheet';
import { useToolkitDetailSheet } from '@/hooks/useToolkitDetailSheet';
import { useToolkitCardEnrichment } from '@/hooks/useToolkitCardEnrichment';

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
		<PageShell>
			<PageHeader
				title="Toolkits"
				actions={
					<Button onClick={() => setShowCreate(true)}>
						<Plus className="h-4 w-4" /> Create Toolkit
					</Button>
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
	);
}
