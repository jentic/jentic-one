/**
 * AttentionList — the rendered "needs you" list, shared by the top-bar
 * Notifications menu and the Home inbox so the two can never disagree on
 * wording, grouping, or which verbs a row offers.
 *
 * Items are grouped by what they ask of you:
 *
 *   Alerts     — recent failures and warnings that need a look (view)
 *   Approvals  — agents and OAuth clients waiting to be let in, and agents
 *                waiting on an account connection or a held call (approve / review)
 *   Setup      — credential sign-ins nobody finished
 *
 * The cheap, reversible verb (approve an agent) runs inline; anything that
 * needs context links to where it is resolved. Approve
 * shows only to a caller who may approve (`agents:write` or `org:admin`); the
 * row still links to the agent for anyone else.
 */
import { type ComponentType } from 'react';
import { AlertTriangle, Bot, CheckSquare, KeyRound, PlugZap, ShieldQuestion } from 'lucide-react';
import { AppLink } from '@/shared/ui/AppLink';
import { Button } from '@/shared/ui/Button';
import { toast } from '@/shared/ui';
import { ROUTE_PATHS } from '@/shared/app/routes';
import { useApproveAgent } from '@/shared/attention/actions';
import { AGENTS_WRITE, useCanAccess } from '@/shared/auth/useCanAccess';
import type { AttentionItem, AttentionKind } from '@/shared/attention/useAttentionItems';
import { cn, timeAgo } from '@/shared/lib/utils';

const KIND_ICON: Record<AttentionKind, ComponentType<{ className?: string }>> = {
	agent: Bot,
	oauth_client: ShieldQuestion,
	connect_request: PlugZap,
	execution_approval: CheckSquare,
	credential: KeyRound,
	event: AlertTriangle,
};

type Group = 'alerts' | 'approvals' | 'setup';

const GROUP_OF: Record<AttentionKind, Group> = {
	event: 'alerts',
	agent: 'approvals',
	oauth_client: 'approvals',
	connect_request: 'approvals',
	execution_approval: 'approvals',
	credential: 'setup',
};

const GROUP_LABEL: Record<Group, string> = {
	alerts: 'Alerts',
	approvals: 'Approvals',
	setup: 'Setup',
};

const GROUP_ORDER: Group[] = ['alerts', 'approvals', 'setup'];

export type AttentionListProps = {
	items: AttentionItem[];
	/** `menu`: narrow popover rows (verbs under the title). `page`: wide rows. */
	variant?: 'page' | 'menu';
	/** Fired after any link in a row is followed (the menu closes itself). */
	onNavigate?: () => void;
};

/** Group a pre-sorted list by what it asks of you, preserving order within a group. */
function groupItems(items: AttentionItem[]): Array<{ label: string; items: AttentionItem[] }> {
	const groups = new Map<Group, AttentionItem[]>();
	for (const item of items) {
		const group = GROUP_OF[item.kind];
		const list = groups.get(group) ?? [];
		list.push(item);
		groups.set(group, list);
	}
	return GROUP_ORDER.filter((g) => groups.has(g)).map((g) => ({
		label: GROUP_LABEL[g],
		items: groups.get(g)!,
	}));
}

export function AttentionList({ items, variant = 'page', onNavigate }: AttentionListProps) {
	const groups = groupItems(items);
	return (
		<div className="divide-hairline divide-y">
			{groups.map((group) => (
				<section key={group.label} aria-label={group.label}>
					<h3
						className={cn(
							'text-foreground-faint text-[10.5px] font-bold tracking-[0.08em] uppercase',
							variant === 'menu' ? 'px-3 pt-2.5 pb-1' : 'px-5 pt-3 pb-1',
						)}
					>
						{group.label}
						<span className="ml-1.5 font-mono tabular-nums">{group.items.length}</span>
					</h3>
					<ul className="divide-hairline-row divide-y" aria-label={group.label}>
						{group.items.map((item) => (
							<AttentionRow
								key={item.key}
								item={item}
								variant={variant}
								onNavigate={onNavigate}
							/>
						))}
					</ul>
				</section>
			))}
		</div>
	);
}

