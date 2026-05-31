import { useEffect, useId, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Trash2, Workflow, KeyRound, Boxes, Layers, CircleDot } from 'lucide-react';
import { Dialog } from './Dialog';
import { Button } from './Button';
import { Checkbox } from './Checkbox';
import { api } from '@/api/client';

export type DeleteTarget =
	| { kind: 'api'; id: string; name: string }
	| { kind: 'workflow'; slug: string; name: string };

interface ConfirmDeleteDialogProps {
	target: DeleteTarget | null;
	open: boolean;
	onClose: () => void;
	onConfirm: (opts: { cascade: boolean }) => void;
	loading?: boolean;
}

export function ConfirmDeleteDialog({
	target,
	open,
	onClose,
	onConfirm,
	loading,
}: ConfirmDeleteDialogProps) {
	const isApi = target?.kind === 'api';
	const [cascade, setCascade] = useState(false);
	// Stable id for the dialog body's lead paragraph, so screen readers
	// announce the impact summary alongside the title via aria-describedby.
	const descriptionId = `delete-dialog-desc-${useId()}`;

	useEffect(() => {
		if (open) setCascade(false);
	}, [open]);

	return (
		<Dialog
			open={open}
			onClose={onClose}
			title={isApi ? 'Remove API' : 'Delete workflow'}
			size="md"
			describedById={descriptionId}
			footer={
				<div className="flex w-full items-center gap-3">
					<Button
						variant="ghost"
						size="sm"
						onClick={onClose}
						disabled={loading}
						className="flex-1"
					>
						Cancel
					</Button>
					<Button
						variant="danger"
						size="sm"
						onClick={() => onConfirm({ cascade })}
						disabled={loading}
						className="flex-1"
					>
						<Trash2 className="h-3.5 w-3.5" />
						{loading ? 'Removing…' : isApi ? 'Remove API' : 'Delete workflow'}
					</Button>
				</div>
			}
		>
			{target?.kind === 'api' ? (
				<ApiCascadeInfo
					apiId={target.id}
					name={target.name}
					cascade={cascade}
					onCascadeChange={setCascade}
					open={open}
					descriptionId={descriptionId}
				/>
			) : target?.kind === 'workflow' ? (
				<WorkflowCascadeInfo
					slug={target.slug}
					name={target.name}
					open={open}
					descriptionId={descriptionId}
				/>
			) : null}
		</Dialog>
	);
}

