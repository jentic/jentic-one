/**
 * ScopeTree — the conceptual "Scopes" view: resource families with their
 * scopes laid out as an expandable implication tree.
 *
 *   - Scopes are grouped into resource **families** (Agents, Credentials, …),
 *     each a card with a one-line blurb.
 *   - Within a family, each scope is a node tinted by its **tier**
 *     (admin / write / execute / read), showing its plain-English meaning.
 *   - A scope that **implies** others renders those children indented beneath
 *     it with a connector, so the implication hierarchy is visible at a glance
 *     (e.g. `agents:write → agents:read`).
 *   - Each node shows how many endpoints in *this* instance it gates, and
 *     expands to list them.
 *
 * `org:admin` is pulled out into a separate superuser banner (it implies every
 * scope), so it doesn't dominate the family grid. The data comes from the same
 * `/reference/endpoints.json` payload as everything else.
 */
import { useMemo, useState } from 'react';
import { ChevronRight, Crown, Pencil, Eye, Zap, ShieldCheck } from 'lucide-react';
import type { ReferencePayload } from '@/modules/docs/api/types';
import {
	buildScopeFamilies,
	endpointsForScope,
	type ScopeNode,
	type ScopeTier,
} from '@/modules/docs/lib/scopeTree';
import { MethodBadge } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';

const TIER_STYLE: Record<
	ScopeTier,
	{ label: string; chip: string; dot: string; ring: string; Icon: typeof Eye }
> = {
	admin: {
		label: 'Admin',
		chip: 'bg-danger/10 text-danger border-danger/30',
		dot: 'bg-danger',
		ring: 'border-danger/40',
		Icon: Crown,
	},
	write: {
		label: 'Write',
		chip: 'bg-accent-orange/10 text-accent-orange border-accent-orange/30',
		dot: 'bg-accent-orange',
		ring: 'border-accent-orange/40',
		Icon: Pencil,
	},
	execute: {
		label: 'Execute',
		chip: 'bg-accent-blue/10 text-accent-blue border-accent-blue/30',
		dot: 'bg-accent-blue',
		ring: 'border-accent-blue/40',
		Icon: Zap,
	},
	read: {
		label: 'Read',
		chip: 'bg-accent-teal/10 text-accent-teal border-accent-teal/30',
		dot: 'bg-accent-teal',
		ring: 'border-accent-teal/40',
		Icon: Eye,
	},
};

function TierChip({ tier }: { tier: ScopeTier }) {
	const s = TIER_STYLE[tier];
	return (
		<span
			className={cn(
				'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-semibold tracking-wide uppercase',
				s.chip,
			)}
		>
			<s.Icon className="h-3 w-3" aria-hidden="true" />
			{s.label}
		</span>
	);
}

function ScopeEndpoints({ payload, scope }: { payload: ReferencePayload; scope: string }) {
	const endpoints = useMemo(() => endpointsForScope(payload, scope), [payload, scope]);
	if (endpoints.length === 0) {
		return (
			<p className="text-foreground/50 px-3 py-2 text-xs italic">
				No endpoint in this instance requires this scope directly.
			</p>
		);
	}
	return (
		<ul className="divide-border/50 max-h-72 divide-y overflow-y-auto">
			{endpoints.map((e) => (
				<li key={`${e.method} ${e.path}`} className="flex items-center gap-2 px-3 py-1.5">
					<MethodBadge method={e.method} />
					<code className="text-foreground/90 text-xs break-all">{e.path}</code>
					{e.summary && (
						<span className="text-foreground/50 truncate text-xs">— {e.summary}</span>
					)}
				</li>
			))}
		</ul>
	);
}

function ScopeCard({
	scope,
	payload,
	impliedNames,
}: {
	scope: ScopeNode;
	payload: ReferencePayload;
	impliedNames: string[];
}) {
	const [open, setOpen] = useState(false);
	const s = TIER_STYLE[scope.tier];

	return (
		<div className={cn('bg-card overflow-hidden rounded-lg border', s.ring)}>
			<button
				type="button"
				onClick={() => setOpen((v) => !v)}
				aria-expanded={open}
				className="flex w-full items-start gap-3 p-3 text-left"
			>
				<span
					className={cn('mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full', s.dot)}
					aria-hidden="true"
				/>
				<span className="min-w-0 flex-1">
					<span className="flex flex-wrap items-center gap-2">
						<code className="text-foreground font-mono text-sm font-semibold">
							{scope.name}
						</code>
						<TierChip tier={scope.tier} />
					</span>
					<span className="text-foreground/70 mt-1 block text-sm">
						{scope.description}
					</span>
					{impliedNames.length > 0 && (
						<span className="text-foreground/55 mt-1.5 block text-xs">
							Grants:{' '}
							{impliedNames.map((n, i) => (
								<span key={n}>
									<code className="text-foreground/75">{n}</code>
									{i < impliedNames.length - 1 ? ', ' : ''}
								</span>
							))}
						</span>
					)}
				</span>
				<span className="flex shrink-0 items-center gap-2">
					<span className="text-foreground/55 text-xs">
						{scope.endpointCount} endpoint{scope.endpointCount === 1 ? '' : 's'}
					</span>
					<ChevronRight
						className={cn(
							'text-foreground/40 h-4 w-4 transition-transform',
							open && 'rotate-90',
						)}
						aria-hidden="true"
					/>
				</span>
			</button>
			{open && (
				<div className="border-border/60 border-t">
					<ScopeEndpoints payload={payload} scope={scope.name} />
				</div>
			)}
		</div>
	);
}

