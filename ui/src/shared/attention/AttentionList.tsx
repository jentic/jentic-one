/**
 * AttentionList — the rendered "needs you" list, shared by the top-bar
 * Notifications menu and the Home inbox so the two can never disagree on
 * wording, grouping, or which verbs a row offers.
 *
 * Items are grouped by what they ask of you:
 *
 *   Waiting for you — held calls, one per call, oldest first; each Review
 *                opens the call's review pop-up on the current page. The menu
 *                lists the first few and links the rest to the rail's Inbox.
 *   Alerts     — recent failures and warnings that need a look (view)
 *   Approvals  — agents and OAuth clients waiting to be let in, and agents
 *                waiting on an account connection (approve / review)
 *   Setup      — credential sign-ins nobody finished
 *
 * The cheap, reversible verb (approve an agent) runs inline; anything that
 * needs context links to where it is resolved. Every verb is permission-gated so
 * a row never offers a call the server refuses: Approve needs `agents:write`
 * (the row still links to the agent for anyone else), and "Finish setup" needs
 * `credentials:write`. A held call's Review needs no gate: the server lists only
 * the calls the viewer may decide.
 */
import { type ComponentType } from 'react';
import { AlertTriangle, Bot, KeyRound, PlugZap, ShieldQuestion } from 'lucide-react';
import { AppLink } from '@/shared/ui/AppLink';
import { UserText } from '@/shared/ui/UserText';
import { Button } from '@/shared/ui/Button';
import { InlineAction } from '@/shared/ui/InlineAction';
import { toast } from '@/shared/ui';
import { ROUTE_PATHS } from '@/shared/app/routes';
import { useNow } from '@/shared/app/rail/useNow';
import { useReviewLinks } from '@/shared/approvals';
import { useApproveAgent } from '@/shared/attention/actions';
import { ATTENTION_TILE_CLASS } from '@/shared/approvals/attentionTone';
import { expiresIn, waitingSince } from '@/shared/approvals/heldCallCopy';
import { AGENTS_WRITE, CREDENTIALS_WRITE, useCanAccess } from '@/shared/auth/useCanAccess';
import type { AttentionItem, AttentionKind } from '@/shared/attention/useAttentionItems';
import { cn, timeAgo } from '@/shared/lib/utils';

const KIND_ICON: Record<AttentionKind, ComponentType<{ className?: string }>> = {
	agent: Bot,
	oauth_client: ShieldQuestion,
	connect_request: PlugZap,
	held_execution: ShieldQuestion,
	credential: KeyRound,
	event: AlertTriangle,
};

type Group = 'waiting' | 'alerts' | 'approvals' | 'setup';

const GROUP_OF: Record<AttentionKind, Group> = {
	event: 'alerts',
	agent: 'approvals',
	oauth_client: 'approvals',
	connect_request: 'approvals',
	held_execution: 'waiting',
	credential: 'setup',
};

const GROUP_LABEL: Record<Group, string> = {
	waiting: 'Waiting for you',
	alerts: 'Alerts',
	approvals: 'Approvals',
	setup: 'Setup',
};

const GROUP_ORDER: Group[] = ['waiting', 'alerts', 'approvals', 'setup'];

/** Held calls the menu lists before it points at the rail's Inbox for the rest. */
const MENU_WAITING_LIMIT = 5;

export type AttentionListProps = {
	items: AttentionItem[];
	/** `menu`: narrow popover rows (verbs under the title). `page`: wide rows. */
	variant?: 'page' | 'menu';
	/** Fired after any link in a row is followed (the menu closes itself). */
	onNavigate?: () => void;
	/** Marks the rows this viewer hasn't read yet (the menu's own read state). */
	isUnread?: (itemKey: string) => boolean;
	/** Opens the rail's Inbox ("See all N in the inbox", menu only). */
	onOpenInbox?: () => void;
};

/** Group a pre-sorted list by what it asks of you, preserving order within a group. */
function groupItems(
	items: AttentionItem[],
): Array<{ group: Group; label: string; items: AttentionItem[] }> {
	const groups = new Map<Group, AttentionItem[]>();
	for (const item of items) {
		const group = GROUP_OF[item.kind];
		const list = groups.get(group) ?? [];
		list.push(item);
		groups.set(group, list);
	}
	return GROUP_ORDER.filter((g) => groups.has(g)).map((g) => ({
		group: g,
		label: GROUP_LABEL[g],
		items: g === 'waiting' ? oldestFirst(groups.get(g)!) : groups.get(g)!,
	}));
}

/** Held calls read oldest first: the one nearest its expiry leads. */
function oldestFirst(items: AttentionItem[]): AttentionItem[] {
	return [...items].sort((a, b) => Date.parse(a.since) - Date.parse(b.since));
}

