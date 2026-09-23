import { Layers } from 'lucide-react';
import { Badge, AgentBadge } from '@/shared/ui';
import { apiRefDisplayName } from '@/shared/lib';
import { formatApiReference, type Credential } from '@/shared/credentials/api';
import { CredentialTypeBadge } from './CredentialTypeBadge';
import {
	CredentialActions,
	CredentialMetaLine,
	credentialApiLine,
	credentialAuthPlacement,
	credentialIsConnected,
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
 *   [vendor badge] [API name] [vendor/name · version] ........ [N credentials]
 *                  [where the secret goes, when every row agrees]
 *   ─ row: [name] [connected] [type] ............ connect · edit · delete
 *          [usage · added · …id tail when the name repeats]
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
	const apiTitle =
		apiRefDisplayName({
			catalogApiId: first.catalog_api_id,
			vendor: first.api.vendor,
			name: first.api.name,
		}) || formatApiReference(first.api);
	const apiLine = credentialApiLine(first, apiTitle);
	const headingId = `credential-group-${first.credential_id}`;

	// One placement line in the header when the rows agree; per row otherwise.
	const placements = new Set(credentials.map(credentialAuthPlacement));
	const sharedPlacement = placements.size === 1 ? [...placements][0] : null;
	const repeatedNames = repeatedCredentialNames(credentials);

	return (
		<section
			data-testid="credential-group"
			aria-labelledby={headingId}
			title={formatApiReference(first.api)}
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
	showIdTail,
	onEdit,
	onDelete,
	onConnect,
	usage,
}: {
	cred: Credential;
	placement: string | null;
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
					<CredentialTypeBadge type={cred.type} />
				</div>
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
