/**
 * BindingDurationField — how long access lasts (credential grant expiry, D8):
 * a dropdown of preset durations (1 hour to 90 days) plus "Custom…", which
 * opens a whole-number-and-unit picker that also holds "No expiry". Controlled;
 * the caller keeps the {@link BindingDuration} (or `null` while nothing is
 * chosen — there is no default) and works out the actual `expires_at` when it
 * saves ({@link expiresAtFromDuration}), so the time runs from the save, not
 * from when this field rendered.
 *
 * Under the control it says roughly when access would end if saved now (in
 * the caller's words, via `describe`), or what is wrong with a custom amount.
 *
 * Where the choice is optional (approving an agent), `clearable` makes the
 * empty choice selectable under `placeholder`, so the operator can go back to
 * it. `compact` fits the control into a row of buttons: the label becomes the
 * group's accessible name and the line under it is read to screen readers only.
 * `emptyHint` is the line under the control while nothing is chosen, and
 * `selectRef` lets a caller move focus to the dropdown (the "No expiry"
 * warning's "Set an expiry").
 *
 * Under an org maximum (`maxSeconds`), presets above it are disabled, "No
 * expiry" is not offered, and a longer custom amount says so.
 */
import { useId, type Ref } from 'react';
import { Input, Select } from '@/shared/ui';
import {
	BINDING_DURATION_PRESETS,
	CUSTOM_DURATION_UNITS,
	INITIAL_CUSTOM_DURATION,
	MAX_BINDING_DURATION_AMOUNT,
	bindingDurationError,
	expiresAtFromDuration,
	type BindingDuration,
	type BindingDurationPreset,
	type CustomDurationUnit,
} from '@/shared/credentials/lib/bindingDuration';

export interface BindingDurationFieldProps {
	/** The choice; `null` while nothing is chosen. */
	value: BindingDuration | null;
	onChange: (value: BindingDuration | null) => void;
	disabled?: boolean;
	/** The control's label: visible, or its accessible name when `compact`. */
	label?: string;
	/** What the dropdown shows while nothing is chosen. */
	placeholder?: string;
	/** The empty choice can be picked again (an optional duration). */
	clearable?: boolean;
	/** Fit into a row of buttons: no visible label, the description for screen readers only. */
	compact?: boolean;
	/** The line under the control for a valid choice: when access would end if saved now. */
	describe?: (end: Date | 'never') => string;
	/** The line under the control while nothing is chosen. */
	emptyHint?: string;
	/**
	 * Id of a line elsewhere that describes when access ends (a grant's limits
	 * share one, under "End after one use"): this field then shows only its
	 * own errors.
	 */
	describedBy?: string;
	/** The preset dropdown, for a caller that moves focus to it. */
	selectRef?: Ref<HTMLSelectElement>;
	/** Prefix for the field's test ids (`<prefix>-duration-*`). */
	testIdPrefix?: string;
	/** The org's maximum, in seconds; null when it sets none. */
	maxSeconds?: number | null;
}

const EXPIRY_FORMAT = new Intl.DateTimeFormat(undefined, {
	dateStyle: 'medium',
	timeStyle: 'short',
});

/** Formats an end date the way this field does, for callers' `describe`. */
export function formatDurationEnd(end: Date): string {
	return EXPIRY_FORMAT.format(end);
}

function defaultDescribe(end: Date | 'never'): string {
	return end === 'never'
		? 'No expiry: access lasts until someone removes it.'
		: `If saved now, access ends around ${formatDurationEnd(end)}.`;
}

const CUSTOM = 'custom';
const NONE = '';

