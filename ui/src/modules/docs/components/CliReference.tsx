/**
 * CliReference — the "CLI" view of the docs page.
 *
 * Renders the full command set of both CLI binaries (`jentic` and `jenticctl`)
 * from the generated `cli-reference.json` (produced by `make cli-reference`
 * from the cobra definitions, so it never drifts from `--help`).
 *
 * Layout follows the Stripe / GitHub-CLI reference pattern: a sticky command
 * index on the left and a single continuously-scrolling document on the right.
 * The tree is *flattened* into anchored command blocks (every subcommand gets
 * its own block, addressed by its full path) instead of deeply-nested
 * collapsibles, so a reader can scan the whole surface and deep-link to any
 * command. Each block is consistent: signature, description, flags table,
 * runnable example.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { Terminal, CornerDownRight, Filter } from 'lucide-react';
import type { CliBinary, CliCommand, CliFlag } from '@/modules/docs/api/types';
import { CopyButton, Input, LazyMount } from '@/shared/ui';
import { useScrollSpy } from '@/modules/docs/lib/useScrollSpy';
import { cn } from '@/shared/lib/utils';

/** A command flattened out of the tree, carrying its nesting depth. */
interface FlatCommand {
	cmd: CliCommand;
	depth: number;
	/** cobra group title of the *top-level* command this belongs under. */
	group: string;
}

/** Walk the tree depth-first into a flat, ordered list of commands. */
function flatten(commands: CliCommand[], depth = 0, group?: string): FlatCommand[] {
	const out: FlatCommand[] = [];
	for (const cmd of commands) {
		const g = depth === 0 ? cmd.group_title || 'Commands' : (group ?? 'Commands');
		out.push({ cmd, depth, group: g });
		if (cmd.subcommands?.length) {
			out.push(...flatten(cmd.subcommands, depth + 1, g));
		}
	}
	return out;
}

/** Anchor id for a command block (stable, path-derived). */
function anchorId(cmd: CliCommand): string {
	return `cmd-${cmd.path.replace(/\s+/g, '-')}`;
}

/** Anchor id for a binary's header block (matches the sidebar sub-items). */
function binaryAnchorId(name: string): string {
	return `cli-${name}`;
}

/** Document order for binaries — installer/operator tool (`jenticctl`) first,
 *  then the day-to-day catalog CLI (`jentic`); unknown names sort last. */
function binaryRank(name: string): number {
	const order = ['jenticctl', 'jentic'];
	const i = order.indexOf(name);
	return i === -1 ? order.length : i;
}

function matchesQuery(cmd: CliCommand, q: string): boolean {
	if (!q) return true;
	return `${cmd.path} ${cmd.short} ${cmd.long ?? ''} ${cmd.aliases?.join(' ') ?? ''}`
		.toLowerCase()
		.includes(q);
}

/** Extract the positional-arg portion of cobra's `Use` line (drops the name). */
function argSpec(cmd: CliCommand): string | null {
	const rest = cmd.use.replace(/^\S+\s*/, '').trim();
	return rest.length > 0 ? rest : null;
}

function FlagsTable({ flags }: { flags: CliFlag[] }) {
	return (
		<div className="border-border/60 overflow-hidden rounded-md border">
			<table className="w-full text-left text-[13px]">
				<thead className="bg-muted/40 text-foreground/55">
					<tr>
						<th className="px-3 py-1.5 font-medium">Flag</th>
						<th className="px-3 py-1.5 font-medium">Type</th>
						<th className="px-3 py-1.5 font-medium">Default</th>
						<th className="px-3 py-1.5 font-medium">Description</th>
					</tr>
				</thead>
				<tbody className="divide-border/40 divide-y">
					{flags.map((f) => (
						<tr key={f.name} className="align-top">
							<td className="px-3 py-1.5 font-mono whitespace-nowrap">
								<span className="text-primary">--{f.name}</span>
								{f.shorthand && (
									<span className="text-foreground/45">, -{f.shorthand}</span>
								)}
							</td>
							<td className="text-foreground/55 px-3 py-1.5 font-mono">{f.type}</td>
							<td className="text-foreground/55 px-3 py-1.5 font-mono">
								{f.default ? f.default : '—'}
							</td>
							<td className="text-foreground/75 px-3 py-1.5">{f.usage}</td>
						</tr>
					))}
				</tbody>
			</table>
		</div>
	);
}

/** The terminal-style invocation signature: `jentic catalog import <api_id>`. */
function Signature({ cmd }: { cmd: CliCommand }) {
	const args = argSpec(cmd);
	return (
		<code className="text-foreground font-mono text-[15px] font-semibold break-all">
			<span className="text-primary/70 mr-1.5 select-none">$</span>
			{cmd.path}
			{args && <span className="text-accent-orange/90 ml-1.5 font-normal">{args}</span>}
		</code>
	);
}

