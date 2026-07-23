/**
 * DenyReasonField — the "Reason (sent back to the agent)" label + textarea used
 * whenever a human denies an access request. Shared by the rail row's fast-path
 * deny and the per-item dialog so the copy and styling can't drift apart.
 *
 * Stateless: the parent owns the value. `id` must be unique per field so the
 * <label> binds to the right textarea.
 */
type DenyReasonFieldProps = {
	id: string;
	value: string;
	onChange: (value: string) => void;
	autoFocus?: boolean;
};

export function DenyReasonField({ id, value, onChange, autoFocus }: DenyReasonFieldProps) {
	return (
		<div className="flex flex-col gap-1">
			<label
				htmlFor={id}
				className="text-muted-foreground text-[10px] font-semibold tracking-wide uppercase"
			>
				Reason (sent back to the agent)
			</label>
			<textarea
				id={id}
				value={value}
				onChange={(e) => onChange(e.target.value)}
				rows={2}
				autoFocus={autoFocus}
				placeholder="Why is this being denied?"
				className="border-border bg-background focus:border-primary placeholder:text-input-placeholder w-full resize-none rounded border px-2 py-1 text-[11px] outline-none"
			/>
		</div>
	);
}
