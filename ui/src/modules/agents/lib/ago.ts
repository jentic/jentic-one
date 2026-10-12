import { timeAgo } from '@/shared/lib';

/** "3m ago" from the shared compact age; "just now" under a second. */
export function ago(iso: string): string {
	const age = timeAgo(iso);
	return age === 'now' ? 'just now' : `${age} ago`;
}
