/**
 * ScopePanel — the authorization block for a single operation, the way
 * GitHub/Google/Stripe surface required permissions.
 *
 * Rendered in our own React tree (the companion panel), so it takes a typed
 * `ReferenceEndpoint` straight from `/reference/endpoints.json` — no Vue→React
 * bridge, no shape-guessing. Returns the public notice when the endpoint needs
 * no auth, otherwise the required scopes, advisory caller, note, and implied
 * scope closure — laid out as a clear, labelled card so the access rules read
 * at a glance.
 */
import { Globe, Lock, ArrowRight } from 'lucide-react';
import type { ReactNode } from 'react';
import type { ReferenceEndpoint } from '@/modules/docs/api/types';
import { cn } from '@/shared/lib/utils';

/** "agent" | "operator" | "any" → human label. Advisory only. */
const TYPICAL_CALLER_LABEL: Record<string, string> = {
	agent: 'Agent / service-account / toolkit',
	operator: 'Human operator / admin',
	any: 'Any authenticated actor',
};

/** Actor-type token → short human label (matches the four OAuth actor schemes). */
const ACTOR_LABEL: Record<string, string> = {
	user: 'User',
	agent: 'Agent',
	service_account: 'Service account',
	toolkit: 'Toolkit',
};

export interface ScopePanelProps {
	endpoint: ReferenceEndpoint;
}

/** A monospace scope token, the visual anchor of the panel. */
function ScopeChip({ scope, tone = 'primary' }: { scope: string; tone?: 'primary' | 'muted' }) {
	return (
		<code
			className={cn(
				'rounded px-1.5 py-0.5 font-mono text-[12px] font-medium',
				tone === 'primary'
					? 'bg-primary/15 text-primary'
					: 'bg-muted/60 text-foreground/70',
			)}
		>
			{scope}
		</code>
	);
}

/** A label / value row, definition-list style. */
function Row({ label, children }: { label: string; children: ReactNode }) {
	return (
		<div className="grid grid-cols-[7rem_minmax(0,1fr)] gap-x-3 gap-y-1 px-3 py-2 sm:grid-cols-[8rem_minmax(0,1fr)]">
			<dt className="text-foreground/60 text-[11px] font-semibold tracking-wider uppercase">
				{label}
			</dt>
			<dd className="min-w-0 text-sm">{children}</dd>
		</div>
	);
}

export function ScopePanel({ endpoint }: ScopePanelProps) {
	if (!endpoint.authenticated) {
		return (
			<section className="jentic-scope-panel border-success/40 bg-success/10 flex items-center gap-2 rounded-lg border px-3 py-2.5 text-sm">
				<Globe className="text-success h-4 w-4 shrink-0" aria-hidden="true" />
				<span className="text-foreground">
					<span className="text-success font-semibold">Public</span> — no authentication
					required.
				</span>
			</section>
		);
	}

	// Defensive reads: the reference is untyped JSON, so a malformed-but-200
	// payload may omit these. Default to empty rather than throwing (which would
	// blank the whole reference) so one bad field degrades to "no scopes".
	const impliedEntries = Object.entries(endpoint.implied_scopes ?? {}).filter(
		([, v]) => Array.isArray(v) && v.length > 0,
	);
	const scopes = endpoint.required_scopes ?? [];
	const actors = endpoint.actor_types ?? [];
	const ALL_ACTORS = ['user', 'agent', 'service_account', 'toolkit'];
	const allActors =
		actors.length >= ALL_ACTORS.length && ALL_ACTORS.every((a) => actors.includes(a));

	return (
		<section className="jentic-scope-panel border-border bg-card/40 overflow-hidden rounded-lg border text-sm">
			{/* Header — states the gate in one line. */}
			<header className="border-border/60 bg-muted/20 flex items-center gap-2 border-b px-3 py-2">
				<Lock className="text-foreground/55 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
				<span className="text-foreground/80 text-[13px] font-semibold">Authorization</span>
				<span className="text-foreground/60 ml-auto text-[11px]">Scopes are the gate</span>
			</header>

			<dl className="divide-border/40 divide-y">
				<Row label="Scopes">
					{scopes.length === 0 ? (
						<span className="text-foreground/65">
							Any authenticated caller — no specific scope required.
						</span>
					) : (
						<span className="flex flex-wrap items-center gap-1.5">
							{scopes.map((scope, i) => (
								<span key={scope} className="flex items-center gap-1.5">
									{i > 0 && (
										<span className="text-foreground/60 text-[11px]">or</span>
									)}
									<ScopeChip scope={scope} />
								</span>
							))}
						</span>
					)}
				</Row>

				{endpoint.typical_caller && (
					<Row label="Typical caller">
						<span className="text-foreground/75">
							{TYPICAL_CALLER_LABEL[endpoint.typical_caller] ??
								endpoint.typical_caller}
						</span>
						<span className="text-foreground/60 ml-1.5 text-[11px]">advisory</span>
					</Row>
				)}

				{actors.length > 0 && (
					<Row label="Actor types">
						{allActors ? (
							<span className="text-foreground/75">
								Any actor type{' '}
								<span className="text-foreground/60 text-[11px]">
									(user, agent, service account, toolkit)
								</span>
							</span>
						) : (
							<span className="flex flex-wrap items-center gap-1.5">
								{actors.map((a) => (
									<code
										key={a}
										className="bg-muted/60 text-foreground/70 rounded px-1.5 py-0.5 font-mono text-[12px]"
									>
										{ACTOR_LABEL[a] ?? a}
									</code>
								))}
							</span>
						)}
					</Row>
				)}

				{endpoint.auth_note && (
					<Row label="Note">
						<span className="text-foreground/70">{endpoint.auth_note}</span>
					</Row>
				)}

				{impliedEntries.length > 0 && (
					<Row label="Implies">
						<ul className="space-y-1.5">
							{impliedEntries.map(([scope, implied]) => (
								<li key={scope} className="flex flex-wrap items-center gap-1.5">
									<ScopeChip scope={scope} />
									<ArrowRight
										className="text-foreground/35 h-3 w-3 shrink-0"
										aria-hidden="true"
									/>
									{implied.map((s) => (
										<ScopeChip key={s} scope={s} tone="muted" />
									))}
								</li>
							))}
						</ul>
					</Row>
				)}
			</dl>
		</section>
	);
}
