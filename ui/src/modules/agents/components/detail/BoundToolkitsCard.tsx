/**
 * BoundToolkitsCard — the "Bound toolkits" card on the agent detail Overview
 * tab: real bindings (GET /agents/{id}/toolkits) with agent-side bind/unbind
 * (#607) mirroring the toolkit page's "Link agent". Self-contained: owns the
 * picker dialog and the inline unbind confirm.
 */
import { useMemo, useState } from 'react';
import { ArrowRight, KeyRound, Link as LinkIcon, Shield, Unlink } from 'lucide-react';
import {
	AppLink,
	Button,
	Card,
	CardBody,
	CardHeader,
	CardTitle,
	Dialog,
	ErrorAlert,
	LoadingState,
} from '@/shared/ui';
import { formatTimestamp, timeAgo } from '@/shared/lib/utils';
import {
	useAgentToolkits,
	useToolkitName,
	useBindToolkitToAgent,
	useUnbindToolkitFromAgent,
	type ToolkitBindingEntity,
} from '@/modules/agents/api';
import { ToolkitPicker } from '@/modules/agents/components/ToolkitPicker';
import { ROUTES, ROUTE_PATHS } from '@/shared/app/routes';

/**
 * One row in the "Bound toolkits" card. Resolves the toolkit's human name via a
 * per-id read (`useToolkitName`) so the card never pays the whole-catalogue
 * sweep — the hook is called at the top of THIS component (not in the parent's
 * map callback) to keep the hooks-per-row contract valid. Until the name
 * resolves (or if the toolkit is gone), it falls back to the id as the primary
 * label and hides the redundant secondary id line (#4).
 */
function BoundToolkitRow({
	toolkit,
	confirming,
	unbindPending,
	onStartUnbind,
	onCancelUnbind,
	onConfirmUnbind,
}: {
	toolkit: ToolkitBindingEntity;
	confirming: boolean;
	unbindPending: boolean;
	onStartUnbind: () => void;
	onCancelUnbind: () => void;
	onConfirmUnbind: () => void;
}) {
	const nameQuery = useToolkitName(toolkit.toolkitId);
	// Primary label: the resolved name if we have one, else the id (both while
	// the per-id read is in flight and for a since-deleted toolkit).
	const name = nameQuery.data?.trim() || toolkit.toolkitId;
	// Only show the mono id sub-line once a DISTINCT name resolved — otherwise
	// the id would appear twice (bold label + mono line) (#4).
	const showIdLine = name !== toolkit.toolkitId;
	return (
		<div
			data-testid="bound-toolkit-row"
			className="group border-border/60 bg-background/40 flex items-center gap-2 rounded-lg border px-3 py-2"
		>
			<KeyRound className="text-muted-foreground h-3.5 w-3.5 shrink-0" />
			<AppLink
				href={ROUTE_PATHS.toolkit(toolkit.toolkitId)}
				className="hover:text-primary flex min-w-0 flex-1 items-center gap-2 transition-colors"
			>
				<span className="flex min-w-0 flex-1 flex-col">
					<span className="text-foreground truncate text-sm font-medium" title={name}>
						{name}
					</span>
					{showIdLine && (
						<code
							className="text-muted-foreground/80 truncate font-mono text-[11px]"
							title={toolkit.toolkitId}
						>
							{toolkit.toolkitId}
						</code>
					)}
				</span>
				<span
					className="text-muted-foreground/70 shrink-0 text-[11px]"
					title={formatTimestamp(toolkit.boundAt)}
				>
					{timeAgo(toolkit.boundAt)}
				</span>
				<ArrowRight className="text-muted-foreground/40 group-hover:text-primary h-3.5 w-3.5 shrink-0 transition-colors" />
			</AppLink>
			{confirming ? (
				<span
					role="group"
					aria-label={`Unbind ${name} for the agent?`}
					className="border-danger/30 bg-danger/5 ml-1 inline-flex shrink-0 items-center gap-2 rounded-md border px-2 py-1"
				>
					<span className="text-muted-foreground text-xs">Unbind this toolkit?</span>
					<Button
						variant="danger"
						size="sm"
						className="px-2 py-0.5 text-xs"
						disabled={unbindPending}
						onClick={onConfirmUnbind}
						aria-label={`Unbind toolkit ${name}`}
					>
						Unbind
					</Button>
					<Button
						variant="ghost"
						size="sm"
						className="px-2 py-0.5 text-xs"
						disabled={unbindPending}
						onClick={onCancelUnbind}
					>
						Cancel
					</Button>
				</span>
			) : (
				<Button
					variant="danger"
					size="sm"
					className="ml-1 inline-flex shrink-0 items-center gap-1 px-2 py-1 text-xs"
					onClick={onStartUnbind}
					aria-label={`Unbind toolkit ${name}`}
				>
					<Unlink className="h-3 w-3" /> Unbind
				</Button>
			)}
		</div>
	);
}