export function AttentionList({
	items,
	variant = 'page',
	onNavigate,
	isUnread,
	onOpenInbox,
}: AttentionListProps) {
	const groups = groupItems(items);
	return (
		<div className="divide-hairline divide-y">
			{groups.map((group) => {
				// The menu keeps to the first few held calls; the rail's Inbox
				// lists every one.
				const capped = variant === 'menu' && group.group === 'waiting';
				const shown = capped ? group.items.slice(0, MENU_WAITING_LIMIT) : group.items;
				const hidden = group.items.length - shown.length;
				return (
					<section key={group.label} aria-label={group.label}>
						<h3
							className={cn(
								'text-foreground-faint text-[10.5px] font-bold tracking-[0.08em] uppercase',
								variant === 'menu' ? 'px-3 pt-2.5 pb-1' : 'px-5 pt-3 pb-1',
							)}
						>
							{group.label}
							<span className="ml-1.5 font-mono tabular-nums">
								{group.items.length}
							</span>
						</h3>
						<ul className="divide-hairline-row divide-y" aria-label={group.label}>
							{shown.map((item) => (
								<AttentionRow
									key={item.key}
									item={item}
									variant={variant}
									onNavigate={onNavigate}
									unread={isUnread?.(item.key) ?? false}
								/>
							))}
							{hidden > 0 && onOpenInbox && (
								<li className="px-3 py-2">
									<InlineAction
										className="text-primary -mx-1.5 px-1.5 text-xs font-semibold"
										onClick={() => {
											onNavigate?.();
											onOpenInbox();
										}}
									>
										See all {group.items.length} in the inbox
									</InlineAction>
								</li>
							)}
						</ul>
					</section>
				);
			})}
		</div>
	);
}

function AttentionRow({
	item,
	variant,
	onNavigate,
	unread,
}: {
	item: AttentionItem;
	variant: 'page' | 'menu';
	onNavigate?: () => void;
	unread: boolean;
}) {
	const Icon = KIND_ICON[item.kind];
	const urgent = item.urgency === 3;
	const menu = variant === 'menu';

	const tile = (
		<span
			className={cn(
				'flex shrink-0 items-center justify-center rounded-md',
				menu ? 'h-7 w-7' : 'h-8 w-8',
				item.kind === 'held_execution'
					? ATTENTION_TILE_CLASS
					: urgent
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
			<li className="flex gap-2.5 px-3 py-2.5" data-unread={unread || undefined}>
				{tile}
				<div className="min-w-0 flex-1">
					<p className="text-foreground-name line-clamp-2 text-[13px] leading-snug font-semibold">
						{unread && (
							<span
								className="bg-primary mr-1.5 inline-block h-1.5 w-1.5 rounded-full align-middle"
								aria-hidden="true"
							/>
						)}
						{unread && <span className="sr-only">Unread: </span>}
						<UserText>{item.title}</UserText>
					</p>
					<p className="text-muted-foreground mt-0.5 truncate text-xs">
						{item.heldCall ? (
							<HeldCallMeta since={item.since} expiresAt={item.heldCall.expires_at} />
						) : (
							<>
								<span title={item.since}>{timeAgo(item.since)}</span>
								{item.detail && (
									<>
										{' · '}
										<UserText>{item.detail}</UserText>
									</>
								)}
							</>
						)}
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
				<p className="text-foreground-name truncate text-sm font-semibold">
					<UserText>{item.title}</UserText>
				</p>
				{item.heldCall ? (
					<p className="text-muted-foreground truncate text-xs">
						<HeldCallMeta since={item.since} expiresAt={item.heldCall.expires_at} />
					</p>
				) : (
					item.detail && (
						<p className="text-muted-foreground truncate text-xs">
							<UserText>{item.detail}</UserText>
						</p>
					)
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
		case 'held_execution':
			return item.heldCall ? (
				<HeldCallReview heldId={item.heldCall.id} onNavigate={onNavigate} />
			) : null;
		case 'oauth_client':
		case 'connect_request':
			return <ReviewLink href={item.href} label="Review" onNavigate={onNavigate} />;
		case 'credential':
			// The inventory is a sheet on the Agents page; there is no per-credential route.
			// Finishing an OAuth sign-in writes the credential's tokens, so without
			// `credentials:write` the verb is not offered — the row stays in the
			// list as the standing fact it is, for whoever can act on it.
			return <FinishCredentialSetupLink onNavigate={onNavigate} />;
	}
}

/** "5m ago · expires in 23 h" — how long a held call has waited, and how long it has left. */
function HeldCallMeta({ since, expiresAt }: { since: string; expiresAt: string }) {
	const now = useNow();
	return (
		<>
			<span title={since}>{waitingSince(since, now)}</span>
			{' · '}
			<span title={expiresAt}>{expiresIn(expiresAt, now)}</span>
		</>
	);
}

/**
 * Review for a held call: opens its review pop-up over the current page
 * (`?review=`), after the menu closes.
 */
function HeldCallReview({ heldId, onNavigate }: { heldId: string; onNavigate?: () => void }) {
	const { openReview } = useReviewLinks();
	return (
		<Button
			variant="tonal"
			size="xs"
			onClick={() => {
				onNavigate?.();
				openReview(heldId);
			}}
		>
			Review
		</Button>
	);
}

/** "Finish setup" for an unfinished OAuth sign-in, shown only to a caller who
 * may actually complete it. */
function FinishCredentialSetupLink({ onNavigate }: { onNavigate?: () => void }) {
	const canWriteCredentials = useCanAccess(CREDENTIALS_WRITE);
	if (!canWriteCredentials) return null;
	return (
		<ReviewLink
			href={ROUTE_PATHS.credentialInventory()}
			label="Finish setup"
			onNavigate={onNavigate}
		/>
	);
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
