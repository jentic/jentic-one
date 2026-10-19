import { useEffect, useId, useRef, useState } from 'react';
import { motion, type Variants } from 'framer-motion';
import { Ban, Check, Cloud, Key, KeyRound, Shield, Ticket, User } from 'lucide-react';
import { cn } from '@/shared/lib/utils';
import { Button, ExpandReveal } from '@/shared/ui';
import {
	CREDENTIAL_TYPE_DESCRIPTIONS,
	CREDENTIAL_TYPE_LABELS,
	CredentialType,
} from '@/shared/credentials/api';
import { OptionCard } from '@/shared/credentials/components/CredentialOptions';

/**
 * Radio-card picker for the credential type, modelled on jentic-webapp's
 * `AuthTypeSelector`. Replaces the older compact pill bar + segmented toggle
 * combo with a single, scannable grid: icon, title, one-line description, and
 * a selected ring.
 *
 * The full-size picker of the drawer and dialog surfaces; a form unfolded
 * inside another pane uses {@link AuthMethodChooser} below. Two callers:
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
	[CredentialType.NO_AUTH]: <Ban className="h-5 w-5" />,
	[CredentialType.SIGV4]: <Cloud className="h-5 w-5" />,
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

export interface AuthMethodChooserProps {
	options: CredentialType[];
	/** The chosen method; `null` until the operator picks one. */
	value: CredentialType | null;
	onChange: (type: CredentialType) => void;
	/** The spec declares exactly one method: shown as a fact, not a choice. */
	detected?: boolean;
	fromSpec?: boolean;
}

/**
 * Inline method picker: radio rows that fold to a one-line summary with "Change".
 * Arrow keys set the method without folding (so the keyboard can keep moving);
 * click/Space/Enter folds. Focus goes to "Change" on fold, the checked row on reopen.
 */
export function AuthMethodChooser({
	options,
	value,
	onChange,
	detected = false,
	fromSpec = false,
}: AuthMethodChooserProps) {
	if (detected && options.length === 1) {
		return (
			<p className="flex min-w-0 items-center gap-2 text-sm" data-testid="auth-method-row">
				<span className="text-foreground shrink-0 font-medium">Authentication</span>
				<span className="text-foreground-name min-w-0 truncate font-medium">
					{CREDENTIAL_TYPE_LABELS[options[0]]}
				</span>
				<span className="text-foreground-sub min-w-0 truncate text-xs">
					· from the API&apos;s spec
				</span>
			</p>
		);
	}
	return <MethodList options={options} value={value} onChange={onChange} fromSpec={fromSpec} />;
}

function MethodList({
	options,
	value,
	onChange,
	fromSpec,
}: {
	options: readonly CredentialType[];
	value: CredentialType | null;
	onChange: (type: CredentialType) => void;
	fromSpec: boolean;
}) {
	const baseId = useId();
	const [reopened, setReopened] = useState(false);
	// A cleared pick (the form reset) starts the choice over.
	if (value == null && reopened) setReopened(false);
	const open = value == null || reopened;
	const changeRef = useRef<HTMLButtonElement>(null);
	const radios = useRef(new Map<CredentialType, HTMLInputElement>());
	const arrowed = useRef(false);
	/** Focus moves only on the operator's own fold / reopen, never on a reset. */
	const moveFocus = useRef(false);
	useEffect(() => {
		if (!moveFocus.current) return;
		moveFocus.current = false;
		if (!open) {
			changeRef.current?.focus({ preventScroll: true });
			return;
		}
		// Wait one frame for the reveal to un-hide before focusing the row.
		const raf = requestAnimationFrame(() => {
			if (value) radios.current.get(value)?.focus({ preventScroll: true });
		});
		return (): void => cancelAnimationFrame(raf);
	}, [open, value]);

	const fold = (): void => {
		moveFocus.current = true;
		setReopened(false);
	};
	const select = (type: CredentialType): void => {
		const viaArrow = arrowed.current;
		arrowed.current = false;
		if (type !== value) onChange(type);
		// An arrow onto a first pick keeps the list open, as later arrows do.
		if (viaArrow) setReopened(true);
		else fold();
	};
	const hintId = `${baseId}-hint`;
	return (
		<div className="min-w-0" data-testid="auth-method-row">
			{!open && value && (
				<p
					className="flex min-w-0 items-center gap-2 text-sm"
					data-testid="auth-method-chosen"
				>
					<span className="text-foreground shrink-0 font-medium">Authentication</span>
					<span className="text-foreground-name min-w-0 truncate font-medium">
						{CREDENTIAL_TYPE_LABELS[value]}
					</span>
					<Button
						ref={changeRef}
						type="button"
						variant="ghost"
						size="xs"
						className="ml-auto shrink-0"
						aria-label={`Change authentication method (${CREDENTIAL_TYPE_LABELS[value]})`}
						onClick={(): void => {
							moveFocus.current = true;
							setReopened(true);
						}}
					>
						Change
					</Button>
				</p>
			)}
			<ExpandReveal open={open} keepMounted>
				<fieldset
					role="radiogroup"
					aria-label="Authentication method"
					aria-describedby={hintId}
					className="min-w-0 space-y-1.5"
					onKeyDown={(e): void => {
						if (e.key.startsWith('Arrow')) arrowed.current = true;
						else if (e.key === 'Enter' && value) {
							// A pick, not an implicit submit of the form around it.
							e.preventDefault();
							fold();
						} else if (e.key === 'Escape' && value) {
							// The pane's overlay watches Escape too: this press folds only.
							e.stopPropagation();
							fold();
						}
					}}
				>
					<legend className="text-foreground mb-0.5 text-sm font-medium">
						Authentication
					</legend>
					<p id={hintId} className="text-muted-foreground pb-1 text-xs">
						{fromSpec
							? `The API's spec declares ${options.length} methods — choose the one you have.`
							: 'Choose how this credential signs in to the API.'}
					</p>
					{options.map((type) => (
						<OptionCard
							key={type}
							name={`${baseId}-method`}
							checked={value === type}
							onSelect={(): void => select(type)}
							title={CREDENTIAL_TYPE_LABELS[type]}
							detail={CREDENTIAL_TYPE_DESCRIPTIONS[type]}
							inputRef={(el): void => {
								if (el) radios.current.set(type, el);
								else radios.current.delete(type);
							}}
						/>
					))}
				</fieldset>
			</ExpandReveal>
		</div>
	);
}
