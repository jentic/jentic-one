import type { ReactNode } from 'react';
import { Link2, RefreshCw, Settings, Trash2 } from 'lucide-react';
import { AgentBadge, Badge, Button, Skeleton } from '@/shared/ui';
import { apiRefDisplayName, formatApiVersion } from '@/shared/lib';
import { CredentialTypeBadge } from './CredentialTypeBadge';
import {
	CredentialType,
	credentialDetails,
	formatApiReference,
	type Credential,
	type CredentialDetails,
} from '@/shared/credentials/api';
import { isManagedProvider } from '@/shared/credentials/config';

interface CredentialCardProps {
	cred: Credential;
	onEdit: (cred: Credential) => void;
	onDelete: (cred: Credential) => void;
	onConnect: (cred: Credential) => void;
	/** How many agents hold this credential: `undefined` still resolving
	 * (skeleton), `null` unprovable (the clause is omitted), a number exact. */
	usedByAgentCount?: number | null;
	/** Calls brokered with this credential over the last 7 days — same contract. */
	callsLast7d?: number | null;
}

/**
 * One credential card in the credentials grid.
 *
 * Anatomy mirrors the rest of jentic-one's resource cards:
 *
 *   [vendor badge] [name + api name · version] ......... [type badge]
 *   [where the secret is injected, in plain language]
 *   [meta row (usage · added) ......... connect · edit · delete]
 *
 * The whole card is a click target that opens the edit sheet (a full-card
 * `<button>` sits behind the content). The explicit action buttons
 * (connect / edit / delete) sit *above* that overlay and `stopPropagation`
 * so each control stays independently clickable and focusable without
 * nesting interactive elements inside the overlay button.
 */
export function CredentialCard({
	cred,
	onEdit,
	onDelete,
	onConnect,
	usedByAgentCount,
	callsLast7d,
}: CredentialCardProps) {
	const details = credentialDetails(cred);
	const isOAuth = cred.type === CredentialType.OAUTH2;
	const managed = isManagedProvider(cred.provider);
	const connected = isOAuth && !!cred.provider_account_ref;
	const vendor = cred.api.vendor ?? cred.name;
	// Heading = the user's own `cred.name` when they've set one, so renaming a
	// credential updates the card's title (matching the edit sheet's intent).
	// Fall back to the friendly API name — the persisted catalog slug when
	// recorded (`Article Search`), else the humanised tuple — then the raw
	// tuple — so the card never leads with a blank line. We never render the
	// derived API name as a *separate* line: the card shows just the name
	// (user's or derived) plus the copyable mono tuple beneath.
	const title =
		cred.name ||
		apiRefDisplayName({
			catalogApiId: cred.catalog_api_id,
			vendor: cred.api.vendor,
			name: cred.api.name,
		}) ||
		formatApiReference(cred.api);

	// Machine identity, kept as a hover/AT string rather than a rendered line.
	const tuple = formatApiReference(cred.api);
	// Line 2 is which API the secret unlocks, in the `host · version` grammar the
	// tiles use. The vendor prints as stored because it IS a domain.
	const apiName = cred.api.name && cred.api.name !== 'default' ? cred.api.name : null;
	const apiPath = [cred.api.vendor, apiName].filter(Boolean).join('/');
	const version = formatApiVersion(cred.api.version);
	// When the heading IS that identity (a credential with no name of its own,
	// headed by its derived API name) only the version is left to add.
	const apiLine =
		apiPath && apiPath !== title ? [apiPath, version].filter(Boolean).join(' · ') : version;

	const subtitle = authPlacement(cred, details, managed);

	// Clauses, so a withheld figure removes itself instead of leaving a dangling
	// separator. Usage leads: who holds a secret decides whether removing it is safe.
	const meta: { key: string; node: ReactNode }[] = [];
	if (!cred.active)
		meta.push({
			key: 'inactive',
			node: <span className="text-warning font-medium">Inactive</span>,
		});
	if (usedByAgentCount === undefined)
		meta.push({ key: 'used-by', node: <Skeleton className="h-3 w-24" /> });
	else if (usedByAgentCount !== null)
		meta.push({
			key: 'used-by',
			node: (
				<span data-testid="cred-used-by">
					{usedByAgentCount === 0
						? 'used by no agents'
						: `used by ${usedByAgentCount} agent${usedByAgentCount === 1 ? '' : 's'}`}
				</span>
			),
		});
	if (callsLast7d === undefined)
		meta.push({ key: 'calls', node: <Skeleton className="h-3 w-16" /> });
	else if (callsLast7d !== null)
		meta.push({
			key: 'calls',
			node: (
				<span data-testid="cred-calls-7d">
					{callsLast7d === 0
						? 'no calls in 7d'
						: `${callsLast7d.toLocaleString()} call${callsLast7d === 1 ? '' : 's'} in 7d`}
				</span>
			),
		});
	meta.push({ key: 'added', node: <span>added {formatDate(cred.created_at)}</span> });

	/** Run an action button's handler without triggering the card-edit click. */
	const stop =
		(fn: () => void) =>
		(e: React.MouseEvent): void => {
			e.stopPropagation();
			fn();
		};

	return (
		<div
			data-testid="credential-card"
			title={tuple}
			className="group border-border/60 bg-card hover:border-border focus-within:border-primary/50 relative flex h-full min-w-0 flex-col gap-3 overflow-hidden rounded-xl border p-4 text-left transition-all hover:shadow-sm"
		>
			{/* Full-card click target → edit, for pointer users. Hidden from the
			    a11y tree (aria-hidden + tabIndex=-1) so screen-reader/keyboard
			    users get a single, clearly-labelled "Edit" control (the explicit
			    button below) instead of two competing "edit" affordances. */}
			<button
				type="button"
				tabIndex={-1}
				aria-hidden="true"
				data-testid="credential-card-overlay"
				onClick={(): void => onEdit(cred)}
				className="absolute inset-0 z-0 rounded-xl focus:outline-none"
			/>

			<div className="pointer-events-none relative flex items-start gap-3">
				<AgentBadge id={vendor} name={vendor} kind="API" size="lg" className="rounded-xl" />
				<div className="min-w-0 flex-1">
					<div className="flex items-start gap-2">
						{/* Wraps rather than truncates: the tail of a name ("… staging" vs
						    "… prod") is often the only thing telling two cards apart. */}
						<h3 className="font-heading text-foreground min-w-0 flex-1 text-sm leading-snug font-semibold break-words">
							{title}
						</h3>
						{connected && (
							<Badge variant="success" className="shrink-0">
								Connected
							</Badge>
						)}
						<CredentialTypeBadge type={cred.type} />
					</div>
					{apiLine && (
						<p className="text-muted-foreground mt-0.5 truncate text-xs">{apiLine}</p>
					)}
				</div>
			</div>

			<p className="text-muted-foreground pointer-events-none relative line-clamp-2 min-h-[2lh] text-xs leading-snug break-words">
				{subtitle}
			</p>

			<div className="border-border/50 relative mt-auto flex items-center gap-2 border-t pt-3">
				<div className="text-muted-foreground pointer-events-none flex min-w-0 flex-1 flex-wrap items-center gap-x-1.5 gap-y-0.5 text-[11px]">
					{meta.map((clause, i) => (
						<span key={clause.key} className="inline-flex items-center gap-1.5">
							{i > 0 && <span aria-hidden="true">·</span>}
							{clause.node}
						</span>
					))}
				</div>

				<div className="relative z-10 flex shrink-0 items-center gap-1">
					{isOAuth && (
						<Button
							variant={connected ? 'secondary' : 'primary'}
							size="sm"
							onClick={stop((): void => onConnect(cred))}
							aria-label={`${connected ? 'Reconnect' : 'Connect'} ${cred.name}`}
							title={
								managed
									? 'Connect via Pipedream'
									: connected
										? 'Reconnect via OAuth'
										: 'Connect via OAuth'
							}
						>
							{managed ? (
								<Link2 className="h-4 w-4" />
							) : (
								<RefreshCw className="h-4 w-4" />
							)}
						</Button>
					)}
					<Button
						variant="secondary"
						size="sm"
						onClick={stop((): void => onEdit(cred))}
						aria-label={`Edit credential ${cred.name}`}
					>
						<Settings className="h-4 w-4" />
					</Button>
					<Button
						variant="danger"
						size="sm"
						onClick={stop((): void => onDelete(cred))}
						aria-label={`Delete credential ${cred.name}`}
					>
						<Trash2 className="h-4 w-4" />
					</Button>
				</div>
			</div>
		</div>
	);
}

