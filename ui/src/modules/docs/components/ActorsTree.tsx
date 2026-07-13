/**
 * ActorExplorer — the visual twin of the `jentic endpoints` CLI command.
 *
 * The CLI prints every control-plane endpoint grouped by its typical caller and
 * the scope(s) it requires, filterable by `--actor`. This renders the *same*
 * join interactively: pick an actor identity (user / agent / service_account /
 * toolkit) and see exactly which endpoints that actor can be the caller of,
 * grouped into the same caller buckets (Agent-facing / Operator-facing / Any /
 * Public) the CLI uses, each row showing its required scopes.
 *
 * Because both this and the CLI consume `/reference/endpoints.json` through the
 * identical filter+group rules (see `lib/actors.ts` ↔ `cli/.../endpoints.go`),
 * the page and the terminal can never disagree about who can call what.
 */
import { useMemo, useState } from 'react';
import { UserRound, Bot, Server, Boxes, ChevronRight } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { ReferencePayload } from '@/modules/docs/api/types';
import {
	ACTOR_TYPES,
	actorCounts,
	endpointsForActor,
	groupByCaller,
	CALLER_GROUP_LABEL,
	CALLER_GROUP_BLURB,
	type ActorType,
} from '@/modules/docs/lib/actors';
import { MethodBadge } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';

const ACTOR_META: Record<
	ActorType,
	{ icon: LucideIcon; role: string; accent: string; activeBg: string; activeBorder: string }
> = {
	user: {
		icon: UserRound,
		role: 'A human operator — dashboard or CLI.',
		accent: 'text-accent-blue',
		activeBg: 'bg-accent-blue/10',
		activeBorder: 'border-accent-blue/50',
	},
	agent: {
		icon: Bot,
		role: 'Autonomous identity brokering calls on a human’s behalf.',
		accent: 'text-accent-green',
		activeBg: 'bg-accent-green/10',
		activeBorder: 'border-accent-green/50',
	},
	service_account: {
		icon: Server,
		role: 'Non-human integration; no human in the loop.',
		accent: 'text-accent-orange',
		activeBg: 'bg-accent-orange/10',
		activeBorder: 'border-accent-orange/50',
	},
	toolkit: {
		icon: Boxes,
		role: 'Credential-bearing grouping that rides the agent token.',
		accent: 'text-accent-pink',
		activeBg: 'bg-accent-pink/10',
		activeBorder: 'border-accent-pink/50',
	},
};

function ScopeTags({ scopes, public: pub }: { scopes: string[]; public: boolean }) {
	if (pub) {
		return <span className="text-success text-xs font-medium">public — no auth</span>;
	}
	if (scopes.length === 0) {
		return <span className="text-foreground/65 text-xs">any authenticated</span>;
	}
	return (
		<span className="flex flex-wrap gap-1">
			{scopes.map((s) => (
				<code
					key={s}
					className="border-border/70 bg-muted/40 text-foreground/80 rounded border px-1.5 py-0.5 font-mono text-[11px]"
				>
					{s}
				</code>
			))}
		</span>
	);
}

export interface ActorExplorerProps {
	payload: ReferencePayload;
}