function CommandBlock({ cmd, depth }: { cmd: CliCommand; depth: number }) {
	const example = cmd.example?.trimEnd();
	return (
		<div className={cn('border-b py-5 first:pt-0 last:border-b-0', 'border-border/40')}>
			<div className="flex flex-wrap items-center gap-2">
				{depth > 0 && (
					<CornerDownRight
						className="text-foreground/25 h-4 w-4 shrink-0"
						aria-hidden="true"
					/>
				)}
				<Signature cmd={cmd} />
				{cmd.aliases?.map((a) => (
					<span
						key={a}
						className="text-foreground/55 border-border/70 bg-muted/30 rounded border px-1.5 py-0.5 font-mono text-[11px]"
					>
						{a}
					</span>
				))}
			</div>

			<p className="text-foreground/75 mt-2 text-sm">{cmd.short}</p>

			{cmd.long && cmd.long.trim() !== cmd.short.trim() && (
				<p className="text-foreground/55 mt-2 max-w-3xl text-[13px] leading-relaxed whitespace-pre-line">
					{cmd.long}
				</p>
			)}

			{cmd.flags && cmd.flags.length > 0 && (
				<div className="mt-3">
					<h5 className="text-foreground/45 mb-1.5 text-[11px] font-semibold tracking-wide uppercase">
						Flags
					</h5>
					<FlagsTable flags={cmd.flags} />
				</div>
			)}

			{example && (
				<div className="mt-3">
					<h5 className="text-foreground/45 mb-1.5 text-[11px] font-semibold tracking-wide uppercase">
						Example
					</h5>
					<div className="border-border/60 relative overflow-hidden rounded-md border bg-black/30">
						<pre className="text-foreground/90 overflow-x-auto p-3 pr-12 font-mono text-[13px] leading-relaxed">
							{example}
						</pre>
						<div className="absolute top-2 right-2">
							<CopyButton value={example} size="icon" variant="ghost" />
						</div>
					</div>
				</div>
			)}
		</div>
	);
}

/** Group flattened commands by their top-level cobra group, preserving order. */
function groupFlat(flat: FlatCommand[]): { title: string; items: FlatCommand[] }[] {
	const order: string[] = [];
	const byTitle = new Map<string, FlatCommand[]>();
	for (const fc of flat) {
		if (!byTitle.has(fc.group)) {
			byTitle.set(fc.group, []);
			order.push(fc.group);
		}
		byTitle.get(fc.group)!.push(fc);
	}
	return order.map((title) => ({ title, items: byTitle.get(title)! }));
}

