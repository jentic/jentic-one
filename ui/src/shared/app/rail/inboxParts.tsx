/**
 * The pieces the Inbox's two views share, so Waiting and Decided load, fail,
 * stand empty and lay out their rows alike.
 */
import { ErrorAlert, SkeletonRows } from '@/shared/ui';

/** An empty view: the shell's EmptyState, flat on the rail. */
export const INBOX_EMPTY_CLASS = 'bg-transparent px-5 py-10 sm:px-5 sm:py-10';

/** One row's control: the whole row opens the call. */
export const INBOX_ROW_CLASS =
	'hover:bg-surface-1-hover focus-visible:ring-ring flex w-full cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-left focus-visible:ring-2 focus-visible:outline-none';

/** "Show all" / "Show more" under a list. */
export const INBOX_MORE_CLASS = 'mx-2 mt-1 h-6 px-2 text-[11px]';

export function InboxLoading() {
	return (
		<div className="px-3 py-3">
			<SkeletonRows rows={3} />
		</div>
	);
}

export function InboxLoadError({
	title,
	error,
	onRetry,
	retrying,
}: {
	title: string;
	error: unknown;
	onRetry: () => void;
	retrying: boolean;
}) {
	return (
		<div className="px-3 py-3">
			<ErrorAlert
				title={title}
				message={error instanceof Error ? error : 'Something went wrong.'}
				onRetry={onRetry}
				retrying={retrying}
			/>
		</div>
	);
}
