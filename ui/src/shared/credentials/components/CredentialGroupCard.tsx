import { Layers } from 'lucide-react';
import { Badge, AgentBadge } from '@/shared/ui';
import { apiRefDisplayName, formatApiVersion } from '@/shared/lib';
import { formatApiReference, type Credential } from '@/shared/credentials/api';
import { CredentialTypeBadge } from './CredentialTypeBadge';
import {
	CredentialActions,
	CredentialMetaLine,
	credentialApiLine,
	credentialAuthPlacement,
	credentialIsConnected,
	credentialIsPendingSignIn,
} from './CredentialCard';

type Usage = { usedByAgentCount?: number | null; callsLast7d?: number | null };

interface CredentialGroupCardProps {
	/** Two or more credentials for one API, in list order. */
	credentials: Credential[];
	onEdit: (cred: Credential) => void;
	onDelete: (cred: Credential) => void;
	onConnect: (cred: Credential) => void;
	usageFor: (cred: Credential) => Usage;
}

/**
 * Every credential one API holds, as one card: the API is said once in the header
 * and each credential is a row under it, so three keys for `airlabs.co` read as
 * "one API, three credentials" instead of three identical cards.
 *
 *   [vendor badge] [API name] [vendor/name] .................. [N credentials]
 *                  [where the secret goes, when every row agrees]
 *   ─ row: [name] [connected] [type] ............ connect · edit · delete
 *          [pinned version, or "any version"]
 *          [usage · added · …id tail when the name repeats]
 *
 * The header carries only what every row shares; the version is per row, since
 * rows of one API may pin different revisions. Rows that don't share an API get
 * a neutral vendor + "N APIs" header and each names its own API.
 *
 * A row whose name another row shares carries its id tail — then it is the only
 * thing telling the two apart; a unique name needs nothing more. Each row is a
 * click target for edit, the way a single card is.
 */
export function CredentialGroupCard({
	credentials,
	onEdit,
	onDelete,
	onConnect,
	usageFor,
}: CredentialGroupCardProps) {
	const [first] = credentials;
	const vendor = first.api.vendor ?? first.name;
	// The header names only what every row shares. The version never goes up
	// there — each row states its own pin — and if the rows don't agree on the
	// API itself, the header falls back to the vendor and a count of APIs rather
	// than borrowing the first row's identity.
	const distinctApis = new Set(credentials.map(apiIdentityKey)).size;
	const sameApi = distinctApis === 1;
	const apiTitle = sameApi
		? apiRefDisplayName({
				catalogApiId: first.catalog_api_id,
				vendor: first.api.vendor,
				name: first.api.name,
			}) || formatApiReference(unversioned(first.api))
		: vendor;
	const apiLine = sameApi
		? credentialApiLine({ ...first, api: unversioned(first.api) }, apiTitle)
		: `${distinctApis} APIs`;
	const headingId = `credential-group-${first.credential_id}`;

	// One placement line in the header when the rows agree; per row otherwise.
	const placements = new Set(credentials.map(credentialAuthPlacement));
	const sharedPlacement = placements.size === 1 ? [...placements][0] : null;
	const repeatedNames = repeatedCredentialNames(credentials);

	return (
		<section
			data-testid="credential-group"
			aria-labelledby={headingId}
			title={sameApi ? formatApiReference(unversioned(first.api)) : vendor}
			className="border-border/60 bg-card min-w-0 overflow-hidden rounded-xl border"
		>
			<header className="flex items-start gap-3 p-4 pb-3">
				<AgentBadge id={vendor} name={vendor} kind="API" size="lg" className="rounded-xl" />
				<div className="min-w-0 flex-1">
					<div className="flex items-start gap-2">
						<h3
							id={headingId}
							className="font-heading text-foreground min-w-0 flex-1 text-sm leading-snug font-semibold break-words"
						>
							{apiTitle}
						</h3>
						<span
							className="text-muted-foreground inline-flex shrink-0 items-center gap-1 pt-0.5 text-xs"
							data-testid="credential-group-count"
						>
							<Layers className="h-3.5 w-3.5" aria-hidden="true" />
							{credentials.length} credentials
						</span>
					</div>
					{apiLine && (
						<p className="text-muted-foreground mt-0.5 truncate text-xs">{apiLine}</p>
					)}
					<p className="text-muted-foreground mt-1.5 text-xs leading-snug">
						{sharedPlacement ? `${sharedPlacement}. ` : ''}
						Any of these can be bound to an agent — you choose which when you add the
						API.
						{repeatedNames.size > 0 &&
							' Some share a name; rename one to tell them apart.'}
					</p>
				</div>
			</header>

			<ul className="divide-border/50 border-border/50 divide-y border-t">
				{credentials.map((cred) => (
					<CredentialRow
						key={cred.credential_id}
						cred={cred}
						placement={sharedPlacement ? null : credentialAuthPlacement(cred)}
						apiLabel={sameApi ? null : formatApiReference(unversioned(cred.api))}
						showIdTail={repeatedNames.has(normalizedName(cred))}
						onEdit={onEdit}
						onDelete={onDelete}
						onConnect={onConnect}
						usage={usageFor(cred)}
					/>
				))}
			</ul>
		</section>
	);
}