export function BoundToolkitsCard({ agentId }: { agentId: string }) {
	const toolkits = useAgentToolkits(agentId);
	const bindToolkit = useBindToolkitToAgent(agentId);
	const unbindToolkit = useUnbindToolkitFromAgent(agentId);

	const [bindToolkitOpen, setBindToolkitOpen] = useState(false);
	const [unlinkToolkitId, setUnlinkToolkitId] = useState<string | null>(null);

	// Memoised so ToolkitPicker's internal useMemos (available list, candidate
	// count) don't invalidate on every parent re-render — a fresh ``new Set`` on
	// each render would force a filter over the whole toolkit list every time.
	const boundToolkitIds = useMemo(
		() => new Set((toolkits.data ?? []).map((t) => t.toolkitId)),
		[toolkits.data],
	);

	return (
		<>
			<Card>
				<CardHeader className="flex flex-row items-center justify-between gap-2">
					<div className="flex items-center gap-2">
						<Shield className="text-primary h-4 w-4" />
						<CardTitle>Bound toolkits</CardTitle>
					</div>
					<Button variant="secondary" size="sm" onClick={() => setBindToolkitOpen(true)}>
						<LinkIcon className="h-4 w-4" /> Bind toolkit
					</Button>
				</CardHeader>
				<CardBody className="space-y-2">
					{toolkits.isPending ? (
						<LoadingState size="sm" />
					) : toolkits.error ? (
						<ErrorAlert message={toolkits.error as Error} />
					) : !toolkits.data || toolkits.data.length === 0 ? (
						<div className="text-muted-foreground border-border/60 rounded-lg border border-dashed p-4 text-center text-sm">
							No toolkits bound to this agent. Bind one to let this agent call its
							APIs.
						</div>
					) : (
						toolkits.data.map((t) => (
							<BoundToolkitRow
								key={t.id}
								toolkit={t}
								confirming={unlinkToolkitId === t.toolkitId}
								unbindPending={unbindToolkit.isPending}
								onStartUnbind={() => setUnlinkToolkitId(t.toolkitId)}
								onCancelUnbind={() => setUnlinkToolkitId(null)}
								onConfirmUnbind={async () => {
									try {
										await unbindToolkit.mutateAsync(t.toolkitId);
										setUnlinkToolkitId(null);
									} catch {
										// onError toasts; keep the row in the confirming
										// state so the user can retry.
									}
								}}
							/>
						))
					)}
				</CardBody>
			</Card>

			{/* Bind toolkit — agent-side picker mirroring the toolkit page's
			 *  "Link agent" (#607). */}
			<Dialog
				open={bindToolkitOpen}
				onClose={() => {
					// Don't let a mid-flight bind be Esc/backdrop-dismissed — if it then
					// fails the error toast fires but the dialog is gone, leaving the
					// user unable to retry in place.
					if (bindToolkit.isPending) return;
					setBindToolkitOpen(false);
				}}
				title="Bind toolkit"
				size="sm"
				footer={
					<Button
						variant="secondary"
						onClick={() => setBindToolkitOpen(false)}
						disabled={bindToolkit.isPending}
					>
						Cancel
					</Button>
				}
			>
				<div className="space-y-3">
					<p className="text-muted-foreground text-sm">
						Pick a toolkit to bind to this agent. The agent's identity will be able to
						call the toolkit's bound APIs. Manage toolkits on the{' '}
						<AppLink href={ROUTES.toolkits} className="text-primary font-medium">
							Toolkits
						</AppLink>{' '}
						page.
					</p>
					<ToolkitPicker
						boundIds={boundToolkitIds}
						onSelect={async (toolkitId) => {
							try {
								await bindToolkit.mutateAsync(toolkitId);
								setBindToolkitOpen(false);
							} catch {
								// onError toasts; keep the dialog open so the user can retry
								// (matches the unlink flow and every other confirm dialog on
								// this page).
							}
						}}
						pending={bindToolkit.isPending}
						enabled={bindToolkitOpen}
					/>
				</div>
			</Dialog>
		</>
	);
}
