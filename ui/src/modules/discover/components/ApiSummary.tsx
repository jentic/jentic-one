/**
 * ApiSummary — renders an API's `info.description` inside the detail sheet.
 *
 * Ported from jentic-mini: the description is Markdown (sanitized) truncated to
 * a word boundary at ~280 chars with a "Show more / Show less" toggle. When the
 * spec has no description there's nothing to show.
 */
import { useState } from 'react';
import { Markdown } from '@/shared/ui';

const SUMMARY_TRUNCATE = 280;

interface ApiSummaryProps {
	description?: string | null;
}

export function ApiSummary({ description }: ApiSummaryProps) {
	const [expanded, setExpanded] = useState(false);
	const desc = (description ?? '').trim();
	if (!desc) return null;

	const truncated = desc.length > SUMMARY_TRUNCATE;
	const raw = desc.slice(0, SUMMARY_TRUNCATE);
	const visible =
		!truncated || expanded
			? desc
			: (raw.includes(' ') ? raw.slice(0, raw.lastIndexOf(' ')) : raw).trimEnd();

	return (
		<div data-testid="api-summary" className="mb-4">
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
