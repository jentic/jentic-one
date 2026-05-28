import { useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { subscribeCredentialImported } from '@/lib/events/credentialImported';
import { subscribeApiImported } from '@/lib/events/apiImported';
import { toast } from '@/components/ui/toastStore';

/**
 * Subscribes to `credentialImported` AND `apiImported` events and
 * invalidates all discovery-related query caches.
 *
 * Both events warrant the same cache busting from the Discover surface
 * because Discover only cares about "did the workspace gain something
 * tied to this api_id?". The split between channels lives at the
 * *emitter* layer (so credential-only consumers don't react to bare
 * imports). This sync hook is a unified consumer.
 *
 * `onImported` lets the caller patch local UI state (e.g. clear the
 * selected entity, drop optimistic-import flags) regardless of which
 * channel fired.
 */
export function useCredentialImportedSync(opts: { onImported: (apiId: string) => void }): void {
	const queryClient = useQueryClient();

	useEffect(() => {
		const invalidate = (api_id: string) => {
			queryClient.invalidateQueries({ queryKey: ['sheet-workflows-directory', api_id] });
			queryClient.invalidateQueries({ queryKey: ['workflows'] });
			queryClient.invalidateQueries({ queryKey: ['apis', 'discover'] });
			queryClient.invalidateQueries({ queryKey: ['apis'] });
			queryClient.invalidateQueries({ queryKey: ['catalog'] });
			queryClient.invalidateQueries({ queryKey: ['search'] });
			queryClient.invalidateQueries({ queryKey: ['sheet-resolve-source', api_id] });
			queryClient.invalidateQueries({ queryKey: ['workspace'] });
			queryClient.invalidateQueries({ queryKey: ['workspace-stats'] });
			// Per-API and global credential queries: WorkflowDetailView keys on
			// ['credentials','for-api',apiId] and ApiDetailView on
			// ['credentials',apiId], so without this the chips that depend
			// on credential presence stay stale until staleTime expires.
			queryClient.invalidateQueries({ queryKey: ['credentials'] });
			opts.onImported(api_id);
		};

		const offCred = subscribeCredentialImported((evt) => {
			if (!evt.api_id) return;
			invalidate(evt.api_id);
			toast({
				title: 'Imported to workspace',
				description: `${evt.api_id} is now in your workspace.`,
				variant: 'success',
				action: {
					label: 'View in Workspace',
					onClick: () => {
						window.location.assign(
							`/workspace/apis/${encodeURIComponent(evt.api_id)}`,
						);
					},
				},
			});
		});

		const offApi = subscribeApiImported((evt) => {
			if (!evt.api_id) return;
			invalidate(evt.api_id);
			// Don't double-toast — when both events fire (catalog import
			// triggered by credential save), the credential channel's
			// toast already covered "got an API". Plain API imports
			// (Discover sheet "Add to workspace") only fire `apiImported`,
			// so we still toast here when source === 'catalog'.
			toast({
				title: 'API imported',
				description: `${evt.api_id} is now in your workspace.`,
				variant: 'success',
			});
		});

		return () => {
			offCred();
			offApi();
		};
	}, [queryClient, opts.onImported]);
}