/** Where the secret is injected, said the way an operator would say it — the
 * stored `provider` (`static`) answers nothing an upstream 401 is diagnosed
 * against. */
function authPlacement(cred: Credential, details: CredentialDetails, managed: boolean): string {
	if (managed) return 'Managed via Pipedream';
	switch (cred.type) {
		case CredentialType.API_KEY: {
			const where = details.location === 'query' ? 'query parameter' : 'header';
			return details.field_name
				? `API key in the ${details.field_name} ${where}`
				: `API key in a request ${where}`;
		}
		case CredentialType.BEARER_TOKEN:
			return 'Bearer token in the Authorization header';
		case CredentialType.BASIC:
			return 'Username and password, sent as Basic auth';
		case CredentialType.OAUTH2:
			return cred.provider_account_ref
				? 'OAuth 2.0 — access tokens refreshed automatically'
				: 'OAuth 2.0 — needs a sign-in before it can be used';
		case CredentialType.NO_AUTH:
			return 'No credential — the API is called unauthenticated';
		case CredentialType.SIGV4: {
			const region = details.aws_region ? ` (${details.aws_region})` : '';
			return `AWS SigV4 request signing${region}`;
		}
		default:
			// An unknown type is a backend the UI hasn't caught up with; name the
			// provider rather than invent a placement it might not have.
			return cred.provider;
	}
}

function formatDate(value: string | null | undefined): string {
	if (!value) return 'recently';
	const d = new Date(value);
	if (Number.isNaN(d.getTime())) return 'recently';
	return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

/** Card-shaped skeleton matching `CredentialCard`'s layout. */
export function CredentialCardSkeleton() {
	return (
		<div className="border-border/60 bg-card flex h-full min-w-0 flex-col gap-3 rounded-xl border p-4">
			<div className="flex items-center gap-3">
				<div className="bg-muted h-11 w-11 shrink-0 animate-pulse rounded-xl" />
				<div className="min-w-0 flex-1 space-y-2">
					<div className="bg-muted h-4 w-2/3 animate-pulse rounded" />
					<div className="bg-muted h-3 w-1/2 animate-pulse rounded" />
				</div>
			</div>
			<div className="space-y-1.5">
				<div className="bg-muted h-3 w-full animate-pulse rounded" />
				<div className="bg-muted h-3 w-3/5 animate-pulse rounded" />
			</div>
			<div className="border-border/50 mt-auto flex items-center gap-3 border-t pt-3">
				<div className="bg-muted h-3 w-24 animate-pulse rounded" />
				<div className="ml-auto flex gap-1">
					<div className="bg-muted h-7 w-7 animate-pulse rounded-md" />
					<div className="bg-muted h-7 w-7 animate-pulse rounded-md" />
				</div>
			</div>
		</div>
	);
}
