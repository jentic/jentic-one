import { useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { subscribeCredentialImported } from '@/lib/events/credentialImported';
import { toast } from '@/components/ui/toastStore';

/**
 * Subscribes to `credentialImported` events and invalidates all
 * discovery-related query caches. Fires `onImported` so the caller
 * can patch local UI state (e.g. clear selectedEntity, update
 * optimistic import sets).
 */
export function useCredentialImportedSync(opts: { onImported: (apiId: string) => void }): void {
	const queryClient = useQueryClient();

	useEffect(() => {
		const off = subscribeCredentialImported((evt) => {
			if (!evt.api_id) return;

			queryClient.invalidateQueries({ queryKey: ['sheet-workflows-directory', evt.api_id] });
			queryClient.invalidateQueries({ queryKey: ['workflows'] });
			queryClient.invalidateQueries({ queryKey: ['apis', 'discover'] });
			queryClient.invalidateQueries({ queryKey: ['apis'] });
			queryClient.invalidateQueries({ queryKey: ['catalog'] });
			queryClient.invalidateQueries({ queryKey: ['search'] });
			queryClient.invalidateQueries({ queryKey: ['sheet-resolve-source', evt.api_id] });
			queryClient.invalidateQueries({ queryKey: ['workspace'] });
			queryClient.invalidateQueries({ queryKey: ['workspace-stats'] });
			// Per-API and global credential queries: WorkflowDetailView keys on
			// ['credentials','for-api',apiId] and ApiDetailView on
			// ['credentials',apiId], so without this the chips that depend
			// on credential presence stay stale until staleTime expires.
			queryClient.invalidateQueries({ queryKey: ['credentials'] });

			opts.onImported(evt.api_id);

			toast({
				title: 'Imported to workspace',
				description: `${evt.api_id} is now in your workspace.`,
				variant: 'success',
				action: {
					label: 'View in Workspace',
					onClick: () => {
						window.location.assign(`/workspace/apis/${encodeURIComponent(evt.api_id)}`);
					},
				},
			});
		});
		return off;
	}, [queryClient, opts.onImported]);
}
