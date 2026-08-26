/**
 * CidrListField — edit a per-endpoint IP/CIDR allowlist.
 *
 * The allowlist is an *advanced, opt-in narrowing* of where an endpoint may
 * deliver: it widens the operator egress policy to also permit the listed
 * IP/CIDR ranges once the destination has been resolved and pinned at send time.
 * Empty (the default) means "operator egress policy only" — no per-endpoint
 * exemption. It never re-opens the platform's hard denies (link-local/metadata
 * stay blocked regardless), so this is safe to expose in the form.
 *
 * We validate each entry client-side for shape only (a bare IP or CIDR, v4 or
 * v6) to catch typos before a round-trip; the server re-validates and normalises
 * authoritatively. Entries are added as chips so the stored list stays an exact,
 * de-duplicated set of strings.
 */
import { useId, useState } from 'react';
import { Plus, X } from 'lucide-react';
import { Button, Disclosure, Input } from '@/shared/ui';

interface CidrListFieldProps {
	value: string[];
	onChange: (next: string[]) => void;
	/** Id of the group label, for `aria-labelledby`. */
	labelId?: string;
	/**
	 * Disables the add input and the per-chip remove buttons — e.g. while a
	 * batched Save is in flight, so a click can't race the mutation.
	 */
	disabled?: boolean;
}

/**
 * Structural validation of a single entry: a bare IPv4/IPv6 address or a
 * CIDR block. Intentionally permissive on the exact numeric ranges (the server
 * is the authority) — this just rejects obvious nonsense before a request.
 */
function isPlausibleCidrOrIp(raw: string): boolean {
	const value = raw.trim();
	if (!value) return false;
	const [addr, prefix, ...rest] = value.split('/');
	if (rest.length > 0) return false;
	const isV4 =
		/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.test(addr) &&
		addr.split('.').every((o) => Number(o) <= 255);
	// A loose IPv6 check: hex groups and at most one "::" run. The server does
	// the real parse; this only weeds out plainly-wrong input.
	const isV6 = /^[0-9a-fA-F:]+$/.test(addr) && addr.includes(':');
	if (!isV4 && !isV6) return false;
	if (prefix !== undefined) {
		if (!/^\d{1,3}$/.test(prefix)) return false;
		const max = isV4 ? 32 : 128;
		if (Number(prefix) > max) return false;
	}
	return true;
}

export function CidrListField({ value, onChange, labelId, disabled = false }: CidrListFieldProps) {
	const [draft, setDraft] = useState('');
	const [error, setError] = useState<string | null>(null);
	// Derived, unique per instance so two co-mounted fields never share an id
	// (which would break `aria-describedby` targeting).
	const errorId = useId();

	function add() {
		const entry = draft.trim();
		if (!entry) return;
		if (!isPlausibleCidrOrIp(entry)) {
			setError('Enter a valid IP address or CIDR block (e.g. 203.0.113.0/24).');
			return;
		}
		if (value.includes(entry)) {
			setError('That range is already in the list.');
			return;
		}
		onChange([...value, entry]);
		setDraft('');
		setError(null);
	}

	function remove(entry: string) {
		onChange(value.filter((v) => v !== entry));
	}

	return (
		<div className="space-y-2" role="group" aria-labelledby={labelId}>
			<p className="text-muted-foreground text-xs leading-relaxed">
				Optional. Also permit delivery to these IP ranges once the destination is resolved —
				useful for a stable internal or single-IP relay. Leave empty to use only the
				platform egress policy. Metadata and link-local addresses are always blocked,
				allowlist or not.
			</p>

			{value.length > 0 && (
				<ul className="flex flex-wrap gap-1.5">
					{value.map((entry) => (
						<li key={entry}>
							<span className="border-border bg-muted/40 text-foreground inline-flex items-center gap-1 rounded-md border px-2 py-1 font-mono text-xs">
								{entry}
								<button
									type="button"
									onClick={() => remove(entry)}
									disabled={disabled}
									aria-label={`Remove ${entry}`}
									className="text-muted-foreground hover:text-danger rounded transition-colors disabled:cursor-not-allowed disabled:opacity-50"
								>
									<X className="h-3 w-3" />
								</button>
							</span>
						</li>
					))}
				</ul>
			)}

			<div className="flex gap-2">
				<Input
					value={draft}
					onChange={(e) => {
						setDraft(e.target.value);
						if (error) setError(null);
					}}
					onKeyDown={(e) => {
						if (e.key === 'Enter') {
							e.preventDefault();
							add();
						}
					}}
					disabled={disabled}
					placeholder="203.0.113.0/24 or 2001:db8::/32"
					aria-label="Add an IP or CIDR range"
					aria-invalid={error ? true : undefined}
					aria-describedby={error ? errorId : undefined}
					className="font-mono"
				/>
				<Button
					type="button"
					variant="secondary"
					onClick={add}
					disabled={disabled || !draft.trim()}
				>
					<Plus className="h-4 w-4" />
					Add
				</Button>
			</div>
			{error && (
				<p id={errorId} className="text-danger text-sm" role="alert">
					{error}
				</p>
			)}

			<Disclosure summary="When should I use this?">
				Cloud provider IP ranges rotate, so an allowlist is best for a stable, internal, or
				single-IP destination. The check runs against the actual resolved IP at send time
				(both IPv4 and IPv6), so it can&apos;t be bypassed by DNS rebinding — and it can
				never permit a metadata or loopback address.
			</Disclosure>
		</div>
	);
}
