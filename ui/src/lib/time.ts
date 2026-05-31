export function timeAgo(ts: number | null | undefined): string {
	if (!ts) return '';
	const diff = Math.floor(Date.now() / 1000 - ts);
	if (diff < 0) return 'just now';
	if (diff < 60) return `${diff}s ago`;
	if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
	if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
	const days = Math.floor(diff / 86400);
	if (days === 1) return 'yesterday';
	if (days < 30) return `${days}d ago`;
	return new Date(ts * 1000).toLocaleDateString(undefined, {
		month: 'short',
		day: 'numeric',
		year: 'numeric',
	});
}

export function formatTimestamp(ts: number | null | undefined): string {
	if (!ts) return '';
	return new Date(ts * 1000).toLocaleString();
}
