/**
 * CredentialOptions — which credential an API should use, as radio cards: every
 * credential that covers it, then "Add a new credential". The tray and the setup
 * queue both render it, so the choice reads the same wherever it is made.
 *
 * Sibling credentials often share the API's own name, so each card carries the
 * type, date and id tail that tell them apart, and reuse is never the only
 * option — an API can hold several credentials.
 */
import type { ReactNode } from 'react';
import { Plus } from 'lucide-react';
import { Badge } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import type { Credential } from '@/shared/credentials/api';
import { CredentialTypeBadge } from '@/shared/credentials/components/CredentialTypeBadge';
import { credentialDistinguisher } from '@/shared/credentials/lib/credentialIdentity';
import { credentialAwaitsConsent } from '@/modules/agents/lib/apiTiles';
import type { CredentialChoice } from '@/modules/agents/lib/apiPreflight';

export interface CredentialOptionsProps {
	/** Radio-group name and fieldset id — unique per API on screen. */
	id: string;
	legend: ReactNode;
	/** Visually hide the legend, for a host whose own text already asks the question. */
	legendHidden?: boolean;
	/** The credentials that cover the API, in list order. */
	credentials: Credential[];
	selected: CredentialChoice | null;
	onSelect: (choice: CredentialChoice) => void;
	/** What choosing a new credential leads to, from where it is being chosen. */
	newCredentialDetail: string;
	disabled?: boolean;
	className?: string;
}

export function CredentialOptions({
	id,
	legend,
	legendHidden = false,
	credentials,
	selected,
	onSelect,
	newCredentialDetail,
	disabled = false,
	className,
}: CredentialOptionsProps) {
	return (
		<fieldset id={id} disabled={disabled} className={cn('space-y-1.5', className)}>
			<legend className={cn(legendHidden ? 'sr-only' : 'text-muted-foreground mb-2 text-xs')}>
				{legend}
			</legend>
			{credentials.map((credential) => (
				<OptionCard
					key={credential.credential_id}
					name={id}
					checked={
						selected?.kind === 'existing' &&
						selected.credentialId === credential.credential_id
					}
					onSelect={(): void =>
						onSelect({ kind: 'existing', credentialId: credential.credential_id })
					}
					title={credential.name}
					detail={credentialDistinguisher(credential, { type: false })}
					badges={
						<>
							{credentialAwaitsConsent(credential) && (
								<Badge variant="pending" className="text-[10px]">
									Sign-in needed
								</Badge>
							)}
							<CredentialTypeBadge type={credential.type} />
						</>
					}
				/>
			))}
			<OptionCard
				name={id}
				checked={selected?.kind === 'new'}
				onSelect={(): void => onSelect({ kind: 'new' })}
				title="Add a new credential"
				detail={newCredentialDetail}
				icon={<Plus className="text-primary h-3.5 w-3.5" aria-hidden="true" />}
			/>
		</fieldset>
	);
}

function OptionCard({
	name,
	checked,
	onSelect,
	title,
	detail,
	badges,
	icon,
}: {
	name: string;
	checked: boolean;
	onSelect: () => void;
	title: string;
	detail: string;
	badges?: ReactNode;
	icon?: ReactNode;
}) {
	return (
		<label
			className={cn(
				'border-border/60 hover:border-border bg-card flex cursor-pointer items-center gap-2.5 rounded-lg border px-3 py-2 transition-colors',
				'has-[:disabled]:cursor-not-allowed has-[:disabled]:opacity-60',
				checked && 'border-primary/60 bg-primary/5',
			)}
		>
			<input
				type="radio"
				name={name}
				className="accent-primary h-4 w-4 shrink-0"
				checked={checked}
				onChange={onSelect}
			/>
			<span className="min-w-0 flex-1">
				<span className="text-foreground flex items-center gap-1.5 text-sm">
					{icon}
					<span className="truncate">{title}</span>
				</span>
				<span className="text-muted-foreground block truncate text-[11px]">{detail}</span>
			</span>
			{badges && <span className="flex shrink-0 items-center gap-1.5">{badges}</span>}
		</label>
	);
}