export function BindingDurationField({
	value,
	onChange,
	disabled = false,
	label = 'Expires after',
	placeholder = 'Choose…',
	clearable = false,
	compact = false,
	describe = defaultDescribe,
	emptyHint,
	describedBy,
	selectRef,
	testIdPrefix = 'binding',
	maxSeconds = null,
}: BindingDurationFieldProps) {
	const labelId = useId();
	const hintId = useId();
	const error = bindingDurationError(value, maxSeconds);
	const describes = error || !describedBy ? hintId : describedBy;
	const end = error ? null : expiresAtFromDuration(value);
	const custom = value?.kind === 'custom' ? value : null;
	const selected = value === null ? NONE : value.kind === 'preset' ? value.preset : CUSTOM;

	const onSelect = (next: string): void => {
		if (next === CUSTOM) {
			if (!custom) onChange(INITIAL_CUSTOM_DURATION);
			return;
		}
		if (next === NONE) {
			if (clearable) onChange(null);
			return;
		}
		onChange({ kind: 'preset', preset: next as BindingDurationPreset });
	};

	return (
		<div
			role="group"
			aria-labelledby={compact ? undefined : labelId}
			aria-label={compact ? label : undefined}
			className={compact ? 'space-y-1' : 'space-y-1.5'}
			data-testid={`${testIdPrefix}-duration`}
		>
			{!compact && (
				<p id={labelId} className="text-foreground text-sm font-medium">
					{label}
				</p>
			)}
			<div className="flex flex-wrap items-start gap-2">
				<div className={compact ? 'w-32 shrink-0' : 'w-40 shrink-0'}>
					<Select
						ref={selectRef}
						aria-label="Duration"
						aria-describedby={describes}
						value={selected}
						disabled={disabled}
						className="py-1.5"
						data-testid={`${testIdPrefix}-duration-preset`}
						onChange={(e): void => onSelect(e.target.value)}
					>
						{(value === null || clearable) && (
							<option value={NONE} disabled={!clearable}>
								{placeholder}
							</option>
						)}
						{BINDING_DURATION_PRESETS.map((p) => (
							<option
								key={p.value}
								value={p.value}
								disabled={maxSeconds !== null && p.ms / 1000 > maxSeconds}
							>
								{p.label}
							</option>
						))}
						<option value={CUSTOM}>Custom…</option>
					</Select>
				</div>
				{custom && (
					<>
						{custom.unit !== 'never' && (
							<div className={compact ? 'w-20 shrink-0' : 'w-24 shrink-0'}>
								<Input
									type="number"
									inputMode="numeric"
									min={1}
									max={MAX_BINDING_DURATION_AMOUNT}
									step={1}
									size="sm"
									aria-label="Amount"
									aria-invalid={error ? true : undefined}
									aria-describedby={describes}
									value={custom.amount}
									disabled={disabled}
									data-testid={`${testIdPrefix}-duration-amount`}
									onChange={(e): void =>
										onChange({ ...custom, amount: e.target.value })
									}
								/>
							</div>
						)}
						<div className={compact ? 'w-28 shrink-0' : 'w-36 shrink-0'}>
							<Select
								aria-label="Unit"
								aria-describedby={describes}
								value={custom.unit}
								disabled={disabled}
								className="py-1.5"
								data-testid={`${testIdPrefix}-duration-unit`}
								onChange={(e): void =>
									onChange({
										...custom,
										unit: e.target.value as CustomDurationUnit,
									})
								}
							>
								{CUSTOM_DURATION_UNITS.filter(
									// Under an org maximum every grant must end.
									(u) => maxSeconds === null || u.value !== 'never',
								).map((u) => (
									<option key={u.value} value={u.value}>
										{u.label}
									</option>
								))}
							</Select>
						</div>
					</>
				)}
			</div>
			{error ? (
				<p
					id={hintId}
					role="alert"
					className="text-danger text-xs"
					data-testid={`${testIdPrefix}-duration-error`}
				>
					{error}
				</p>
			) : describedBy ? null : (
				<p
					id={hintId}
					className={compact ? 'sr-only' : 'text-muted-foreground text-xs'}
					data-testid={`${testIdPrefix}-duration-hint`}
				>
					{end ? describe(end) : value === null ? (emptyHint ?? null) : null}
				</p>
			)}
		</div>
	);
}
