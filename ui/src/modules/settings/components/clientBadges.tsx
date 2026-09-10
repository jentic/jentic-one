/**
 * Presentational bits shared by the OAuth-clients roster, approval queue, and
 * detail sheet: the approval-status badge row (single-sourced from
 * `clientStatus.ts`), the type chips, and the relative-time cell.
 */
import { Badge } from '@/shared/ui';
import { formatTimestamp, timeAgo } from '@/shared/lib/utils';
import type { OAuthClient } from '@/modules/settings/api/hooks';
import {
	APPROVAL_STATUS_LABEL,
	APPROVAL_STATUS_VARIANT,
	isInactiveChipVisible,
	toApprovalStatus,
} from '@/modules/settings/components/clientStatus';

/**
 * Approval-status badge + the orthogonal "Inactive" chip. `showApproved`
 * opts the happy state in (detail header wants the full picture; the queue
 * rows only ever carry pending/denied).
 */
export function ClientStatusBadges({
	client,
	showApproved = false,
}: {
	client: OAuthClient;
	showApproved?: boolean;
}) {
	const status = toApprovalStatus(client.approval_status);
	return (
		<>
			{(status !== 'approved' || showApproved) && (
				<Badge variant={APPROVAL_STATUS_VARIANT[status]}>
					{APPROVAL_STATUS_LABEL[status]}
				</Badge>
			)}
			{isInactiveChipVisible(client) && (
				<span className="bg-muted text-muted-foreground rounded-full px-2 py-0.5 font-mono text-xs">
					Inactive
				</span>
			)}
		</>
	);
}

/**
 * Type chips: `Public` (secret-less PKCE client, D5), `DCR` (front-door
 * registration, D8), `Agent consent` (consent binds an agent, not the user —
 * the MCP marker). Renders nothing for a plain confidential admin client.
 */
export function ClientTypeChips({ client }: { client: OAuthClient }) {
	return (
		<>
			{client.token_endpoint_auth_method === 'none' && (
				<Badge variant="default">Public</Badge>
			)}
			{client.registration_source === 'dcr' && <Badge variant="default">DCR</Badge>}
			{client.consent_model === 'agent' && <Badge variant="default">Agent consent</Badge>}
		</>
	);
}

/** Relative time with the full timestamp on hover; em-dash when unknown. */
export function TimeCell({ value }: { value: string | null | undefined }) {
	if (!value) return <span aria-hidden>—</span>;
	return (
		<span className="text-muted-foreground text-xs" title={formatTimestamp(value)}>
			{timeAgo(value)}
		</span>
	);
}
