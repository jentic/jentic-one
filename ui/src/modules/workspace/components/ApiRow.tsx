/**
 * ApiRow — one workspace API as a dense, clickable row.
 *
 * In a shared workspace, whoever opens this list asks "is everything our
 * agents depend on OK?", not "how many operations does it have?". So each
 * row answers, left to right: which API (and what needs attention on it),
 * whether it is serving, who is calling it and how that's going, whether
 * the gateway holds a credential for it, and when it last changed.
 *
 * Unknown is rendered as "—", never as zero: traffic beyond the busiest APIs
 * the usage endpoint returns, or credentials still paging in, must not read
 * as "no calls" / "no credential".
 */
import { ChevronRight } from 'lucide-react';
import { AppLink, Badge, VendorIcon } from '@/shared/ui';
import type { BadgeVariant } from '@/shared/ui';
import { apiRefDisplayName } from '@/shared/lib';
import { ROUTE_PATHS } from '@/shared/app';
import {
	API_ATTENTION_LABEL,
	encodeApiId,
	formatAgo,
	USAGE_WINDOW_DAYS,
} from '@/modules/workspace/api';
import type { ApiAttention, UsageRow, WorkspaceApi } from '@/modules/workspace/api';

/**
 * The row's title. `apiRefDisplayName` can return `''` when a workspace API
 * has no display name and only generic/empty identity fields (e.g. `vendor:''`,
 * `name:'main'`) — which would render a blank heading and an empty aria-label /
 * VendorIcon name. Chain a guaranteed non-empty fallback off the API's own
 * identity so the row is always titled.
 */
export function apiTitle(api: WorkspaceApi): string {
	return (
		apiRefDisplayName({
			displayName: api.displayName,
			catalogApiId: api.catalogApiId,
			vendor: api.api.vendor,
			name: api.api.name,
		}) ||
		api.api.vendor ||
		api.api.name ||
		encodeApiId(api.api) ||
		'Untitled API'
	);
}

const ATTENTION_VARIANT: Record<ApiAttention, BadgeVariant> = {
	failing: 'danger',
	'no-credential': 'warning',
	draft: 'pending',
	update: 'warning',
};

/** Shared by the row and the column header so the two can't drift apart. */
export const API_ROW_GRID =
	'md:grid md:grid-cols-[minmax(0,2.4fr)_minmax(0,1fr)_minmax(0,1.1fr)_minmax(0,1fr)_minmax(0,0.8fr)_1rem] md:items-center md:gap-4';

function Cell({ label, children }: { label: string; children: React.ReactNode }) {
	// The column header is visual-only (and hidden on phones), so each cell
	// carries its own label — inline on phones, screen-reader-only above.
	return (
		<div className="flex min-w-0 items-baseline gap-1.5 text-xs md:block">
			<span className="text-muted-foreground/80 md:sr-only">{label}:</span>
			<span className="min-w-0 truncate">{children}</span>
		</div>
	);
}

function TrafficCell({ traffic }: { traffic: UsageRow | null | undefined }) {
	if (traffic === undefined) return <span className="text-muted-foreground">—</span>;
	if (traffic === null || traffic.total === 0)
		return <span className="text-muted-foreground">No calls</span>;
	return (
		<span className="text-foreground font-mono">
			{traffic.total.toLocaleString()} call{traffic.total === 1 ? '' : 's'}
			{traffic.failed > 0 ? (
				<span className="text-danger"> · {traffic.failed.toLocaleString()} failed</span>
			) : null}
		</span>
	);
}

function CredentialCell({ count, needed }: { count: number | undefined; needed: boolean }) {
	if (count === undefined) return <span className="text-muted-foreground">—</span>;
	if (count === 0)
		return needed ? (
			<span className="text-warning">None</span>
		) : (
			<span className="text-muted-foreground">Not required</span>
		);
	return (
		<span className="text-foreground">
			{count} credential{count === 1 ? '' : 's'}
		</span>
	);
}

export interface ApiRowProps {
	api: WorkspaceApi;
	attention: ApiAttention[];
	traffic: UsageRow | null | undefined;
	credentialCount: number | undefined;
}

export function ApiRow({ api, attention, traffic, credentialCount }: ApiRowProps) {
	const title = apiTitle(api);
	const serving = api.currentRevisionId !== null;
	const updatedAgo = formatAgo(api.updatedAt);

	return (
		<AppLink
			href={ROUTE_PATHS.workspaceApi(encodeApiId(api.api))}
			data-testid="workspace-api-row"
			aria-label={`Open ${title}`}
			className={`group hover:bg-muted/40 focus-visible:ring-primary/40 flex flex-col gap-2 px-4 py-3 text-left transition-colors focus-visible:ring-2 focus-visible:outline-none focus-visible:ring-inset ${API_ROW_GRID}`}
		>
			<div className="flex min-w-0 items-center gap-3">
				<VendorIcon
					name={title}
					vendor={api.api.host ?? api.api.vendor}
					iconUrl={api.iconUrl}
					size="sm"
				/>
				<div className="min-w-0 flex-1">
					<div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
						<h3 className="text-foreground truncate text-sm font-semibold">{title}</h3>
						{attention.map((reason) => (
							<Badge
								key={reason}
								variant={ATTENTION_VARIANT[reason]}
								data-testid={
									reason === 'update' ? 'update-available-badge' : undefined
								}
								className="px-1.5 py-0 text-[10px]"
							>
								{API_ATTENTION_LABEL[reason]}
							</Badge>
						))}
					</div>
					<p className="text-muted-foreground truncate font-mono text-[11px]">
						{api.api.vendor}/{api.api.name} · {api.api.version}
					</p>
				</div>
			</div>

			<Cell label="Serving">
				{serving ? (
					<span className="text-foreground">
						Live · {api.operationCount.toLocaleString()} op
						{api.operationCount === 1 ? '' : 's'}
					</span>
				) : (
					<span className="text-muted-foreground">Draft only</span>
				)}
			</Cell>
			<Cell label={`Calls, last ${USAGE_WINDOW_DAYS} days`}>
				<TrafficCell traffic={traffic} />
			</Cell>
			<Cell label="Credentials">
				<CredentialCell count={credentialCount} needed={api.securitySchemes.length > 0} />
			</Cell>
			<Cell label="Updated">
				{updatedAgo ? (
					<time
						dateTime={new Date(api.updatedAt).toISOString()}
						className="text-muted-foreground"
					>
						{updatedAgo}
					</time>
				) : (
					<span className="text-muted-foreground">—</span>
				)}
			</Cell>
			<ChevronRight
				size={16}
				aria-hidden="true"
				className="text-muted-foreground group-hover:text-foreground hidden transition-colors md:block"
			/>
		</AppLink>
	);
}
