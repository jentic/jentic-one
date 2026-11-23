import type { SpecOperation } from '@/modules/docs/lib/apiSpec';
import { modelAnchorId, operationAnchorId } from '@/modules/docs/lib/anchor';

/**
 * Anchors are namespaced by an optional `prefix` so two ApiReferenceView
 * instances on the same page (the control plane + the standalone Broker) never
 * collide on the shared `MODELS_ANCHOR` constant or on like-named tag groups.
 * The control-plane instance uses the empty default (anchors unchanged), the
 * Broker passes its own prefix.
 */
export function tagGroupAnchorId(name: string, prefix = ''): string {
	return `${prefix}apigroup-${name}`.replace(/[^a-zA-Z0-9_-]+/g, '-');
}

export function tagAnchorId(name: string, prefix = ''): string {
	return `${prefix}apitag-${name}`.replace(/[^a-zA-Z0-9_-]+/g, '-');
}

export function opAnchorId(op: SpecOperation, prefix = ''): string {
	const base = operationAnchorId(op.method, op.path);
	return prefix ? `${prefix}-${base}` : base;
}

export const MODELS_ANCHOR = 'api-models';

export function modelsAnchorId(prefix = ''): string {
	return prefix ? `${prefix}-${MODELS_ANCHOR}` : MODELS_ANCHOR;
}

export function prefixedModelAnchorId(name: string, prefix = ''): string {
	const base = modelAnchorId(name);
	return prefix ? `${prefix}-${base}` : base;
}
