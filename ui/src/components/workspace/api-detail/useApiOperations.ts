import { useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/api/client';
import { OPS_PAGE_SIZE, filterOps, topTags } from '@/components/discovery/OperationsListControls';
import type { OpRow } from '@/components/discovery/OperationsListControls';
import { parseCapabilityId } from '@/lib/capabilityId';

/**
 * Encapsulates the operations list state machine for one API:
 *
 *   1. Pagination — fetch a page at a time, dedupe, accumulate.
 *   2. Filtering — when the user types or picks a tag, the visible
 *      list must reflect every loaded op, not just the current page.
 *      To make filtering accurate even before the user has clicked
 *      "Load more", a one-shot batched prefetch fires in the
 *      background as soon as a filter becomes active. The pagination
 *      footer hides while that's running.
 *   3. Reset on apiId change — refs are scoped to a single API; we
 *      tear them down when the route param flips.
 *
 * Lifting this into a hook lets the orchestrator surface the totals
 * (`opsTotal`, `tagOptions.length`) to peer sections — the overview
 * strip and `ApiSummary` — without `OperationsSection` needing a
 * side-effecty parent-callback bridge.
 */
export function useApiOperations(apiId: string) {
	const [opsFilter, setOpsFilter] = useState('');
	const [activeTag, setActiveTag] = useState<string | null>(null);
	const [expandedOps, setExpandedOps] = useState<Set<string>>(new Set());
	const [pages, setPages] = useState(1);

	const prevApiIdRef = useRef(apiId);
	const accumulatorRef = useRef<any[]>([]);
	const seenKeysRef = useRef<Set<string>>(new Set());

	if (prevApiIdRef.current !== apiId) {
		prevApiIdRef.current = apiId;
		accumulatorRef.current = [];
		seenKeysRef.current = new Set();
		setPages(1);
		setOpsFilter('');
		setActiveTag(null);
		setExpandedOps(new Set());
	}

	const offset = (pages - 1) * OPS_PAGE_SIZE;

	const {
		data: opsData,
		isLoading: isLoadingOps,
		isFetching,
	} = useQuery({
		queryKey: ['operations', apiId, offset],
		queryFn: () => api.listOperations(apiId, 1, OPS_PAGE_SIZE, { offset }),
		staleTime: 5 * 60_000,
	});

	const prelimTotal = opsData?.total ?? 0;
	const needsAll = (opsFilter.trim() !== '' || activeTag !== null) && prelimTotal > 0;
	const { data: allOpsData } = useQuery({
		queryKey: ['operations-all', apiId],
		queryFn: async () => {
			const all: any[] = [];
			let off = 0;
			const batchSize = 200;
			while (true) {
				const batch = await api.listOperations(apiId, 1, batchSize, { offset: off });
				all.push(...(batch.data ?? []));
				if (all.length >= (batch.total ?? 0) || (batch.data?.length ?? 0) < batchSize)
					break;
				off += batchSize;
			}
			return { data: all, total: all.length };
		},
		staleTime: 5 * 60_000,
		enabled: needsAll,
	});

	const operations = useMemo(() => {
		if (needsAll && allOpsData?.data?.length) {
			return allOpsData.data;
		}
		const incoming = opsData?.data ?? [];
		if (incoming.length === 0) return accumulatorRef.current;
		let changed = false;
		const next = [...accumulatorRef.current];
		for (const op of incoming) {
			const key = op.id;
			if (seenKeysRef.current.has(key)) continue;
			seenKeysRef.current.add(key);
			next.push(op);
			changed = true;
		}
		if (changed) {
			accumulatorRef.current = next;
		}
		return accumulatorRef.current;
	}, [opsData, needsAll, allOpsData]);

	const opsTotal = opsData?.total ?? operations.length;
	const hasMore = !needsAll && operations.length < opsTotal;
	const isFetchingMore = isFetching && pages > 1;

	const rows: OpRow[] = useMemo(
		() =>
			operations.map((op: any) => {
				const parsed = parseCapabilityId(op.id ?? '');
				return {
					key:
						op.id ??
						`${parsed?.method ?? ''} ${parsed?.host ?? ''}${parsed?.path ?? ''}`,
					method: parsed?.method ?? op.method,
					path: parsed?.path ?? op.path,
					label: op.summary || op.id || '',
					tags: Array.isArray(op.tags) ? op.tags : [],
				};
			}),
		[operations],
	);

	const tagOptions = useMemo(() => topTags(rows.flatMap((r) => r.tags)), [rows]);
	const visible = useMemo(
		() => filterOps(rows, opsFilter, activeTag),
		[rows, opsFilter, activeTag],
	);

	function toggleExpanded(key: string) {
		setExpandedOps((prev) => {
			const next = new Set(prev);
			if (next.has(key)) next.delete(key);
			else next.add(key);
			return next;
		});
	}

	function loadMore() {
		setPages((p) => p + 1);
	}

	return {
		rows,
		visible,
		operations,
		opsTotal,
		tagOptions,
		opsFilter,
		setOpsFilter,
		activeTag,
		setActiveTag,
		expandedOps,
		toggleExpanded,
		isLoadingOps,
		hasMore,
		isFetchingMore,
		loadMore,
	};
}