function ApiCascadeInfo({
	apiId,
	name,
	cascade,
	onCascadeChange,
	open,
	descriptionId,
}: {
	apiId: string;
	name: string;
	cascade: boolean;
	onCascadeChange: (v: boolean) => void;
	open: boolean;
	descriptionId: string;
}) {
	const { data: allRelated, isLoading: loadingWorkflows } = useQuery({
		queryKey: ['delete-cascade', 'api-workflows', apiId],
		queryFn: () => api.listWorkflows(undefined, 'local'),
		enabled: open,
		select: (d: any) => {
			if (!Array.isArray(d)) return { willDelete: [], willAffect: [] };
			const willDelete: any[] = [];
			const willAffect: any[] = [];
			for (const w of d) {
				const involved = Array.isArray(w.involved_apis) ? w.involved_apis : [];
				if (!involved.includes(apiId)) continue;
				if (involved.length === 1) {
					willDelete.push(w);
				} else {
					willAffect.push(w);
				}
			}
			return { willDelete, willAffect };
		},
	});

	const { data: credentials, isLoading: loadingCreds } = useQuery({
		queryKey: ['delete-cascade', 'api-credentials', apiId],
		queryFn: () => api.listCredentials(apiId),
		enabled: open,
		select: (d: any) => (Array.isArray(d) ? d : []),
	});

	const { data: toolkits, isLoading: loadingToolkits } = useQuery({
		queryKey: ['delete-cascade', 'api-toolkits', apiId],
		queryFn: async () => {
			const allToolkits = (await api.listToolkits()) as any[];
			if (!Array.isArray(allToolkits)) return [];
			const candidates = allToolkits.filter((t: any) => t.credential_count > 0);
			const details = await Promise.all(
				candidates.map((t: any) => api.getToolkit(t.id).catch(() => null)),
			);
			return details
				.filter(
					(d: any) => d && Array.isArray(d.bound_apis) && d.bound_apis.includes(apiId),
				)
				.map((d: any) => ({
					id: d.id,
					name: d.name || (d.id === 'default' ? 'Default' : d.id),
					credentials: (d.credentials ?? [])
						.filter((c: any) => c.api_id === apiId)
						.map((c: any) => c.label || c.credential_id),
				}));
		},
		enabled: open,
	});

	const isLoading = loadingWorkflows || loadingCreds || loadingToolkits;

	const willDelete = allRelated?.willDelete ?? [];
	const willAffect = allRelated?.willAffect ?? [];
	const affectedCredentials = credentials ?? [];
	const affectedToolkits = toolkits ?? [];
	const hasCredentials = affectedCredentials.length > 0;
	const hasImpact =
		willDelete.length > 0 ||
		willAffect.length > 0 ||
		hasCredentials ||
		affectedToolkits.length > 0;

	return (
		<div className="space-y-5">
			<p id={descriptionId} className="text-foreground/80 text-[13px] leading-relaxed">
				<strong className="text-foreground font-medium">{name}</strong> and all its
				operations will be permanently removed from your workspace.
			</p>

			{isLoading && (
				<p className="text-muted-foreground animate-pulse text-xs">
					Checking affected resources…
				</p>
			)}
			{hasImpact && (
				<div className="max-h-[60vh] space-y-3 overflow-y-auto pr-1">
					{willDelete.length > 0 && (
						<ImpactGroup
							color="red"
							icon={<Workflow size={14} />}
							title="Workflows deleted"
							count={willDelete.length}
							items={willDelete.map((w: any) => w.name || w.slug)}
						/>
					)}

					{willAffect.length > 0 && (
						<ImpactGroup
							color="amber"
							icon={<Workflow size={14} />}
							title="Workflows affected"
							count={willAffect.length}
							subtitle="Will lose this API reference but remain"
							items={willAffect.map((w: any) => w.name || w.slug)}
						/>
					)}

					{hasCredentials && (
						<ImpactGroup
							color={cascade ? 'red' : 'amber'}
							icon={<KeyRound size={14} />}
							title={cascade ? 'Credentials deleted' : 'Credentials orphaned'}
							count={affectedCredentials.length}
							subtitle={
								cascade
									? 'Will be permanently deleted along with toolkit bindings'
									: "Won't function until this API is re-imported"
							}
							items={affectedCredentials.map(
								(c: any) => c.label || c.identity || c.id,
							)}
						/>
					)}

					{affectedToolkits.length > 0 && (
						<div
							className={`${cascade ? 'bg-red-500/5' : 'bg-amber-500/5'} rounded-lg px-3.5 py-3`}
						>
							<div className="flex items-center gap-2">
								<CircleDot
									size={10}
									className={`${cascade ? 'text-red-500' : 'text-amber-500'} shrink-0`}
								/>
								<span className="text-foreground text-xs font-medium">
									{cascade
										? 'Toolkit bindings removed'
										: 'Toolkits lose API access'}
								</span>
								<span className="text-muted-foreground ml-auto font-mono text-[11px]">
									{affectedToolkits.length}
								</span>
							</div>
							<p className="text-muted-foreground mt-0.5 pl-[18px] text-[11px]">
								{cascade
									? 'Credential bindings for this API will be deleted from toolkits'
									: "Toolkit credentials for this API won't work until re-imported"}
							</p>
							<div className="mt-2 space-y-2 pl-[18px]">
								{affectedToolkits.map((t: any) => (
									<div key={t.id}>
										<div className="flex items-center gap-2">
											<span className="text-muted-foreground/60">
												<Layers size={14} />
											</span>
											<span className="text-foreground/70 text-xs font-medium">
												{t.name}
											</span>
										</div>
										{t.credentials.length > 0 && (
											<div className="mt-0.5 space-y-0.5 pl-[22px]">
												{t.credentials.map((cred: string) => (
													<div
														key={cred}
														className="text-muted-foreground flex items-center gap-1.5 text-[11px]"
													>
														<KeyRound
															size={10}
															className="shrink-0 opacity-50"
														/>
														<span className="truncate">{cred}</span>
													</div>
												))}
											</div>
										)}
									</div>
								))}
							</div>
						</div>
					)}
				</div>
			)}

			{!hasImpact && (
				<p className="text-muted-foreground text-xs">
					No other resources will be affected.
				</p>
			)}

			{hasCredentials && (
				<div className="border-border/60 bg-background rounded-lg border px-3.5 py-3">
					<Checkbox checked={cascade} onChange={onCascadeChange} size="sm">
						<span>
							<span className="text-foreground text-xs font-medium">
								Also delete credentials and toolkit bindings
							</span>
							<p className="text-muted-foreground mt-0.5 text-[11px]">
								Clean slate — choose this if you don't plan to re-import.
							</p>
						</span>
					</Checkbox>
				</div>
			)}
		</div>
	);
}

