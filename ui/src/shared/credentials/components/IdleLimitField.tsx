/**
 * IdleLimitField — the optional second row of the duration control: "Also end
 * if unused for", off by default. Ticking it turns on an idle limit (14 days
 * to start), chosen from idle-sized presets or "Custom…" in hours or days,
 * between 1 day and 999 days. Controlled: `null` is off.
 *
 * It shows only its own error: when access ends, for all the limits
 * together, is the one line under "End after one use" (`SingleUseField`),
 * which `describedBy` names. The warning before access ends sits below all
 * the limits (`AccessEndFields`), not here.
 */
import { useId } from 'react';
import { Checkbox, Input, Select } from '@/shared/ui';
import {
	DEFAULT_IDLE_LIMIT,
	IDLE_PRESETS,
	IDLE_UNITS,
	idleLabel,
	idleLimitError,
	type IdleLimit,
	type IdlePreset,
	type IdleUnit,
} from '@/shared/credentials/lib/idleLimit';

export interface IdleLimitFieldProps {
	/** The idle limit; `null` while the row is off. */
	value: IdleLimit | null;
	onChange: (value: IdleLimit | null) => void;
	/** Id of the shared line describing when access ends. */
	describedBy?: string;
	disabled?: boolean;
	/** Prefix for test ids (`<prefix>-idle-*`). */
	testIdPrefix?: string;
	/**
	 * The org's maximum idle limit, in seconds; null when it sets none. Under
	 * one, longer presets are disabled and the row can't be turned off.
	 */
	maxSeconds?: number | null;
}

const CUSTOM = 'custom';

export function IdleLimitField({
	value,
	onChange,
	describedBy,
	disabled = false,
	testIdPrefix = 'binding',
	maxSeconds = null,
}: IdleLimitFieldProps) {
	const hintId = useId();
	const requiredId = useId();
	const error = idleLimitError(value, maxSeconds);
	// An org maximum makes an idle limit required: once on, it stays on.
	const required = maxSeconds !== null && value !== null;
	const custom = value?.kind === 'custom' ? value : null;
	const describes = error ? hintId : describedBy;

	return (
		<div className="space-y-1.5" data-testid={`${testIdPrefix}-idle`}>
			<div data-testid={`${testIdPrefix}-idle-toggle`}>
				<Checkbox
					size="sm"
					checked={value !== null}
					disabled={disabled || required}
					ariaDescribedBy={required ? requiredId : undefined}
					onChange={(): void => onChange(value === null ? DEFAULT_IDLE_LIMIT : null)}
				>
					<span className="text-foreground text-sm">Also end if unused for</span>
				</Checkbox>
			</div>
			{required && (
				<p
					id={requiredId}
					className="text-muted-foreground pl-6 text-xs"
					data-testid={`${testIdPrefix}-idle-required`}
				>
					Your org requires an idle limit of at most {idleLabel(maxSeconds)}, so this
					stays on.
				</p>
			)}
			{value !== null && (
				<div className="flex flex-wrap items-start gap-2 pl-6">
					<div className="w-40 shrink-0">
						<Select
							aria-label="Idle limit"
							aria-describedby={describes}
							value={value.kind === 'preset' ? value.preset : CUSTOM}
							disabled={disabled}
							className="py-1.5"
							data-testid={`${testIdPrefix}-idle-preset`}
							onChange={(e): void => {
								const next = e.target.value;
								if (next === CUSTOM) {
									if (!custom)
										onChange({
											kind: 'custom',
											amount: '14',
											unit: 'days',
										});
									return;
								}
								onChange({
									kind: 'preset',
									preset: next as IdlePreset,
								});
							}}
						>
							{IDLE_PRESETS.map((p) => (
								<option
									key={p.value}
									value={p.value}
									disabled={maxSeconds !== null && p.seconds > maxSeconds}
								>
									{p.label}
								</option>
							))}
							<option value={CUSTOM}>Custom…</option>
						</Select>
					</div>
					{custom && (
						<>
							<div className="w-24 shrink-0">
								<Input
									type="number"
									inputMode="numeric"
									min={1}
									step={1}
									size="sm"
									aria-label="Idle amount"
									aria-invalid={error ? true : undefined}
									aria-describedby={describes}
									value={custom.amount}
									disabled={disabled}
									data-testid={`${testIdPrefix}-idle-amount`}
									onChange={(e): void =>
										onChange({ ...custom, amount: e.target.value })
									}
								/>
							</div>
							<div className="w-28 shrink-0">
								<Select
									aria-label="Idle unit"
									aria-describedby={describes}
									value={custom.unit}
									disabled={disabled}
									className="py-1.5"
									data-testid={`${testIdPrefix}-idle-unit`}
									onChange={(e): void =>
										onChange({ ...custom, unit: e.target.value as IdleUnit })
									}
								>
									{IDLE_UNITS.map((u) => (
										<option key={u.value} value={u.value}>
											{u.label}
										</option>
									))}
								</Select>
							</div>
						</>
					)}
				</div>
			)}
			{value !== null && error && (
				<p
					id={hintId}
					role="alert"
					className="text-danger pl-6 text-xs"
					data-testid={`${testIdPrefix}-idle-error`}
				>
					{error}
				</p>
			)}
		</div>
	);
}
