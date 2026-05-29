/**
 * OperationInspect
 *
 * Shared utilities and renderer for operation drill-down (inspect) panels.
 * Used by both workspace `InspectPanel` (drilling from a registered op) and
 * `DirectoryInspectPanel` (drilling from a directory preview).
 */

import type React from 'react';
import { SectionTitle } from './SectionTitle';
import { MethodBadge } from '@/components/ui/Badge';
import { TruncateWithTooltip } from '@/components/ui/TruncateWithTooltip';

// ── Types ─────────────────────────────────────────────────────────────────────

export interface InspectParam {
	name: string;
	in: string;
	required: boolean;
	description?: string;
}

export interface InspectAuthEntry {
	label: string;
	sub?: string;
	description?: string;
}

// ── Component ─────────────────────────────────────────────────────────────────

export function OperationInspectContent({
	method,
	path,
	summary,
	description,
	parameters,
	auth,
	footer,
	testId,
}: {
	method?: string;
	path?: string;
	summary?: string;
	description?: string;
	parameters: InspectParam[];
	auth: InspectAuthEntry[];
	footer?: React.ReactNode;
	testId?: string;
}) {
	return (
		<div className="min-w-0 space-y-5 p-5" data-testid={testId}>
			<div className="space-y-2">
				{(method || path) && (
					<div className="flex items-start gap-2">
						{method && <MethodBadge method={method} />}
						{path && (
							<code className="text-foreground min-w-0 font-mono text-sm break-all">
								{path}
							</code>
						)}
					</div>
				)}
				{summary && <p className="text-foreground text-sm font-medium">{summary}</p>}
				{description && description !== summary && (
					<p className="text-muted-foreground text-sm leading-relaxed">{description}</p>
				)}
			</div>

			{parameters.length > 0 && (
				<section>
					<SectionTitle count={parameters.length}>Parameters</SectionTitle>
					<div className="border-border/40 overflow-x-auto rounded-lg border">
						<table className="w-full min-w-[400px] table-fixed text-left text-xs">
							<colgroup>
								<col className="w-[30%]" />
								<col className="w-[12%]" />
								<col className="w-[13%]" />
								<col className="w-[45%]" />
							</colgroup>
							<thead>
								<tr className="border-border/40 bg-muted/30 border-b text-[11px]">
									<th className="text-muted-foreground px-3 py-1.5 font-medium">
										Name
									</th>
									<th className="text-muted-foreground px-3 py-1.5 font-medium">
										In
									</th>
									<th className="text-muted-foreground px-3 py-1.5 font-medium">
										Required
									</th>
									<th className="text-muted-foreground px-3 py-1.5 font-medium">
										Description
									</th>
								</tr>
							</thead>
							<tbody className="divide-border/30 divide-y">
								{parameters.slice(0, 20).map((p) => (
									<tr key={`${p.in}-${p.name}`}>
										<td className="px-3 py-1.5">
											<code className="text-foreground block truncate font-mono">
												{p.name}
											</code>
										</td>
										<td className="text-muted-foreground px-3 py-1.5">
											{p.in}
										</td>
										<td className="px-3 py-1.5">
											{p.required ? (
												<span className="text-danger text-[10px] font-medium">
													yes
												</span>
											) : (
												<span className="text-muted-foreground/60 text-[10px]">
													no
												</span>
											)}
										</td>
										<td className="text-muted-foreground max-w-[200px] px-3 py-1.5">
											<TruncateWithTooltip>
												{p.description || '—'}
											</TruncateWithTooltip>
										</td>
									</tr>
								))}
							</tbody>
						</table>
						{parameters.length > 20 && (
							<div className="border-border/40 text-muted-foreground border-t px-3 py-1.5 text-[11px]">
								+ {parameters.length - 20} more parameters
							</div>
						)}
					</div>
				</section>
			)}

			{auth.length > 0 && (
				<section>
					<SectionTitle>Authentication</SectionTitle>
					<div className="border-border/40 overflow-x-auto rounded-lg border">
						<table className="w-full min-w-[320px] table-fixed text-left text-xs">
							<colgroup>
								<col className="w-[30%]" />
								<col className="w-[25%]" />
								<col className="w-[45%]" />
							</colgroup>
							<thead>
								<tr className="border-border/40 bg-muted/30 border-b text-[11px]">
									<th className="text-muted-foreground px-3 py-1.5 font-medium">
										Scheme
									</th>
									<th className="text-muted-foreground px-3 py-1.5 font-medium">
										Type
									</th>
									<th className="text-muted-foreground px-3 py-1.5 font-medium">
										Description
									</th>
								</tr>
							</thead>
							<tbody className="divide-border/30 divide-y">
								{auth.map((a) => (
									<tr key={a.label}>
										<td className="px-3 py-1.5">
											<code className="text-foreground font-mono">
												{a.label}
											</code>
										</td>
										<td className="text-muted-foreground px-3 py-1.5">
											{a.sub || '—'}
										</td>
										<td className="text-muted-foreground max-w-[200px] px-3 py-1.5">
											<TruncateWithTooltip>
												{a.description || '—'}
											</TruncateWithTooltip>
										</td>
									</tr>
								))}
							</tbody>
						</table>
					</div>
				</section>
			)}

			{footer}
		</div>
	);
}

// ── Utility functions ─────────────────────────────────────────────────────────

export function flattenInspectParameters(
	raw:
		| Record<
				string,
				| Array<{ name?: string; required?: boolean; description?: string }>
				| { required?: boolean; description?: string; media_type?: string }
		  >
		| null
		| undefined,
): InspectParam[] {
	if (!raw || typeof raw !== 'object') return [];
	const out: InspectParam[] = [];
	for (const [loc, value] of Object.entries(raw)) {
		if (Array.isArray(value)) {
			for (const p of value) {
				if (!p?.name) continue;
				out.push({
					name: p.name,
					in: loc,
					required: Boolean(p.required),
					description: p.description ?? '',
				});
			}
		} else if (value && typeof value === 'object') {
			out.push({
				name: 'body',
				in: loc,
				required: Boolean(value.required),
				description: value.media_type
					? `${value.description ?? ''} (${value.media_type})`.trim()
					: (value.description ?? ''),
			});
		}
	}
	return out;
}

export function normalizeWorkspaceAuth(
	raw:
		| Array<{
				scheme?: string;
				type?: string;
				in?: string;
				name?: string;
				instruction?: string;
		  }>
		| null
		| undefined,
): InspectAuthEntry[] {
	if (!Array.isArray(raw)) return [];
	const out: InspectAuthEntry[] = [];
	for (const a of raw) {
		const label = a.scheme || a.type || a.name;
		if (!label) continue;
		const subParts: string[] = [];
		if (a.type && a.type !== label) subParts.push(a.type);
		if (a.in) subParts.push(`in ${a.in}`);
		out.push({
			label,
			sub: subParts.length > 0 ? `(${subParts.join(', ')})` : undefined,
			description: a.instruction,
		});
	}
	return out;
}