function WorkflowCascadeInfo({
	slug,
	name,
	open,
	descriptionId,
}: {
	slug: string;
	name: string;
	open: boolean;
	descriptionId: string;
}) {
	const { data: workflow } = useQuery({
		queryKey: ['delete-cascade', 'workflow-detail', slug],
		queryFn: () => api.getWorkflow(slug),
		enabled: open,
	});

	const involvedApis: string[] = Array.isArray(workflow?.involved_apis)
		? workflow.involved_apis
		: [];

	return (
		<div className="space-y-5">
			<p id={descriptionId} className="text-foreground/80 text-[13px] leading-relaxed">
				<strong className="text-foreground font-medium">{name}</strong> will be permanently
				deleted from your workspace.
			</p>

			{involvedApis.length > 0 && (
				<ImpactGroup
					color="emerald"
					icon={<Boxes size={14} />}
					title="APIs unaffected"
					count={involvedApis.length}
					subtitle="Will remain in your workspace"
					items={involvedApis}
				/>
			)}

			<p className="text-muted-foreground text-xs">
				You can re-import from Discover or an external URL anytime.
			</p>
		</div>
	);
}

function ImpactGroup({
	color,
	icon,
	title,
	count,
	subtitle,
	items,
}: {
	color: 'red' | 'amber' | 'emerald';
	icon: React.ReactNode;
	title: string;
	count: number;
	subtitle?: string;
	items: string[];
}) {
	const [expanded, setExpanded] = useState(items.length <= 5);

	const dotColor =
		color === 'red'
			? 'text-red-500'
			: color === 'amber'
				? 'text-amber-500'
				: 'text-emerald-500';

	const bgColor =
		color === 'red'
			? 'bg-red-500/5'
			: color === 'amber'
				? 'bg-amber-500/5'
				: 'bg-emerald-500/5';

	return (
		<div className={`${bgColor} rounded-lg px-3.5 py-3`}>
			<div className="flex items-center gap-2">
				<CircleDot size={10} className={`${dotColor} shrink-0`} />
				<span className="text-foreground text-xs font-medium">{title}</span>
				<span className="text-muted-foreground ml-auto font-mono text-[11px]">{count}</span>
			</div>
			{subtitle && (
				<p className="text-muted-foreground mt-0.5 pl-[18px] text-[11px]">{subtitle}</p>
			)}
			{items.length > 0 && (
				<div className="mt-2 pl-[18px]">
					<div className="space-y-1">
						{(expanded ? items : items.slice(0, 4)).map((item, i) => (
							<div key={i} className="flex items-center gap-2">
								<span className="text-muted-foreground/60">{icon}</span>
								<span className="text-foreground/70 min-w-0 truncate text-xs">
									{item}
								</span>
							</div>
						))}
					</div>
					{!expanded && items.length > 4 && (
						<Button
							variant="ghost"
							size="sm"
							onClick={() => setExpanded(true)}
							className="text-primary mt-1.5 h-auto p-0 text-[11px] font-medium"
						>
							Show all {items.length}
						</Button>
					)}
				</div>
			)}
		</div>
	);
}
