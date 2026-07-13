/**
 * OperationDetail — shared, presentational per-operation drill-down.
 *
 * Renders the method + path, summary/description, a Parameters table, and an
 * Authentication table resolved from a `security_schemes` map. Originally lived
 * in the discover module (ported from jentic-mini's `OperationInspectContent`);
 * promoted to `shared/ui` so both Discover (catalog preview) and Workspace
 * (spec-derived) operation views render operations identically.
 *
 * Neutral by design: it accepts a plain {@link OperationDetailData} shape and a
 * `securitySchemes` record, so callers can project their own API types (the
 * generated catalog preview type, a parsed OpenAPI document, …) onto it without
 * coupling shared to any one module's models.
 */
import type React from 'react';
import { MethodBadge } from '@/shared/ui/Badge';
import { Markdown } from '@/shared/ui/Markdown';
import { TruncateWithTooltip } from '@/shared/ui/TruncateWithTooltip';

/** A single request parameter row in the Parameters table. */
export interface OperationParameter {
	name: string;
	in: string;
	required: boolean;
	description?: string;
}

/** The neutral operation shape the detail view renders. */
export interface OperationDetailData {
	method: string;
	path: string;
	summary?: string;
	description?: string;
	parameters: OperationParameter[];
	/** Security scheme names referenced by this operation. */
	security: string[];
}

/** A `security_schemes`-style map keyed by scheme name. */
export type SecuritySchemeMap = Record<string, Record<string, unknown>>;

interface AuthEntry {
	label: string;
	sub?: string;
	description?: string;
}

function SectionTitle({ children, count }: { children: React.ReactNode; count?: number }) {
	return (
		<h3 className="text-muted-foreground mb-2 flex items-baseline gap-2 text-xs font-medium tracking-wider uppercase">
			{children}
			{count != null && (
				<span className="text-muted-foreground/60 font-mono text-[10px] normal-case">
					{count}
				</span>
			)}
		</h3>
	);
}

/**
 * Resolve `op.security` (scheme names) against the spec's `security_schemes`
 * map into renderable rows. The `type`/`scheme`/`in` fields form a short "(…)"
 * subtitle and the description comes straight off the scheme.
 */
function resolveAuth(security: string[], schemes: SecuritySchemeMap): AuthEntry[] {
	return security.map((name) => {
		const scheme = schemes[name] as
			{ type?: string; scheme?: string; in?: string; description?: string } | undefined;
		const subParts: string[] = [];
		if (scheme?.type) subParts.push(scheme.type);
		if (scheme?.scheme) subParts.push(scheme.scheme);
		if (scheme?.in) subParts.push(`in ${scheme.in}`);
		return {
			label: name,
			sub: subParts.length > 0 ? `(${subParts.join(', ')})` : undefined,
			description: scheme?.description,
		};
	});
}

export interface OperationDetailProps {
	operation: OperationDetailData;
	securitySchemes?: SecuritySchemeMap;
	/**
	 * Render the method + path heading. Defaults to `true` (the standalone
	 * drill-down used by Discover's sheet). Set `false` when the caller already
	 * shows method/path in an enclosing row (e.g. Workspace's inline accordion)
	 * to avoid repeating it.
	 */
	showHeader?: boolean;
}

export function OperationDetail({
	operation,
	securitySchemes,
	showHeader = true,
}: OperationDetailProps) {
	const { method, path, summary, description, parameters, security } = operation;
	const auth = resolveAuth(security ?? [], securitySchemes ?? {});
	const showSummary = summary && (!showHeader || summary !== path);
	const showDescription = description && description !== summary;

	return (
		<div className="min-w-0 space-y-5" data-testid="operation-detail">
			{(showHeader || showSummary || showDescription) && (
				<div className="space-y-2">
					{showHeader && (
						<div className="flex items-start gap-2">
							<MethodBadge method={method} />
							<code className="text-foreground min-w-0 font-mono text-sm break-all">
								{path}
							</code>
						</div>
					)}
					{showSummary && (
						<p className="text-foreground text-sm font-medium">{summary}</p>
					)}
					{showDescription && (
						<Markdown
							source={description as string}
							className="text-muted-foreground text-sm leading-relaxed"
						/>
					)}
				</div>
			)}

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
		</div>
	);
}
