import { useState } from 'react';
import { Markdown } from '@/components/ui/Markdown';

export const SUMMARY_TRUNCATE = 280;

/**
 * "What does this API let me do" — short prose blurb at the top of both
 * Workspace and Directory bodies. Strategy:
 *   1. If a non-empty markdown description is provided, render the first
 *      `SUMMARY_TRUNCATE` chars (with a "Show more" toggle).
 *   2. Otherwise fall back to a synthesised one-liner from `host` /
 *      `opCount` / `tagCount` so every sheet shows *something*.
 *   3. Skip entirely when even the fallback has nothing meaningful to
 *      say (no host, no ops). Caller is responsible for not rendering
 *      this in that case — the component returns null.
 */
export function ApiSummary({
	description,
	title,
	host,
	opCount,
	tagCount,
}: {
	description?: string | null;
	/** The title shown in the sheet header — skip description if identical. */
	title?: string | null;
	host?: string | null;
	opCount?: number;
	tagCount?: number;
}) {
	const [expanded, setExpanded] = useState(false);

	const desc = (description ?? '').trim();
	const titleStr = (title ?? '').trim();
	const hasDesc = desc.length > 0 && desc.toLowerCase() !== titleStr.toLowerCase();

	if (hasDesc) {
		const truncated = desc.length > SUMMARY_TRUNCATE;
		const raw = desc.slice(0, SUMMARY_TRUNCATE);
		const visible =
			!truncated || expanded
				? desc
				: (raw.includes(' ') ? raw.slice(0, raw.lastIndexOf(' ')) : raw).trimEnd();

		return (
			<div data-testid="api-summary">
				<Markdown
					source={visible + (truncated && !expanded ? '…' : '')}
					className="text-muted-foreground text-sm leading-relaxed"
				/>
				{truncated && (
					<button
						type="button"
						onClick={() => setExpanded((v) => !v)}
						className="text-accent-teal mt-1 text-xs font-medium hover:underline"
						data-testid="api-summary-toggle"
					>
						{expanded ? 'Show less' : 'Show more'}
					</button>
				)}
			</div>
		);
	}

	const fallbackBits: string[] = [];
	if (host) fallbackBits.push(host);
	if (typeof opCount === 'number' && opCount > 0) {
		const opPart = `${opCount} operation${opCount === 1 ? '' : 's'}`;
		const tagPart =
			typeof tagCount === 'number' && tagCount > 0
				? ` across ${tagCount} tag${tagCount === 1 ? '' : 's'}`
				: '';
		fallbackBits.push(opPart + tagPart);
	}

	if (fallbackBits.length === 0) return null;

	return (
		<p
			className="text-muted-foreground/80 text-sm leading-relaxed italic"
			data-testid="api-summary"
		>
			{fallbackBits.join(' — ')}
		</p>
	);
}