/** A prominent, collapsed-by-default banner for the superuser scope so it
 *  stops visually dominating (it implies every other scope). */
function SuperuserBanner({ scope, payload }: { scope: ScopeNode; payload: ReferencePayload }) {
	const [open, setOpen] = useState(false);
	return (
		<div className="border-danger/40 bg-danger/5 overflow-hidden rounded-xl border">
			<button
				type="button"
				onClick={() => setOpen((v) => !v)}
				aria-expanded={open}
				className="flex w-full items-start gap-3 p-4 text-left"
			>
				<Crown className="text-danger mt-0.5 h-5 w-5 shrink-0" aria-hidden="true" />
				<span className="min-w-0 flex-1">
					<span className="flex flex-wrap items-center gap-2">
						<code className="text-foreground font-mono text-sm font-semibold">
							{scope.name}
						</code>
						<span className="text-danger inline-flex items-center gap-1 text-[10px] font-semibold tracking-wide uppercase">
							<ShieldCheck className="h-3 w-3" aria-hidden="true" />
							Superuser
						</span>
					</span>
					<span className="text-foreground/70 mt-1 block text-sm">
						{scope.description} It implies <strong>every</strong> other scope and
						bypasses endpoint scope checks at runtime — grant it sparingly.
					</span>
				</span>
				<span className="flex shrink-0 items-center gap-2">
					<span className="text-foreground/55 text-xs">{scope.endpointCount} direct</span>
					<ChevronRight
						className={cn(
							'text-foreground/40 h-4 w-4 transition-transform',
							open && 'rotate-90',
						)}
						aria-hidden="true"
					/>
				</span>
			</button>
			{open && (
				<div className="border-danger/30 border-t">
					<ScopeEndpoints payload={payload} scope={scope.name} />
				</div>
			)}
		</div>
	);
}

export interface ScopeTreeProps {
	payload: ReferencePayload;
}

export function ScopeTree({ payload }: ScopeTreeProps) {
	const families = useMemo(() => buildScopeFamilies(payload), [payload]);

	if (!families) {
		return (
			<p className="text-foreground/60 text-sm">
				This server doesn't publish the scope catalogue yet (it predates jentic-one #602).
				The API reference still works.
			</p>
		);
	}

	// Pull the superuser scope out of the normal grid so it doesn't dominate.
	const superuser = families.flatMap((f) => f.scopes).find((s) => s.is_superuser) ?? null;
	const regularFamilies = families
		.map((f) => ({ ...f, scopes: f.scopes.filter((s) => !s.is_superuser) }))
		.filter((f) => f.scopes.length > 0);

	return (
		<div className="space-y-5">
			{/* Legend */}
			<div className="border-border bg-muted/30 flex flex-wrap items-center gap-3 rounded-lg border px-3 py-2">
				<span className="text-foreground/60 text-xs font-medium">Tiers:</span>
				{(['admin', 'write', 'execute', 'read'] as const).map((tier) => (
					<TierChip key={tier} tier={tier} />
				))}
				<span className="text-foreground/50 ml-auto text-xs">
					Indented scopes are <em>implied</em> — holding the parent grants them.
				</span>
			</div>

			{superuser && <SuperuserBanner scope={superuser} payload={payload} />}

			{regularFamilies.map((family) => (
				<section
					key={family.name}
					className="border-border bg-card/40 rounded-xl border p-4"
					aria-label={`${family.label} scopes`}
				>
					<header className="mb-3">
						<h3 className="text-foreground flex items-baseline gap-2 text-base font-semibold">
							{family.label}
							<span className="text-foreground/45 font-mono text-xs">
								{family.name}:*
							</span>
						</h3>
						{family.blurb && (
							<p className="text-foreground/60 mt-0.5 text-sm">{family.blurb}</p>
						)}
					</header>

					<div className="space-y-2">
						{(() => {
							// Within a family, a scope implied by a sibling is rendered nested
							// under that sibling, never again at top level — so each scope
							// appears exactly once. (Cross-family implications, e.g.
							// capabilities:execute → apis:read, are shown via the "Grants:" line.)
							const names = new Set(family.scopes.map((s) => s.name));
							const impliedBySibling = new Set<string>();
							for (const s of family.scopes) {
								for (const child of s.implies) {
									if (names.has(child)) impliedBySibling.add(child);
								}
							}
							const roots = family.scopes.filter(
								(s) => !impliedBySibling.has(s.name),
							);
							return roots.map((scope) => {
								const childNodes = family.scopes.filter((s) =>
									scope.implies.includes(s.name),
								);
								return (
									<div key={scope.name}>
										<ScopeCard
											scope={scope}
											payload={payload}
											impliedNames={scope.implies}
										/>
										{childNodes.length > 0 && (
											<div className="border-border/50 mt-2 ml-4 space-y-2 border-l-2 pl-4">
												{childNodes.map((child) => (
													<ScopeCard
														key={child.name}
														scope={child}
														payload={payload}
														impliedNames={child.implies}
													/>
												))}
											</div>
										)}
									</div>
								);
							});
						})()}
					</div>
				</section>
			))}
		</div>
	);
}
