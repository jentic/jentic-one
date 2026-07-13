/**
 * Global search index for the docs portal.
 *
 * The top navbar search spans *everything* on the page — narrative sections,
 * CLI commands, scopes, and API endpoints — and jumps to the matching anchor.
 * To do that we flatten each source into a uniform `SearchItem` with a stable
 * `anchor` (an element id already present in the DOM) and a `kind` for grouping
 * + iconography in the results dropdown.
 *
 * Matching is a cheap case-insensitive substring over a precomputed haystack;
 * the corpus here is small (a few hundred rows) so this stays instant without a
 * fuzzy-search dependency.
 */
import type { CliBinary, CliCommand, ReferencePayload } from '@/modules/docs/api/types';
import { DOCS_SECTIONS } from '@/modules/docs/lib/nav';
import { operationAnchorId, modelAnchorId } from '@/modules/docs/lib/anchor';

export type SearchKind = 'section' | 'cli' | 'scope' | 'endpoint' | 'model';

export interface SearchItem {
	kind: SearchKind;
	/** Primary label shown in the result row. */
	title: string;
	/** Secondary, muted context line. */
	subtitle?: string;
	/** Element id to scroll to. */
	anchor: string;
	/** Lowercased text matched against the query. */
	haystack: string;
}

/** A flattened Broker operation/model for the search index (prefixed anchors). */
export interface BrokerSearchSource {
	operations: { method: string; path: string; summary?: string; operationId?: string }[];
	models: string[];
	/** Anchor namespace the Broker reference renders under. */
	anchorPrefix: string;
}

function cmdAnchor(cmd: CliCommand): string {
	return `cmd-${cmd.path.replace(/\s+/g, '-')}`;
}

function walkCli(binary: CliBinary, commands: CliCommand[], out: SearchItem[]) {
	for (const cmd of commands) {
		out.push({
			kind: 'cli',
			title: cmd.path,
			subtitle: cmd.short,
			anchor: cmdAnchor(cmd),
			haystack:
				`${cmd.path} ${cmd.short} ${cmd.long ?? ''} ${cmd.aliases?.join(' ') ?? ''}`.toLowerCase(),
		});
		if (cmd.subcommands?.length) walkCli(binary, cmd.subcommands, out);
	}
}

export function buildSearchIndex(
	reference: ReferencePayload | undefined,
	binaries: CliBinary[] | undefined,
	models: string[] | undefined = undefined,
	broker: BrokerSearchSource | undefined = undefined,
): SearchItem[] {
	const items: SearchItem[] = [];

	// Narrative sections.
	for (const s of DOCS_SECTIONS) {
		items.push({
			kind: 'section',
			title: s.label,
			anchor: s.id,
			haystack: s.label.toLowerCase(),
		});
	}

	// CLI commands (both binaries).
	for (const b of binaries ?? []) {
		walkCli(b, b.commands, items);
	}

	// Scopes (conceptual catalogue) — jump to the Permissions section.
	for (const scope of reference?.scopes?.scopes ?? []) {
		items.push({
			kind: 'scope',
			title: scope.name,
			subtitle: scope.description,
			anchor: 'permissions',
			haystack: `${scope.name} ${scope.description}`.toLowerCase(),
		});
	}

	// API endpoints — jump straight to the operation block in the API reference.
	for (const e of reference?.endpoints ?? []) {
		items.push({
			kind: 'endpoint',
			title: `${e.method} ${e.path}`,
			subtitle: e.summary || undefined,
			anchor: operationAnchorId(e.method, e.path),
			haystack: `${e.method} ${e.path} ${e.summary} ${e.operation_id ?? ''}`.toLowerCase(),
		});
	}

	// Models (component schemas) — jump to the model block in the API reference.
	for (const name of models ?? []) {
		items.push({
			kind: 'model',
			title: name,
			anchor: modelAnchorId(name),
			haystack: name.toLowerCase(),
		});
	}

	// Broker (data-plane) operations + models — jump to the Broker reference,
	// whose anchors are namespaced so they never collide with the control plane.
	if (broker) {
		const px = broker.anchorPrefix;
		for (const e of broker.operations) {
			const base = operationAnchorId(e.method, e.path);
			items.push({
				kind: 'endpoint',
				title: `${e.method} ${e.path}`,
				subtitle: e.summary ? `Broker · ${e.summary}` : 'Broker',
				anchor: px ? `${px}-${base}` : base,
				haystack:
					`broker ${e.method} ${e.path} ${e.summary ?? ''} ${e.operationId ?? ''}`.toLowerCase(),
			});
		}
		for (const name of broker.models) {
			const base = modelAnchorId(name);
			items.push({
				kind: 'model',
				title: name,
				subtitle: 'Broker',
				anchor: px ? `${px}-${base}` : base,
				haystack: `broker ${name}`.toLowerCase(),
			});
		}
	}

	return items;
}

/** Rank + cap matches. Prefix/word-start hits float above mid-string hits. */
export function searchIndex(index: SearchItem[], query: string, limit = 24): SearchItem[] {
	const q = query.trim().toLowerCase();
	if (!q) return [];
	const scored: { item: SearchItem; score: number }[] = [];
	for (const item of index) {
		const idx = item.haystack.indexOf(q);
		if (idx === -1) continue;
		// Lower score = better. Title hits beat body hits; earlier beats later.
		const inTitle = item.title.toLowerCase().includes(q);
		const score = idx + (inTitle ? 0 : 1000);
		scored.push({ item, score });
	}
	scored.sort((a, b) => a.score - b.score);
	return scored.slice(0, limit).map((s) => s.item);
}