function AttentionRow({
	item,
	variant,
	onNavigate,
}: {
	item: AttentionItem;
	variant: 'page' | 'menu';
	onNavigate?: () => void;
}) {
	const Icon = KIND_ICON[item.kind];
	const urgent = item.urgency === 3;
	const menu = variant === 'menu';

	const tile = (
		<span
			className={cn(
				'flex shrink-0 items-center justify-center rounded-md',
				menu ? 'h-7 w-7' : 'h-8 w-8',
				urgent
					? 'bg-danger/10 text-danger'
					: item.urgency === 2
						? 'bg-warning/10 text-warning'
						: 'bg-surface-field text-foreground-sub',
			)}
		>
			<Icon className={menu ? 'h-3.5 w-3.5' : 'h-4 w-4'} aria-hidden="true" />
		</span>
	);

	if (menu) {
		return (
			<li className="flex gap-2.5 px-3 py-2.5">
				{tile}
				<div className="min-w-0 flex-1">
					<p className="text-foreground-name line-clamp-2 text-[13px] leading-snug font-semibold">
						{item.title}
					</p>
					<p className="text-muted-foreground mt-0.5 truncate text-xs">
						<span title={item.since}>{timeAgo(item.since)}</span>
						{item.detail && <> · {item.detail}</>}
					</p>
					<div className="mt-2 flex items-center gap-1.5">
						<RowActions item={item} onNavigate={onNavigate} />
					</div>
				</div>
			</li>
		);
	}

	return (
		<li className="flex flex-wrap items-center gap-3 px-5 py-3 sm:flex-nowrap">
			{tile}
			<div className="min-w-0 flex-1 basis-40">
				<p className="text-foreground-name truncate text-sm font-semibold">{item.title}</p>
				{item.detail && (
					<p className="text-muted-foreground truncate text-xs">{item.detail}</p>
				)}
			</div>
			<span
				className="text-muted-foreground hidden shrink-0 font-mono text-xs sm:inline"
				title={item.since}
			>
				{timeAgo(item.since)}
			</span>
			{/* On a phone the verbs wrap under the title, aligned past the icon. */}
			<div className="ml-11 flex shrink-0 items-center gap-2 sm:ml-0">
				<RowActions item={item} onNavigate={onNavigate} />
			</div>
		</li>
	);
}

function RowActions({ item, onNavigate }: { item: AttentionItem; onNavigate?: () => void }) {
	switch (item.kind) {
		case 'agent':
			return item.agent ? (
				<AgentActions
					agentId={item.agent.id}
					name={item.agent.name}
					onNavigate={onNavigate}
				/>
			) : null;
		case 'event':
			return <ReviewLink href={item.href} label="View" onNavigate={onNavigate} />;
		case 'oauth_client':
		case 'connect_request':
		case 'execution_approval':
			return <ReviewLink href={item.href} label="Review" onNavigate={onNavigate} />;
		case 'credential':
			// The inventory is a sheet on the Agents page; there is no per-credential route.
			return (
				<ReviewLink
					href={ROUTE_PATHS.credentialInventory()}
					label="Finish setup"
					onNavigate={onNavigate}
				/>
			);
	}
}

function ReviewLink({
	href,
	label,
	onNavigate,
}: {
	href: string | null;
	label: string;
	onNavigate?: () => void;
}) {
	if (!href) return null;
	return (
		<AppLink href={href} variant="tonal" size="xs" onClick={onNavigate}>
			{label}
		</AppLink>
	);
}

function AgentActions({
	agentId,
	name,
	onNavigate,
}: {
	agentId: string;
	name: string;
	onNavigate?: () => void;
}) {
	const approve = useApproveAgent();
	const canApprove = useCanAccess(AGENTS_WRITE);
	return (
		<>
			{canApprove && (
				<Button
					variant="primary"
					size="xs"
					loading={approve.isPending}
					onClick={() =>
						approve.mutate(agentId, {
							onSuccess: () =>
								toast({ title: `${name} approved`, variant: 'success' }),
							onError: (error) =>
								toast({
									title: `Couldn't approve ${name}`,
									description: error.message,
									variant: 'error',
								}),
						})
					}
				>
					Approve
				</Button>
			)}
			<ReviewLink
				href={ROUTE_PATHS.agentTab(agentId)}
				label="Review"
				onNavigate={onNavigate}
			/>
		</>
	);
}