export function ActorExplorer({ payload }: ActorExplorerProps) {
	const counts = useMemo(() => actorCounts(payload), [payload]);
	// Start with no actor chosen: the tabs read as a prompt ("pick one") and the
	// endpoint lists stay out of the way until the reader expresses intent.
	const [actor, setActor] = useState<ActorType | null>(null);

	const buckets = useMemo(
		() => (actor ? groupByCaller(endpointsForActor(payload, actor)) : []),
		[payload, actor],
	);
	const total = buckets.reduce((n, b) => n + b.endpoints.length, 0);
	const meta = actor ? ACTOR_META[actor] : null;

	return (
		<div className="border-border bg-card/30 overflow-hidden rounded-xl border">
			{/* Actor selector — the `--actor` filter, as tabs. */}
			<div
				role="tablist"
				aria-label="Actor type"
				className="border-border/60 grid grid-cols-2 gap-2 border-b p-3 sm:grid-cols-4"
			>
				{ACTOR_TYPES.map((a) => {
					const m = ACTOR_META[a];
					const Icon = m.icon;
					const active = a === actor;
					return (
						<button
							key={a}
							type="button"
							role="tab"
							aria-selected={active}
							onClick={() => setActor((cur) => (cur === a ? null : a))}
							className={cn(
								'flex flex-col gap-1 rounded-lg border px-3 py-2 text-left transition-colors',
								active
									? cn(m.activeBg, m.activeBorder)
									: 'border-border/50 bg-background/30 hover:bg-muted/50',
							)}
						>
							<span className="flex items-center gap-1.5">
								<Icon
									className={cn(
										'h-4 w-4 shrink-0',
										active ? m.accent : 'text-foreground/45',
									)}
									aria-hidden="true"
								/>
								<code
									className={cn(
										'font-mono text-[13px] font-semibold',
										active ? 'text-foreground' : 'text-foreground/70',
									)}
								>
									{a}
								</code>
							</span>
							<span className="text-foreground/65 text-[11px] tabular-nums">
								{counts[a]} endpoints
							</span>
						</button>
					);
				})}
			</div>

			{/* Selected actor summary + the grouped endpoint list (the CLI output). */}
			<div className="p-3 sm:p-4">
				{!actor || !meta ? (
					<p className="text-foreground/55 py-6 text-center text-sm">
						Select an actor above to see which endpoints it can call — the interactive
						form of{' '}
						<code className="text-foreground/75 font-mono text-xs">
							jentic endpoints --actor &lt;type&gt;
						</code>
						.
					</p>
				) : (
					<>
						<div className="mb-3 flex items-baseline justify-between gap-2">
							<p className="text-foreground/70 text-sm">
								<span className={cn('font-medium', meta.accent)}>{actor}</span> —{' '}
								{meta.role}
							</p>
							<span className="text-foreground/65 shrink-0 text-xs tabular-nums">
								{total} callable
							</span>
						</div>

						<div className="space-y-3">
							{buckets.map((bucket) => (
								<CallerBucketBlock
									key={bucket.group}
									label={CALLER_GROUP_LABEL[bucket.group]}
									blurb={CALLER_GROUP_BLURB[bucket.group]}
									count={bucket.endpoints.length}
									endpoints={bucket.endpoints}
								/>
							))}
						</div>

						<p className="text-foreground/65 border-border/50 mt-4 border-t pt-3 text-xs">
							Same data as{' '}
							<code className="text-foreground/70 font-mono">
								jentic endpoints --actor {actor}
							</code>
							. Actor type is <em>who</em> calls; the scope is the gate — see{' '}
							<a href="#permissions" className="text-primary underline">
								the scope tree
							</a>{' '}
							below.
						</p>
					</>
				)}
			</div>
		</div>
	);
}

/** One collapsible caller bucket: a header + its endpoint rows. */
function CallerBucketBlock({
	label,
	blurb,
	count,
	endpoints,
}: {
	label: string;
	blurb: string;
	count: number;
	endpoints: ReturnType<typeof endpointsForActor>;
}) {
	const [open, setOpen] = useState(false);
	return (
		<div className="border-border/60 bg-background/20 overflow-hidden rounded-lg border">
			<button
				type="button"
				onClick={() => setOpen((v) => !v)}
				aria-expanded={open}
				className="hover:bg-muted/30 flex w-full items-center gap-2 px-3 py-2 text-left"
			>
				<ChevronRight
					className={cn(
						'text-foreground/40 h-4 w-4 shrink-0 transition-transform',
						open && 'rotate-90',
					)}
					aria-hidden="true"
				/>
				<span className="text-foreground text-sm font-semibold">{label}</span>
				<span className="text-foreground/60 text-xs">{blurb}</span>
				<span className="text-foreground/65 ml-auto shrink-0 text-xs tabular-nums">
					{count}
				</span>
			</button>
			{open && (
				<ul className="divide-border/40 border-border/60 divide-y border-t">
					{endpoints.map((e) => (
						<li
							key={`${e.method} ${e.path}`}
							className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2"
						>
							<MethodBadge method={e.method} />
							<code className="text-foreground/90 font-mono text-[13px] break-all">
								{e.path}
							</code>
							<span className="text-foreground/30" aria-hidden="true">
								→
							</span>
							<ScopeTags scopes={e.required_scopes} public={e.public} />
							{e.summary && (
								<span className="text-foreground/65 w-full truncate pl-1 text-xs">
									{e.summary}
								</span>
							)}
						</li>
					))}
				</ul>
			)}
		</div>
	);
}
