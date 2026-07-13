import { Link2, RefreshCw, Settings, Trash2 } from 'lucide-react';
import { AgentBadge, Badge, Button } from '@/shared/ui';
import { CredentialTypeBadge } from './CredentialTypeBadge';
import {
	CredentialType,
	credentialDetails,
	formatApiReference,
	type Credential,
} from '@/modules/credentials/api';
import { isManagedProvider } from '@/modules/credentials/config';

interface CredentialCardProps {
	cred: Credential;
	onEdit: (cred: Credential) => void;
	onDelete: (cred: Credential) => void;
	onConnect: (cred: Credential) => void;
}

/**
 * One credential card in the credentials grid.
 *
 * Anatomy mirrors the rest of jentic-one's resource cards:
 *
 *   [vendor badge] [name + api ref] ............... [type badge]
 *   [provider · injection hint]
 *   [meta row (added/updated) ......... connect · edit · delete]
 *
 * The whole card is a click target that opens the edit sheet (a full-card
 * `<button>` sits behind the content). The explicit action buttons
 * (connect / edit / delete) sit *above* that overlay and `stopPropagation`
 * so each control stays independently clickable and focusable without
 * nesting interactive elements inside the overlay button.
 */
export function CredentialCard({ cred, onEdit, onDelete, onConnect }: CredentialCardProps) {
	const details = credentialDetails(cred);
	const isOAuth = cred.type === CredentialType.OAUTH2;
	const managed = isManagedProvider(cred.provider);
	const connected = isOAuth && !!cred.provider_account_ref;
	const vendor = cred.api.vendor ?? cred.name;

	const subtitle =
		cred.type === CredentialType.API_KEY && details.field_name
			? `${details.field_name} in ${details.location ?? 'header'}`
			: managed
				? 'Managed via Pipedream'
				: cred.provider;

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

			<div className="pointer-events-none relative flex items-center gap-3">
				<AgentBadge id={vendor} name={vendor} kind="API" size="lg" className="rounded-xl" />
				<div className="min-w-0 flex-1">
					<div className="flex items-center gap-2">
						<h3 className="font-heading text-foreground min-w-0 flex-1 truncate text-sm font-semibold">
							{cred.name}
						</h3>
						{connected && (
							<Badge variant="success" className="shrink-0">
								Connected
							</Badge>
						)}
						<CredentialTypeBadge type={cred.type} />
					</div>
					<p className="text-muted-foreground mt-0.5 truncate font-mono text-xs">
						{formatApiReference(cred.api)}
					</p>
				</div>
			</div>

			<p className="text-muted-foreground pointer-events-none relative line-clamp-2 min-h-[2lh] text-xs leading-snug break-words">
				{subtitle}
			</p>

			<div className="border-border/50 relative mt-auto flex items-center gap-2 border-t pt-3">
				<div className="text-muted-foreground pointer-events-none flex min-w-0 flex-1 flex-col gap-0.5 text-[11px]">
					<span className="truncate">
						{!cred.active && (
							<span className="text-warning mr-1.5 font-medium">Inactive ·</span>
						)}
						added {formatDate(cred.created_at)}
					</span>
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
