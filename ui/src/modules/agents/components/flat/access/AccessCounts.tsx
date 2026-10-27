/**
 * AccessCounts — allowed · ask · denied for a rule list over an API's
 * operations: a thin proportional bar and the three counts, always all three,
 * so an Ask is never folded into allowed. Denied says how much of it is "not
 * set" (no rule matches) so it never reads as the count of Deny choices.
 */
import { deniedText, type OperationCounts } from '@/shared/credentials/components';

export function AccessCounts({
	counts,
	'data-testid': testId,
}: {
	counts: OperationCounts;
	'data-testid'?: string;
}) {
	const { allowed, ask, denied } = counts;
	const total = allowed + ask + denied;
	return (
		<span
			className="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-xs"
			data-testid={testId}
		>
			{total > 0 && (
				<span
					aria-hidden="true"
					className="bg-surface-sheet flex h-1.5 w-44 overflow-hidden rounded-full"
				>
					{allowed > 0 && <span className="bg-success" style={{ flexGrow: allowed }} />}
					{ask > 0 && <span className="bg-warning" style={{ flexGrow: ask }} />}
					{denied > 0 && <span className="bg-danger/45" style={{ flexGrow: denied }} />}
				</span>
			)}
			<span className="inline-flex items-center gap-2.5 tabular-nums">
				<span className="text-success">
					<b className="font-semibold">{allowed.toLocaleString()}</b> allowed
				</span>
				<span className="text-warning">
					<b className="font-semibold">{ask.toLocaleString()}</b> ask
				</span>
				<span className="text-danger">
					<b className="font-semibold">{denied.toLocaleString()}</b>
					{deniedText(counts).slice(denied.toLocaleString().length)}
				</span>
			</span>
		</span>
	);
}
