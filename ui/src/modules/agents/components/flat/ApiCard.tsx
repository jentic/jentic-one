/**
 * ApiCard — one API the selected agent can call, as a compact identity tile in
 * the "Can call" cards grid (the dense alternative to the `ApiRow` tree). It
 * carries only identity + status: the avatar, the API name, its credential, and
 * one status word — no sparkline, no call counts, no hover-reveal. The whole
 * card opens the SAME `ApiAccessSidebar` the rows do.
 *
 * Everything shown is derived from the same `ApiTileModel` + rules the rows
 * use, through the same `deriveTileStatus`, `vendorIconPropsFor` avatar and
 * `multiCredentialExplanation` as `ApiRow` — the two views can never disagree.
 */
import { KeyRound } from 'lucide-react';
import { Tag, Tooltip, TruncateWithTooltip, UserText, VendorIcon } from '@/shared/ui';
import { vendorIconPropsFor } from '@/shared/lib';
import { cn } from '@/shared/lib/utils';
import type { BindingRulesState } from '@/modules/agents/api';
import type { ApiTileModel } from '@/modules/agents/lib/apiTiles';
import {
	TILE_STATUS_LABEL,
	deriveTileStatus,
	isBlockedStatus,
	rulesSummaryText,
} from '@/modules/agents/lib/tileStatus';
import { TileCardStatus } from '@/modules/agents/components/flat/TileStatusMarker';
import { multiCredentialExplanation } from '@/modules/agents/components/flat/MultiCredentialNote';
import { API_CARD_HEIGHT } from '@/modules/agents/components/flat/apiCardGrid';

interface ApiCardProps {
	tile: ApiTileModel;
	/** The binding's rules state (same source the row reads), driving the status. */
	rules: BindingRulesState | undefined;
	/** Whether the AGENT this card belongs to is serving traffic. */
	agentServing: boolean;
	/** The credential label when several accounts serve this API (else the plain name). */
	accountLabel?: string;
	/** How many of the agent's bindings serve this API. */
	accountCount?: number;
	/** Whether the access sheet is currently open for this card. */
	expanded: boolean;
	/** DOM id of the sheet this card controls (aria-controls). */
	sidebarId: string;
	/** Open the access sheet for this card's binding. */
	onOpen: () => void;
}

export function ApiCard({
	tile,
	rules,
	agentServing,
	accountLabel,
	accountCount = 1,
	expanded,
	sidebarId,
	onOpen,
}: ApiCardProps) {
	const status = deriveTileStatus({
		suspended: tile.suspended,
		agentServing,
		awaitingConsent: tile.awaitingConsent,
		rules,
	});
	const blocked = isBlockedStatus(status);
	const credentialLabel = (accountLabel ?? tile.credentialName).trim() || 'No credential';
	// The one-word marker is terse on purpose; its tooltip and the card's
	// accessible name carry the full phrase ("Blocked · no rules"), the tooltip
	// with the row's rules summary where it adds one ("no rules" already says
	// the rest).
	const statusLabel = TILE_STATUS_LABEL[status];
	const summary = status === 'blocked-no-rules' ? null : rulesSummaryText(rules);
	const statusTooltip = summary ? `${statusLabel} · ${summary}` : statusLabel;

	const accountsPhrase =
		accountCount > 1 ? `, one of ${accountCount} credentials for this API` : '';

	return (
		<button
			type="button"
			data-testid="api-card"
			data-status={status}
			data-suspended={tile.suspended || undefined}
			aria-haspopup="dialog"
			aria-expanded={expanded}
			aria-controls={expanded ? sidebarId : undefined}
			aria-label={`${tile.title}, credential ${credentialLabel}${accountsPhrase} — ${statusLabel}. Open access details.`}
			onClick={onOpen}
			className={cn(
				// ONE fixed height for every card (and the add tile — `API_CARD_HEIGHT`),
				// with three fixed-height rows spread by `justify-between`, so each
				// row — and so every status dot — sits at the same y across the grid.
				API_CARD_HEIGHT,
				'bg-surface-1 group flex w-full min-w-0 cursor-pointer flex-col justify-between overflow-hidden rounded-[14px] px-3 py-2.5 text-left outline-none',
				'transition-[background-color,box-shadow,transform] duration-150 motion-reduce:transition-none',
				'hover:bg-surface-1-hover hover:-translate-y-0.5 hover:shadow-[var(--elevation-card-hover)] motion-reduce:hover:translate-y-0',
				'focus-visible:shadow-[0_0_0_1.5px_hsl(var(--primary)/0.65),var(--elevation-card-hover)]',
				expanded && 'bg-surface-1-hover shadow-[var(--elevation-card-hover)]',
				tile.suspended && 'bg-surface-1/55',
			)}
		>
			{/* Row 1 (28px): avatar · name (one line, ellipsis) · key+count. */}
			<div
				data-testid="card-row-name"
				className="flex h-7 min-w-0 shrink-0 items-center gap-2"
			>
				<VendorIcon
					{...vendorIconPropsFor(tile)}
					size="sm"
					className={cn('shrink-0', tile.suspended && 'saturate-[.3]')}
				/>
				<TruncateWithTooltip
					focusable={false}
					className="font-heading text-foreground-name min-w-0 flex-1 text-[13.5px] leading-[18px] font-semibold tracking-[-0.01em]"
				>
					<UserText>{tile.title}</UserText>
				</TruncateWithTooltip>
				{accountCount > 1 && (
					// Hover-only inside the card: the card is the one focus stop, and
					// its accessible name already carries the count.
					<Tooltip
						content={multiCredentialExplanation(tile.title, accountCount)}
						interactiveChild
						className="shrink-0 rounded-md"
						bubbleClassName="max-w-xs"
					>
						<Tag icon={KeyRound} data-testid="card-accounts-badge">
							{accountCount}
						</Tag>
					</Tooltip>
				)}
			</div>
			{/* Row 2 (16px): key icon · credential (one line, ellipsis). */}
			<p
				data-testid="card-credential"
				className="text-foreground-sub flex h-4 min-w-0 shrink-0 items-center gap-1.5 text-xs leading-4 whitespace-nowrap"
			>
				<KeyRound aria-hidden="true" className="text-foreground-faint h-3 w-3 shrink-0" />
				<TruncateWithTooltip focusable={false} className="min-w-0 flex-1">
					<UserText>{credentialLabel}</UserText>
				</TruncateWithTooltip>
			</p>
			{/* Row 3 (18px): status dot + word. */}
			<div
				data-testid="card-row-status"
				className="flex h-[18px] min-w-0 shrink-0 items-center whitespace-nowrap"
			>
				<Tooltip content={statusTooltip} interactiveChild placement="bottom">
					<span className="inline-flex" data-blocked={blocked || undefined}>
						<TileCardStatus status={status} />
					</span>
				</Tooltip>
			</div>
		</button>
	);
}
