/**
 * ApiCard — one workspace API as a clickable tile.
 *
 * Visually aligned with jentic-mini's `WorkspaceTile`: a larger vendor icon, a
 * single content column with a hover ChevronRight, a description, and a bottom
 * meta row of icon-led stats (operations · revisions · security schemes). The
 * whole card is a router link to the API's detail page, with a subtle hover
 * lift (`-translate-y-0.5` + shadow) instead of a coloured rail. A draft-only
 * API (no live revision) carries a "Draft" pill — the most common state for a
 * freshly imported API.
 */
import { ChevronRight, GitBranch, ShieldCheck, Zap } from 'lucide-react';
import { AppLink, Badge, VendorIcon } from '@/shared/ui';
import { encodeApiId } from '@/modules/workspace/api';
import type { WorkspaceApi } from '@/modules/workspace/api';
import { ROUTE_PATHS } from '@/shared/app/routes';

function titleFor(api: WorkspaceApi): string {
	return api.displayName ?? `${api.api.vendor}/${api.api.name}`;
}

export function ApiCard({ api }: { api: WorkspaceApi }) {
	const apiId = encodeApiId(api.api);
	const title = titleFor(api);
	const isDraftOnly = api.currentRevisionId === null;

	return (
		<AppLink
			href={ROUTE_PATHS.workspaceApi(apiId)}
			data-testid="workspace-api-card"
			aria-label={`Open ${title}`}
			className="group border-border/60 bg-card hover:border-border hover:bg-muted/30 focus-visible:ring-primary/40 flex h-full w-full min-w-0 flex-col gap-3 overflow-hidden rounded-xl border p-4 text-left transition-all hover:-translate-y-0.5 hover:shadow-sm focus-visible:ring-2 focus-visible:outline-none"
		>
			<div className="flex items-start gap-3">
				<VendorIcon
					name={title}
					vendor={api.api.host ?? api.api.vendor}
					iconUrl={api.iconUrl}
					size="lg"
				/>
				<div className="min-w-0 flex-1">
					<div className="flex items-center justify-between gap-2">
						<h3 className="text-foreground min-w-0 flex-1 truncate text-sm font-semibold">
							{title}
						</h3>
						<div className="flex shrink-0 items-center gap-1.5">
							{isDraftOnly ? <Badge variant="pending">Draft</Badge> : null}
							<ChevronRight
								size={16}
								aria-hidden="true"
								className="text-muted-foreground group-hover:text-foreground transition-colors"
							/>
						</div>
					</div>
					<p className="text-muted-foreground mt-0.5 truncate font-mono text-xs">
						{api.api.vendor}/{api.api.name}/{api.api.version}
					</p>
					{api.description ? (
						<p className="text-muted-foreground mt-1.5 line-clamp-2 text-xs leading-snug break-words">
							{api.description}
						</p>
					) : null}
				</div>
			</div>

			<div className="text-muted-foreground mt-auto flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px]">
				<span className="inline-flex items-center gap-1">
					<Zap size={11} aria-hidden="true" />
					{api.operationCount} op{api.operationCount === 1 ? '' : 's'}
				</span>
				<span className="inline-flex items-center gap-1">
					<GitBranch size={11} aria-hidden="true" />
					{api.revisionCount} revision{api.revisionCount === 1 ? '' : 's'}
				</span>
				{api.securitySchemes.length > 0 ? (
					<span className="inline-flex items-center gap-1">
						<ShieldCheck size={11} aria-hidden="true" />
						{api.securitySchemes.length} scheme
						{api.securitySchemes.length === 1 ? '' : 's'}
					</span>
				) : null}
			</div>
		</AppLink>
	);
}
