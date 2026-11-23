/**
 * CredentialOptions — which credential an API should use, as radio cards: every
 * credential that covers it, then "Add a new credential". The Add APIs setup queue
 * renders it for each API it works through.
 *
 * Sibling credentials often share the API's own name, so each card carries the
 * type, date and id tail that tell them apart, and reuse is never the only
 * option — an API can hold several credentials.
 */
import type { ReactNode, Ref } from 'react';
import { Plus } from 'lucide-react';
import { Badge, ExpandReveal } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import type { Credential } from '@/shared/credentials/api';
import { CredentialTypeBadge } from '@/shared/credentials/components/CredentialTypeBadge';
import {
	credentialAwaitsConsent,
	credentialDistinguisher,
	type CredentialChoice,
} from '@/shared/credentials/lib/credentialIdentity';

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
	/** The credential the agent already uses for this API, marked on its card. */
	linkedCredentialId?: string | null;
	disabled?: boolean;
	className?: string;
	/** Fold the existing cards behind a one-line `summary` while a create form is open. */
	collapseExisting?: { collapsed: boolean; summary: ReactNode };
}

export function CredentialOptions({
	id,
	legend,
	legendHidden = false,
	credentials,
	selected,
	onSelect,
	newCredentialDetail,
	linkedCredentialId,
	disabled = false,
	className,
	collapseExisting,
}: CredentialOptionsProps) {
	const cards = credentials.map((credential) => (
		<OptionCard
			key={credential.credential_id}
			name={id}
			checked={
				selected?.kind === 'existing' && selected.credentialId === credential.credential_id
			}
			onSelect={(): void =>
				onSelect({ kind: 'existing', credentialId: credential.credential_id })
			}
			title={credential.name}
			detail={credentialDistinguisher(credential, { type: false })}
			badges={
				<>
					{credential.credential_id === linkedCredentialId && (
						<Badge variant="success">Already linked</Badge>
					)}
					{credentialAwaitsConsent(credential) && (
						<Badge variant="pending">Sign-in needed</Badge>
					)}
					<CredentialTypeBadge type={credential.type} />
				</>
			}
		/>
	));
	return (
		// `min-w-0`: a fieldset is min-content wide by default and would overflow a narrow pane.
		<fieldset id={id} disabled={disabled} className={cn('min-w-0 space-y-1.5', className)}>
			<legend className={cn(legendHidden ? 'sr-only' : 'text-muted-foreground mb-2 text-xs')}>
				{legend}
			</legend>
			{collapseExisting && credentials.length > 0 ? (
				<>
					<ExpandReveal open={!collapseExisting.collapsed}>
						<div className="space-y-1.5 pb-0.5">{cards}</div>
					</ExpandReveal>
					{collapseExisting.collapsed && collapseExisting.summary}
				</>
			) : (
				cards
			)}
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

/** One radio row of a pane-level choice; shared with the inline method chooser. */
export function OptionCard({
	name,
	checked,
	onSelect,
	title,
	detail,
	badges,
	icon,
	inputRef,
	testId,
}: {
	name: string;
	checked: boolean;
	onSelect: () => void;
	title: string;
	detail: string;
	badges?: ReactNode;
	icon?: ReactNode;
	inputRef?: Ref<HTMLInputElement>;
	testId?: string;
}) {
	return (
		<label
			className={cn(
				'bg-field hover:bg-tint-2 flex cursor-pointer items-center gap-2.5 rounded-lg px-3 py-2 transition-[background-color,box-shadow] duration-200 ease-out motion-reduce:transition-none',
				// Inset: an outer ring is clipped by a folding or scrolling ancestor.
				'has-[:focus-visible]:ring-ring has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-inset',
				'has-[:disabled]:cursor-not-allowed has-[:disabled]:opacity-60',
				checked &&
					'bg-surface-selected shadow-[inset_0_0_0_1.5px_hsl(var(--primary)/0.45)]',
			)}
		>
			<input
				ref={inputRef}
				type="radio"
				name={name}
				data-testid={testId}
				className="accent-primary h-4 w-4 shrink-0"
				checked={checked}
				onChange={onSelect}
				// A checked radio fires no change; the click still confirms (re-folds) it.
				onClick={checked ? onSelect : undefined}
			/>
			<span className="min-w-0 flex-1">
				<span className="text-foreground-name flex items-center gap-1.5 text-sm font-semibold">
					{icon}
					<span className="truncate">{title}</span>
				</span>
				<span className="text-foreground-sub block truncate text-[11px]">{detail}</span>
			</span>
			{badges && <span className="flex shrink-0 items-center gap-1.5">{badges}</span>}
		</label>
	);
}
