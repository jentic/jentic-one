/**
 * Managed-by badge and its renderer registry.
 *
 * A credential an extension manages carries `managed_by: {kind, ref}` (set by
 * the extension through the backend credentials seam, never by a client). Such
 * a credential cannot be bound to an agent, and it is edited or deleted where
 * it is managed, not from the credentials UI (an `org:admin` may still delete
 * it). The badge tells people where that is.
 *
 * An extension registers a renderer for its `kind` once, at import time:
 *
 *     registerManagedByRenderer('example_connection', ExampleConnectionBadge);
 *
 * The renderer receives the marker and renders the badge (for example the
 * managing object's name and a link to it). A kind with no registered renderer
 * gets the generic fallback, "Managed by <kind>".
 */
import type { ComponentType } from 'react';
import { Lock } from 'lucide-react';
import { Tag } from '@/shared/ui';
import type { Credential, ManagedByResponse } from '@/shared/credentials/api';

export type ManagedBy = ManagedByResponse;

export interface ManagedByRendererProps {
	managedBy: ManagedBy;
}

const renderers = new Map<string, ComponentType<ManagedByRendererProps>>();

/**
 * Register the badge for credentials managed by objects of `kind`. A second
 * registration for the same kind replaces the first.
 */
export function registerManagedByRenderer(
	kind: string,
	renderer: ComponentType<ManagedByRendererProps>,
): void {
	renderers.set(kind, renderer);
}

/** Test seam: forget a registration. */
export function unregisterManagedByRenderer(kind: string): void {
	renderers.delete(kind);
}

/** Whether an extension manages the credential. */
export function isManagedCredential(cred: Pick<Credential, 'managed_by'>): boolean {
	return cred.managed_by != null;
}

/** The kind as words: `example_connection` → `example connection`. */
export function managedByLabel(managedBy: ManagedBy): string {
	return managedBy.kind.replace(/[_-]+/g, ' ');
}

/** The generic badge for a kind no extension registered a renderer for. */
export function ManagedByFallbackBadge({ managedBy }: ManagedByRendererProps) {
	return (
		<Tag
			icon={Lock}
			data-testid="credential-managed-badge"
			title={`Managed by ${managedByLabel(managedBy)} ${managedBy.ref}. Change or delete it there.`}
		>
			Managed by {managedByLabel(managedBy)}
		</Tag>
	);
}

/** The managed-by badge for a credential; renders nothing for an unmanaged one. */
export function ManagedByBadge({ managedBy }: { managedBy: ManagedBy | null | undefined }) {
	if (managedBy == null) return null;
	const Renderer = renderers.get(managedBy.kind) ?? ManagedByFallbackBadge;
	return <Renderer managedBy={managedBy} />;
}

/** The note shown where a managed credential would otherwise be edited or deleted. */
export function ManagedCredentialNote({
	managedBy,
	adminMayDelete = false,
}: {
	managedBy: ManagedBy;
	/** Add that an `org:admin` may still delete it (the delete confirm). */
	adminMayDelete?: boolean;
}) {
	return (
		<div
			className="bg-surface-inset space-y-2 rounded-lg p-3 text-xs"
			data-testid="credential-managed-note"
		>
			<ManagedByBadge managedBy={managedBy} />
			<p className="text-muted-foreground">
				This credential is managed by {managedByLabel(managedBy)}. Change or delete it
				there; it can't be edited here or bound to an agent.
				{adminMayDelete &&
					' As an admin you may still delete it, for example when what manages it is gone.'}
			</p>
		</div>
	);
}
