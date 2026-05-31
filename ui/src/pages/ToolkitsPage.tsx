import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, Wrench, AlertTriangle, Key, Ban } from 'lucide-react';
import { AppLink } from '@/components/ui/AppLink';
import { api } from '@/api/client';
import { usePendingRequests } from '@/hooks/usePendingRequests';
import type { ToolkitCreate } from '@/api/types';
import { Dialog } from '@/components/ui/Dialog';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Textarea } from '@/components/ui/Textarea';
import { Label } from '@/components/ui/Label';
import { ErrorAlert } from '@/components/ui/ErrorAlert';
import { LoadingState } from '@/components/ui/LoadingState';
import { PageShell } from '@/components/layout/PageShell';
import { EmptyState } from '@/components/ui/EmptyState';
import { PageHeader } from '@/components/ui/PageHeader';

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

	const {
		data: toolkitsRaw,
		isLoading,
		isError,
	} = useQuery({
		queryKey: ['toolkits'],
		queryFn: api.listToolkits,
		refetchInterval: 30000,
	});
	const toolkits = Array.isArray(toolkitsRaw) ? toolkitsRaw : [];

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
				<LoadingState message="Loading toolkits..." />
			) : isError ? (
				<ErrorAlert message="Failed to load toolkits. Please try refreshing the page." />
			) : !toolkits || toolkits.length === 0 ? (
				<EmptyState
					icon={<Wrench className="h-10 w-10 opacity-30" />}
					title="No toolkits yet"
					description="Create a toolkit to give an agent scoped access to your APIs."
					action={
						<Button onClick={() => setShowCreate(true)}>
							Create your first toolkit
						</Button>
					}
				/>
			) : (
				<div className="grid grid-cols-1 gap-4 md:grid-cols-2">
					{toolkits.map((toolkit) => {
						const pendingCount = pendingByToolkit[toolkit.id] ?? 0;
						return (
							<AppLink
								href={`/toolkits/${toolkit.id}`}
								key={toolkit.id}
								className={`bg-muted hover:border-primary/50 hover:bg-muted/80 block space-y-3 rounded-xl border p-5 transition-all ${toolkit.disabled ? 'border-danger/40 opacity-70' : 'border-border'}`}
							>
								<div className="flex items-start justify-between gap-2">
									<div>
										<div className="flex flex-wrap items-center gap-2">
											<h2 className="font-heading text-foreground font-semibold">
												{toolkit.name}
											</h2>
											{toolkit.disabled && (
												<span className="bg-danger/10 text-danger border-danger/30 inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-xs">
													<Ban className="h-3 w-3" />
													SUSPENDED
												</span>
											)}
											{pendingCount > 0 && (
												<span className="bg-warning/10 text-warning border-warning/20 inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-xs">
													<AlertTriangle className="h-3 w-3" />
													{pendingCount} pending
												</span>
											)}
											{toolkit.simulate && (
												<span className="bg-primary/10 text-primary border-primary/20 rounded-full border px-2 py-0.5 font-mono text-[10px]">
													simulate
												</span>
											)}
										</div>
										{toolkit.description && (
											<p className="text-muted-foreground mt-0.5 text-xs">
												{toolkit.description}
											</p>
										)}
									</div>
									<Wrench className="text-accent-teal mt-0.5 h-4 w-4 shrink-0" />
								</div>
								<div className="text-muted-foreground flex items-center gap-4 text-xs">
									<span className="flex items-center gap-1">
										<Key className="h-3 w-3" />
										{toolkit.key_count ?? '—'} keys
									</span>
									<span>
										{toolkit.credential_count != null
											? `${toolkit.credential_count} credentials`
											: toolkit.credentials?.length != null
												? `${toolkit.credentials.length} credentials`
												: '—'}
									</span>
								</div>
							</AppLink>
						);
					})}
				</div>
			)}

			<CreateModal
				open={showCreate}
				onClose={() => {
					setShowCreate(false);
					if (createNew) navigate('/toolkits');
				}}
				onCreated={(id) => {
					setShowCreate(false);
					navigate(`/toolkits/${id}`);
				}}
			/>
		</PageShell>
	);
}
