/**
 * SingleUseField — "End after one use", next to the end date and the idle
 * limit wherever a binding's policy is set. Off by default. The console only
 * offers one use; a binding given a larger count through the API keeps it,
 * and the box then reads "End after 5 uses" (unticking it removes the count).
 *
 * Under it sits the one line describing when access ends, for all the limits
 * together (`end` and `idleSeconds` are what the sibling controls would set;
 * any limit reached ends the access). The sibling controls show only their
 * own errors and point their `aria-describedby` at `summaryId`.
 */
import { Checkbox } from '@/shared/ui';
import { describeAccess } from '@/shared/credentials/lib/idleLimit';

export interface SingleUseFieldProps {
	/** The use count: `null` off, 1 for single use (a larger count only from the API). */
	value: number | null;
	onChange: (value: number | null) => void;
	/** When the end date chosen beside it would end access if saved now. */
	end: Date | 'never' | null;
	/** The idle limit chosen beside it, in seconds; null without one. */
	idleSeconds: number | null;
	disabled?: boolean;
	/** The line while no end date is chosen. */
	emptyHint?: string | null;
	/** A further sentence on the same line (e.g. what the request asked for). */
	note?: string | null;
	/** Id of the summary line, for the sibling controls' `aria-describedby`. */
	summaryId?: string;
	/** Prefix for test ids (`<prefix>-single-use`, `<prefix>-access-summary`). */
	testIdPrefix?: string;
}

export function SingleUseField({
	value,
	onChange,
	end,
	idleSeconds,
	disabled = false,
	emptyHint = null,
	note = null,
	summaryId,
	testIdPrefix = 'binding',
}: SingleUseFieldProps) {
	const summary = [describeAccess(end, idleSeconds, value, emptyHint), note]
		.filter(Boolean)
		.join(' ');
	const label = value !== null && value > 1 ? `End after ${value} uses` : 'End after one use';
	return (
		<div className="space-y-1.5">
			<div data-testid={`${testIdPrefix}-single-use`}>
				<Checkbox
					size="sm"
					checked={value !== null}
					disabled={disabled}
					onChange={(on): void => onChange(on ? 1 : null)}
				>
					<span className="text-foreground text-sm">{label}</span>
				</Checkbox>
			</div>
			{summary && (
				<p
					id={summaryId}
					className="text-muted-foreground text-xs"
					data-testid={`${testIdPrefix}-access-summary`}
				>
					{summary}
				</p>
			)}
		</div>
	);
}
