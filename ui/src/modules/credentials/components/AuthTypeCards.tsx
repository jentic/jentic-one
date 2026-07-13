import { motion, type Variants } from 'framer-motion';
import { Check, Key, KeyRound, Shield, Ticket, User } from 'lucide-react';
import { cn } from '@/shared/lib/utils';
import {
	CREDENTIAL_TYPE_DESCRIPTIONS,
	CREDENTIAL_TYPE_LABELS,
	CredentialType,
} from '@/modules/credentials/api';

/**
 * Radio-card picker for the credential type, modelled on jentic-webapp's
 * `AuthTypeSelector`. Replaces the older compact pill bar + segmented toggle
 * combo with a single, scannable grid: icon, title, one-line description, and
 * a selected ring.
 *
 * Two callers:
 *  - **spec-driven** — `options` is the set of types the API's security
 *    schemes declare (often one). When the spec drove a single type we still
 *    render the card so the user sees *what* was detected, but mark it
 *    `detected` for the badge.
 *  - **manual** — `options` is every supported type and the user chooses.
 *
 * Pure presentation; the parent owns selection state.
 */

const ICONS: Record<CredentialType, React.ReactNode> = {
	[CredentialType.BEARER_TOKEN]: <Ticket className="h-5 w-5" />,
	[CredentialType.API_KEY]: <Key className="h-5 w-5" />,
	[CredentialType.BASIC]: <User className="h-5 w-5" />,
	[CredentialType.OAUTH2]: <Shield className="h-5 w-5" />,
};

const GRID_VARIANTS: Variants = {
	hidden: {},
	show: { transition: { staggerChildren: 0.04 } },
};

const CARD_VARIANTS: Variants = {
	hidden: { opacity: 0, y: 8 },
	show: { opacity: 1, y: 0, transition: { duration: 0.18, ease: 'easeOut' } },
};

export interface AuthTypeCardsProps {
	options: CredentialType[];
	value: CredentialType;
	onChange: (type: CredentialType) => void;
	/** When true (spec drove a single type), badge the matching card. */
	detected?: boolean;
}

export function AuthTypeCards({ options, value, onChange, detected = false }: AuthTypeCardsProps) {
	const single = options.length === 1;
	return (
		<fieldset>
			<legend className="text-foreground mb-1 text-sm font-medium">
				{single && detected ? 'Detected authentication method' : 'Authentication method'}
			</legend>
			<p className="text-muted-foreground mb-3 text-xs">
				{single && detected
					? 'Shaped from the API spec — you can still adjust the details below.'
					: 'Choose how this credential authenticates with the API.'}
			</p>
			<motion.div
				className={cn('grid gap-2.5', options.length > 1 && 'sm:grid-cols-2')}
				role="radiogroup"
				aria-label="Authentication method"
				variants={GRID_VARIANTS}
				initial="hidden"
				animate="show"
			>
				{options.map((type) => (
					<AuthTypeCard
						key={type}
						type={type}
						selected={value === type}
						detected={detected && single}
						onSelect={(): void => onChange(type)}
					/>
				))}
			</motion.div>
		</fieldset>
	);
}

function AuthTypeCard({
	type,
	selected,
	detected,
	onSelect,
}: {
	type: CredentialType;
	selected: boolean;
	detected: boolean;
	onSelect: () => void;
}) {
	return (
		<motion.button
			type="button"
			role="radio"
			aria-checked={selected}
			onClick={onSelect}
			variants={CARD_VARIANTS}
			whileHover={{ scale: 1.01 }}
			whileTap={{ scale: 0.99 }}
			className={cn(
				'relative flex w-full items-start gap-3 rounded-xl border p-3.5 text-left transition-colors',
				selected
					? 'border-primary bg-primary/5 shadow-primary/10 shadow-sm'
					: 'border-border hover:border-primary/40 hover:bg-muted/40 cursor-pointer',
			)}
		>
			{detected && (
				<span className="bg-primary text-primary-foreground absolute -top-2 right-3 rounded-full px-2 py-0.5 text-[10px] font-medium">
					Detected
				</span>
			)}
			<span
				className={cn(
					'mt-0.5 shrink-0',
					selected ? 'text-primary' : 'text-muted-foreground',
				)}
				aria-hidden
			>
				{ICONS[type] ?? <KeyRound className="h-5 w-5" />}
			</span>
			<div className="min-w-0 flex-1">
				<p className="text-foreground text-sm font-medium">
					{CREDENTIAL_TYPE_LABELS[type]}
				</p>
				<p className="text-muted-foreground mt-0.5 text-xs leading-snug">
					{CREDENTIAL_TYPE_DESCRIPTIONS[type]}
				</p>
			</div>
			<span
				className={cn(
					'mt-0.5 flex h-4.5 w-4.5 shrink-0 items-center justify-center rounded-full border-2 transition-colors',
					selected ? 'border-primary bg-primary' : 'border-muted-foreground/30',
				)}
				aria-hidden
			>
				{selected && <Check className="text-primary-foreground h-3 w-3" />}
			</span>
		</motion.button>
	);
}