function jump(id: string) {
	const el = document.getElementById(id);
	if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

interface BinaryGroups {
	binary: CliBinary;
	groups: { title: string; items: FlatCommand[] }[];
	total: number;
}

/** The merged left index: every binary, each command grouped under it. */
function CommandIndex({ data, activeId }: { data: BinaryGroups[]; activeId: string | null }) {
	const navRef = useRef<HTMLElement | null>(null);

	// Keep the highlighted entry in view as the reader scrolls the document.
	useEffect(() => {
		if (!activeId) return;
		const nav = navRef.current;
		if (!nav) return;
		const el = nav.querySelector<HTMLElement>(`[data-index-for="${CSS.escape(activeId)}"]`);
		if (!el) return;
		const navBox = nav.getBoundingClientRect();
		const elBox = el.getBoundingClientRect();
		if (elBox.top < navBox.top || elBox.bottom > navBox.bottom) {
			el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
		}
	}, [activeId]);

	return (
		<nav
			ref={navRef}
			aria-label="CLI commands"
			className="hidden lg:sticky lg:top-24 lg:block lg:max-h-[calc(100vh-7rem)] lg:self-start lg:overflow-y-auto"
		>
			<div className="space-y-5">
				{data.map(({ binary, groups, total }) => {
					const binId = binaryAnchorId(binary.name);
					return (
						<div key={binary.name}>
							<button
								type="button"
								data-index-for={binId}
								onClick={() => jump(binId)}
								className={cn(
									'mb-1.5 flex w-full items-center gap-1.5 px-2 text-left font-mono text-sm font-semibold transition-colors',
									activeId === binId
										? 'text-primary'
										: 'text-foreground hover:text-primary',
								)}
							>
								<Terminal
									className="text-primary h-3.5 w-3.5 shrink-0"
									aria-hidden="true"
								/>
								{binary.name}
							</button>
							{total === 0 ? (
								<p className="text-foreground/35 px-2 text-[12px] italic">
									no matches
								</p>
							) : (
								groups.map((group) => (
									<div key={group.title} className="mb-2">
										<p className="text-foreground/40 mb-0.5 px-2 text-[10px] font-semibold tracking-wider uppercase">
											{group.title}
										</p>
										<ul className="space-y-0.5">
											{group.items.map(({ cmd, depth }) => {
												const id = anchorId(cmd);
												const active = activeId === id;
												return (
													<li key={cmd.path}>
														<button
															type="button"
															data-index-for={id}
															aria-current={
																active ? 'location' : undefined
															}
															onClick={() => jump(id)}
															className={cn(
																'block w-full truncate rounded px-2 py-1 text-left font-mono text-[13px] transition-colors',
																active
																	? 'bg-primary/10 text-primary font-medium'
																	: 'text-foreground/65 hover:bg-muted hover:text-foreground',
															)}
															style={{
																paddingLeft: `${0.5 + depth * 0.75}rem`,
															}}
															title={cmd.path}
														>
															{cmd.path
																.split(' ')
																.slice(1)
																.join(' ') || cmd.path}
														</button>
													</li>
												);
											})}
										</ul>
									</div>
								))
							)}
						</div>
					);
				})}
			</div>
		</nav>
	);
}

/** One binary's section in the continuous document: header + grouped blocks. */
function BinaryDocument({ binary, groups, total }: BinaryGroups) {
	return (
		<section id={binaryAnchorId(binary.name)} className="scroll-mt-24">
			<header className="border-border bg-card/40 mb-4 flex items-center gap-2 rounded-lg border px-4 py-3">
				<Terminal className="text-primary h-5 w-5 shrink-0" aria-hidden="true" />
				<div className="min-w-0">
					<code className="text-foreground font-mono text-sm font-semibold">
						{binary.name}
					</code>
					{binary.tagline && (
						<span className="text-foreground/60 ml-2 text-sm">{binary.tagline}</span>
					)}
				</div>
			</header>

			{total === 0 ? (
				<p className="text-foreground/50 py-2 pl-1 text-sm">
					No commands match your filter.
				</p>
			) : (
				groups.map((group) => (
					<div key={group.title} className="mb-6">
						<h3 className="text-foreground/65 border-border/40 mb-2 border-b pb-1 text-xs font-semibold tracking-wider uppercase">
							{group.title}
						</h3>
						<div>
							{group.items.map(({ cmd, depth }) => (
								<LazyMount
									key={cmd.path}
									id={anchorId(cmd)}
									className="scroll-mt-24"
									minHeight={120}
								>
									<CommandBlock cmd={cmd} depth={depth} />
								</LazyMount>
							))}
						</div>
					</div>
				))
			)}
		</section>
	);
}

export interface CliReferenceViewProps {
	binaries: CliBinary[];
}

export function CliReferenceView({ binaries }: CliReferenceViewProps) {
	const [filter, setFilter] = useState('');
	const q = filter.trim().toLowerCase();

	const data = useMemo<BinaryGroups[]>(
		() =>
			binaries
				// Install-first narrative: `jenticctl` (stand up & operate the
				// platform) precedes `jentic` (use the catalog day-to-day) — the
				// same order jenticctl's own --help recommends ("install locally…
				// once installed, use the jentic CLI").
				.slice()
				.sort((a, b) => binaryRank(a.name) - binaryRank(b.name))
				.map((binary) => {
					const flat = flatten(binary.commands).filter((fc) => matchesQuery(fc.cmd, q));
					const groups = groupFlat(flat);
					return { binary, groups, total: flat.length };
				}),
		[binaries, q],
	);

	const matchTotal = data.reduce((n, d) => n + d.total, 0);

	// Spy ids: each binary header + every visible command block, in document
	// order. The CLI index highlights + auto-scrolls to the active one.
	const spyIds = useMemo(
		() =>
			data.flatMap((d) => [
				binaryAnchorId(d.binary.name),
				...d.groups.flatMap((g) => g.items.map(({ cmd }) => anchorId(cmd))),
			]),
		[data],
	);
	const activeCmd = useScrollSpy(spyIds, '-120px 0px -70% 0px', false);

	return (
		<div className="space-y-4">
			{/* Sticky filter affordance (per design system: filter icon, not a
			    search box — global search lives in the top navbar). Full-width and
			    sticky so it stays reachable while reading the long reference. */}
			<div className="bg-background/95 supports-[backdrop-filter]:bg-background/80 z-10 -mx-1 px-1 py-2 backdrop-blur lg:sticky lg:top-[3.75rem]">
				<div className="flex items-center gap-3">
					<div className="flex-1">
						<Input
							value={filter}
							onChange={(e) => setFilter(e.target.value)}
							placeholder="Filter commands"
							aria-label="Filter CLI commands"
							startIcon={<Filter className="h-3.5 w-3.5" />}
						/>
					</div>
					{q && (
						<span className="text-foreground/50 shrink-0 text-xs">
							{matchTotal} match{matchTotal === 1 ? '' : 'es'}
						</span>
					)}
				</div>
			</div>

			<div className="grid grid-cols-1 gap-6 lg:grid-cols-[14rem_minmax(0,1fr)]">
				<CommandIndex data={data} activeId={activeCmd} />
				<div className="min-w-0 space-y-10">
					{data.map((d) => (
						<BinaryDocument key={d.binary.name} {...d} />
					))}
				</div>
			</div>
		</div>
	);
}
