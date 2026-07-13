/**
 * PermissionsSection — the conceptual authorization model, then the visual
 * scope tree, then the ownership-gated endpoints.
 *
 * The explainer up top resolves the most common confusion: many endpoints
 * require *no* scope. That is not a gap — they are authorized by **ownership /
 * binding** checks (you can only touch resources you created, unless you hold
 * `org:admin`). Scopes are the *other* model: a coarse capability grant that a
 * subset of operator/agent endpoints require. Showing both side-by-side, with
 * live counts, is what makes the empty-scope endpoints make sense.
 */
import { useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { ShieldCheck, KeySquare, UserCircle, ChevronRight } from 'lucide-react';
import type { ReferencePayload } from '@/modules/docs/api/types';
import { authModelCounts, ownershipEndpoints } from '@/modules/docs/lib/scopeTree';
import { DocsSectionBlock } from '@/modules/docs/components/DocsSectionBlock';
import { ScopeTree } from '@/modules/docs/components/ScopeTree';
import { TierLadder } from '@/modules/docs/components/TierLadder';
import { ActorExplorer } from '@/modules/docs/components/ActorsTree';
import { MethodBadge } from '@/shared/ui';

function ModelCard({
	icon: Icon,
	title,
	count,
	children,
}: {
	icon: typeof KeySquare;
	title: string;
	count: number;
	children: ReactNode;
}) {
	return (
		<div className="border-border bg-card/50 rounded-lg border p-4">
			<div className="flex items-center gap-2">
				<Icon className="text-primary h-5 w-5" aria-hidden="true" />
				<p className="font-heading text-foreground font-semibold">{title}</p>
				<span className="border-border bg-muted/60 text-foreground/70 ml-auto rounded-full border px-2 py-0.5 text-xs font-medium">
					{count} endpoint{count === 1 ? '' : 's'}
				</span>
			</div>
			<p className="text-foreground/65 mt-1.5 text-sm leading-relaxed">{children}</p>
		</div>
	);
}

function OwnershipEndpoints({ payload }: { payload: ReferencePayload }) {
	const [open, setOpen] = useState(false);
	const endpoints = useMemo(() => ownershipEndpoints(payload), [payload]);
	if (endpoints.length === 0) return null;
	return (
		<div className="border-border bg-card/40 overflow-hidden rounded-xl border">
			<button
				type="button"
				onClick={() => setOpen((v) => !v)}
				aria-expanded={open}
				className="flex w-full items-center gap-2 p-4 text-left"
			>
				<UserCircle className="text-primary h-5 w-5 shrink-0" aria-hidden="true" />
				<span className="min-w-0 flex-1">
					<span className="font-heading text-foreground block font-semibold">
						Ownership-gated endpoints
					</span>
					<span className="text-foreground/60 mt-0.5 block text-sm">
						Authenticated, but require <em>no</em> scope — you may act on resources you
						own (or any, with <code className="font-mono">org:admin</code>).
					</span>
				</span>
				<span className="flex shrink-0 items-center gap-2">
					<span className="text-foreground/55 text-xs">{endpoints.length} endpoints</span>
					<ChevronRight
						className={
							open
								? 'text-foreground/40 h-4 w-4 rotate-90'
								: 'text-foreground/40 h-4 w-4'
						}
						aria-hidden="true"
					/>
				</span>
			</button>
			{open && (
				<ul className="divide-border/50 border-border/60 max-h-96 divide-y overflow-y-auto border-t">
					{endpoints.map((e) => (
						<li
							key={`${e.method} ${e.path}`}
							className="flex items-center gap-2 px-4 py-1.5"
						>
							<MethodBadge method={e.method} />
							<code className="text-foreground/90 text-xs break-all">{e.path}</code>
							{e.summary && (
								<span className="text-foreground/65 truncate text-xs">
									— {e.summary}
								</span>
							)}
						</li>
					))}
				</ul>
			)}
		</div>
	);
}

export function PermissionsSection({ payload }: { payload: ReferencePayload }) {
	const counts = useMemo(() => authModelCounts(payload.endpoints), [payload]);

	return (
		<DocsSectionBlock
			id="permissions"
			title="Permissions & scopes"
			icon={ShieldCheck}
			intro="Authorization works two ways. Knowing which applies to an endpoint explains why some require a scope and many don't."
		>
			<div className="grid gap-3 sm:grid-cols-2">
				<ModelCard icon={KeySquare} title="Scopes" count={counts.scopeGated}>
					Coarse capability grants on the token (e.g.{' '}
					<code className="font-mono">agents:write</code>). Operator and agent operations
					check these. Scopes form an implication tree — a broader scope grants narrower
					ones.
				</ModelCard>
				<ModelCard
					icon={UserCircle}
					title="Ownership & binding"
					count={counts.ownershipGated}
				>
					No scope needed. You may read or modify resources you created (and credentials
					bound to your toolkits). <code className="font-mono">org:admin</code> sees
					everything.
				</ModelCard>
			</div>

			<div>
				<h3 className="font-heading text-foreground mb-1 text-base font-semibold">
					Who can call what
				</h3>
				<p className="text-foreground/55 mb-2 text-sm">
					Pick an actor to see exactly which endpoints it can call, grouped by typical
					caller — the interactive form of{' '}
					<code className="text-foreground/75 font-mono text-xs">jentic endpoints</code>.
				</p>
				<ActorExplorer payload={payload} />
			</div>

			<div>
				<h3 className="font-heading text-foreground mb-2 text-base font-semibold">
					The scope tree
				</h3>
				<div className="space-y-4">
					<TierLadder />
					<ScopeTree payload={payload} />
				</div>
			</div>

			<div>
				<h3 className="font-heading text-foreground mb-2 text-base font-semibold">
					Endpoints without a scope
				</h3>
				<OwnershipEndpoints payload={payload} />
			</div>
		</DocsSectionBlock>
	);
}
