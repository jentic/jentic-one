/**
 * PermissionTree — the conceptual "Permissions" view: resource families with
 * their permissions laid out as an expandable implication tree.
 *
 *   - Permissions are grouped into resource **families** (Agents, Credentials,
 *     …), each a card with a one-line blurb.
 *   - Within a family, each permission is a node tinted by its **tier**
 *     (admin / write / execute / read), showing its plain-English meaning.
 *   - A permission that **implies** others renders those children indented
 *     beneath it with a connector, so the implication hierarchy is visible at a
 *     glance (e.g. `agents:write → agents:read`).
 *   - Each node shows how many endpoints in *this* instance it gates, and
 *     expands to list them.
 *
 * `org:admin` is pulled out into a separate superuser banner (it implies every
 * permission), so it doesn't dominate the family grid. The data comes from the
 * same `/reference/endpoints.json` payload as everything else.
 */
import { useMemo, useState } from 'react';
import { ChevronRight, Crown, Pencil, Eye, Zap, ShieldCheck } from 'lucide-react';
import type { ReferencePayload } from '@/modules/docs/api/types';
import {
	buildPermissionFamilies,
	endpointsForPermission,
	type PermissionNode,
	type PermissionTier,
} from '@/modules/docs/lib/permissionTree';
import { MethodBadge } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';

const TIER_STYLE: Record<
	PermissionTier,
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

function TierChip({ tier }: { tier: PermissionTier }) {
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

function PermissionEndpoints({
	payload,
	permission,
}: {
	payload: ReferencePayload;
	permission: string;
}) {
	const endpoints = useMemo(
		() => endpointsForPermission(payload, permission),
		[payload, permission],
	);
	if (endpoints.length === 0) {
		return (
			<p className="text-foreground/50 px-3 py-2 text-xs italic">
				No endpoint in this instance requires this permission directly.
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

function PermissionCard({
	permission,
	payload,
	impliedNames,
}: {
	permission: PermissionNode;
	payload: ReferencePayload;
	impliedNames: string[];
}) {
	const [open, setOpen] = useState(false);
	const s = TIER_STYLE[permission.tier];

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
							{permission.name}
						</code>
						<TierChip tier={permission.tier} />
					</span>
					<span className="text-foreground/70 mt-1 block text-sm">
						{permission.description}
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
						{permission.endpointCount} endpoint
						{permission.endpointCount === 1 ? '' : 's'}
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
					<PermissionEndpoints payload={payload} permission={permission.name} />
				</div>
			)}
		</div>
	);
}

/** A prominent, collapsed-by-default banner for the superuser permission so it
 *  stops visually dominating (it implies every other permission). */
function SuperuserBanner({
	permission,
	payload,
}: {
	permission: PermissionNode;
	payload: ReferencePayload;
}) {
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
							{permission.name}
						</code>
						<span className="text-danger inline-flex items-center gap-1 text-[10px] font-semibold tracking-wide uppercase">
							<ShieldCheck className="h-3 w-3" aria-hidden="true" />
							Superuser
						</span>
					</span>
					<span className="text-foreground/70 mt-1 block text-sm">
						{permission.description} It implies <strong>every</strong> other permission
						and bypasses endpoint permission checks at runtime — grant it sparingly.
					</span>
				</span>
				<span className="flex shrink-0 items-center gap-2">
					<span className="text-foreground/55 text-xs">
						{permission.endpointCount} direct
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
				<div className="border-danger/30 border-t">
					<PermissionEndpoints payload={payload} permission={permission.name} />
				</div>
			)}
		</div>
	);
}

export interface PermissionTreeProps {
	payload: ReferencePayload;
}

export function PermissionTree({ payload }: PermissionTreeProps) {
	const families = useMemo(() => buildPermissionFamilies(payload), [payload]);

	if (!families) {
		return (
			<p className="text-foreground/60 text-sm">
				This server doesn't publish the permission catalogue yet (it predates jentic-one
				#602). The API reference still works.
			</p>
		);
	}

	// Pull the superuser permission out of the normal grid so it doesn't dominate.
	const superuser = families.flatMap((f) => f.permissions).find((p) => p.is_superuser) ?? null;
	const regularFamilies = families
		.map((f) => ({ ...f, permissions: f.permissions.filter((p) => !p.is_superuser) }))
		.filter((f) => f.permissions.length > 0);

	return (
		<div className="space-y-5">
			{/* Legend */}
			<div className="border-border bg-muted/30 flex flex-wrap items-center gap-3 rounded-lg border px-3 py-2">
				<span className="text-foreground/60 text-xs font-medium">Tiers:</span>
				{(['admin', 'write', 'execute', 'read'] as const).map((tier) => (
					<TierChip key={tier} tier={tier} />
				))}
				<span className="text-foreground/50 ml-auto text-xs">
					Indented permissions are <em>implied</em> — holding the parent grants them.
				</span>
			</div>

			{superuser && <SuperuserBanner permission={superuser} payload={payload} />}

			{regularFamilies.map((family) => (
				<section
					key={family.name}
					className="border-border bg-card/40 rounded-xl border p-4"
					aria-label={`${family.label} permissions`}
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
							// Within a family, a permission implied by a sibling is rendered
							// nested under that sibling, never again at top level — so each
							// appears exactly once. (Cross-family implications, e.g.
							// capabilities:execute → apis:read, are shown via the "Grants:" line.)
							const names = new Set(family.permissions.map((p) => p.name));
							const impliedBySibling = new Set<string>();
							for (const p of family.permissions) {
								for (const child of p.implies) {
									if (names.has(child)) impliedBySibling.add(child);
								}
							}
							const roots = family.permissions.filter(
								(p) => !impliedBySibling.has(p.name),
							);
							return roots.map((permission) => {
								const childNodes = family.permissions.filter((p) =>
									permission.implies.includes(p.name),
								);
								return (
									<div key={permission.name}>
										<PermissionCard
											permission={permission}
											payload={payload}
											impliedNames={permission.implies}
										/>
										{childNodes.length > 0 && (
											<div className="border-border/50 mt-2 ml-4 space-y-2 border-l-2 pl-4">
												{childNodes.map((child) => (
													<PermissionCard
														key={child.name}
														permission={child}
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
