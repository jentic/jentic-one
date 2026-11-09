/**
 * ActionList — an action rule's operations in human terms: one compact row per
 * action (method badge · path template · summary), the same row the By action
 * checklist uses, so a chosen action reads the same wherever it is shown.
 *
 * Operation ids are secondary detail: each row carries its id as a `title`,
 * and ids that can't be named (the list is unavailable, or the API no longer
 * has the operation) sit behind a quiet "Details" disclosure — never as the
 * primary content.
 */
import { Loader2 } from 'lucide-react';
import { DetailsLine, MethodBadge } from '@/shared/ui';
import { PlainText } from '@/shared/credentials/components/PlainText';
import { cn } from '@/shared/lib/utils';
import {
	actionCount,
	actionsNote,
	type ActionsDescription,
} from '@/shared/credentials/lib/actionRules';

export interface ActionListProps {
	description: ActionsDescription;
	className?: string;
	'data-testid'?: string;
}

export function ActionList({ description, className, 'data-testid': testId }: ActionListProps) {
	const { entries, status, count } = description;
	const known = entries.filter((e) => e.kind !== 'unresolved');
	const unnamed = entries.filter((e) => e.kind !== 'found').map((e) => e.id);
	const note = actionsNote(description);
	const loading = status === 'loading' && description.unresolved > 0;
	return (
		<div
			className={cn('min-w-0 space-y-1', className)}
			data-testid={testId}
			data-status={status}
			aria-busy={loading || undefined}
		>
			{known.length > 0 && (
				<ul aria-label="Actions" className="max-h-48 overflow-y-auto">
					{known.map((entry) => (
						<li
							key={entry.id}
							title={entry.id}
							className="flex min-w-0 items-center gap-2 px-1.5 py-1"
							data-kind={entry.kind}
						>
							{entry.kind === 'found' ? (
								<>
									<MethodBadge method={entry.method} />
									<span className="text-foreground min-w-0 truncate font-mono text-[11.5px]">
										{entry.path}
									</span>
									{entry.summary && (
										<PlainText
											value={{
												text: entry.summary,
												generated: !!entry.generated,
											}}
											className="ml-auto"
										/>
									)}
									{entry.api && (
										<span
											className={cn(
												'text-foreground-sub shrink-0 text-xs',
												!entry.summary && 'ml-auto',
											)}
											data-testid="action-api"
										>
											— on {entry.api.title}
										</span>
									)}
								</>
							) : (
								<>
									{/* Holds the badge column so the rows stay aligned. */}
									<span aria-hidden="true" className="w-[58px] shrink-0" />
									<span className="text-foreground-sub min-w-0 truncate text-xs italic">
										An action not in this version of the API
									</span>
								</>
							)}
						</li>
					))}
				</ul>
			)}
			{loading ? (
				<p
					role="status"
					className="text-muted-foreground flex items-center gap-1.5 px-1.5 text-xs"
				>
					<Loader2
						aria-hidden="true"
						className="h-3.5 w-3.5 shrink-0 animate-spin motion-reduce:animate-none"
					/>
					{known.length === 0
						? `Reading this API's actions — ${actionCount(count)}…`
						: `Reading this API's actions — ${description.unresolved.toLocaleString()} more…`}
				</p>
			) : (
				note && (
					<DetailsLine
						className="px-1.5"
						summary={
							status === 'unavailable' && known.length === 0
								? `${actionCount(count)} — this API's list of actions isn't available, so they can't be named.`
								: note
						}
					>
						<p>Operation ids the broker matches on:</p>
						<ul aria-label="Operation ids" className="flex flex-wrap gap-1">
							{unnamed.map((id) => (
								<li key={id}>
									<code className="bg-surface-sheet text-foreground-sub rounded-sm px-1 py-0.5 font-mono text-[10px]">
										{id}
									</code>
								</li>
							))}
						</ul>
					</DetailsLine>
				)
			)}
		</div>
	);
}