function normalizedName(cred: Credential): string {
	return cred.name.trim().toLowerCase();
}

/** The API reference with its version blanked — the formatters read an empty
 * version as "none", so the header never prints one row's pin. */
function unversioned(api: Credential['api']): Credential['api'] {
	return { ...api, version: '' };
}

/** The API a row serves, version aside — rows agreeing on this share a header. */
function apiIdentityKey(cred: Credential): string {
	return [
		cred.api.vendor.trim().toLowerCase(),
		(cred.api.name ?? '').trim().toLowerCase(),
		cred.catalog_api_id?.trim().toLowerCase() ?? '',
	].join('|');
}

/** Names two or more rows share, case-insensitively — the rows only an id tells apart. */
function repeatedCredentialNames(credentials: readonly Credential[]): Set<string> {
	const seen = new Set<string>();
	const repeated = new Set<string>();
	for (const cred of credentials) {
		const name = normalizedName(cred);
		if (seen.has(name)) repeated.add(name);
		seen.add(name);
	}
	return repeated;
}

function CredentialRow({
	cred,
	placement,
	apiLabel,
	showIdTail,
	onEdit,
	onDelete,
	onConnect,
	usage,
}: {
	cred: Credential;
	placement: string | null;
	/** The row's own API, set only when the group's rows don't share one. */
	apiLabel: string | null;
	showIdTail: boolean;
	onEdit: (cred: Credential) => void;
	onDelete: (cred: Credential) => void;
	onConnect: (cred: Credential) => void;
	usage: Usage;
}) {
	return (
		<li
			data-testid="credential-card"
			className="hover:bg-muted/30 focus-within:bg-muted/30 relative flex items-center gap-3 px-4 py-2.5 transition-colors"
		>
			{/* Full-row click target → edit, hidden from the a11y tree so keyboard and
			    screen-reader users get the one labelled "Edit" button instead. */}
			<button
				type="button"
				tabIndex={-1}
				aria-hidden="true"
				data-testid="credential-card-overlay"
				onClick={(): void => onEdit(cred)}
				className="absolute inset-0 z-0 focus:outline-none"
			/>
			<div className="pointer-events-none relative min-w-0 flex-1">
				<div className="flex flex-wrap items-center gap-x-2 gap-y-1">
					<h4 className="text-foreground min-w-0 text-sm font-medium break-words">
						{cred.name || formatApiReference(cred.api)}
					</h4>
					{credentialIsConnected(cred) && <Badge variant="success">Connected</Badge>}
					{credentialIsPendingSignIn(cred) && (
						<Badge variant="pending">Pending sign-in</Badge>
					)}
					<CredentialTypeBadge credential={cred} />
				</div>
				<p
					className="text-muted-foreground mt-0.5 truncate font-mono text-xs"
					data-testid="credential-row-api"
				>
					{[apiLabel, formatApiVersion(cred.api.version) ?? 'any version']
						.filter(Boolean)
						.join(' · ')}
				</p>
				{placement && (
					<p className="text-muted-foreground mt-0.5 truncate text-xs">{placement}</p>
				)}
				<CredentialMetaLine
					cred={cred}
					usedByAgentCount={usage.usedByAgentCount}
					callsLast7d={usage.callsLast7d}
					showIdTail={showIdTail}
					className="mt-0.5"
				/>
			</div>
			<CredentialActions
				cred={cred}
				onEdit={onEdit}
				onDelete={onDelete}
				onConnect={onConnect}
			/>
		</li>
	);
}
