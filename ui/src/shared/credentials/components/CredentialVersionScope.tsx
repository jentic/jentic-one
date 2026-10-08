/**
 * The "Use for  [Version X | Any version]" picker every add-credential surface
 * shows for an API with a known registry version.
 *
 * It maps onto `APIReferenceRequest.version`: "Version X" sends the version,
 * "Any version" sends none — the backend stores an empty version as the
 * wildcard (`canonical_credential_scope` → NULL), covering every revision of the
 * API. "Any version" is the default everywhere, so a re-ingested spec keeps its
 * credential. On "Any version" a calm note spells out that the credential —
 * and so any access rules on it — reaches future versions of the API too.
 * Renders nothing without a version to pin.
 */
import { SegmentedToggle } from '@/shared/ui';

/** Which versions of the API the credential covers. */
export type CredentialVersionScopeValue = 'pinned' | 'any';

/** The registry version a pick can be pinned to, or `''` when it has none we can trust. */
export function pinnableVersionOf(api: { source: 'local' | 'catalog'; version?: string }): string {
	// A catalog row carries no real version (the picker fills in a placeholder)
	// and the registry's ingested spec need not report the same string, so only
	// a workspace (registered) API offers a pin.
	return api.source === 'local' ? (api.version?.trim() ?? '') : '';
}

export function CredentialVersionScope({
	version,
	value,
	onChange,
}: {
	/** The version "Version X" pins to. Empty hides the picker. */
	version: string;
	value: CredentialVersionScopeValue;
	onChange: (value: CredentialVersionScopeValue) => void;
}) {
	if (!version) return null;
	return (
		<div className="space-y-1.5" data-testid="credential-version-scope">
			<div className="flex flex-wrap items-center gap-2">
				{/* The group's own name carries the label for assistive tech. */}
				<span aria-hidden="true" className="text-muted-foreground text-xs">
					Use for
				</span>
				<SegmentedToggle<CredentialVersionScopeValue>
					ariaLabel="Use this credential for"
					field
					options={[
						{ value: 'pinned', label: `Version ${version}` },
						{ value: 'any', label: 'Any version' },
					]}
					value={value}
					onChange={onChange}
				/>
			</div>
			{value === 'any' && (
				<p
					className="text-muted-foreground text-xs"
					data-testid="credential-version-any-note"
				>
					Covers every version of this API — any access rules you add apply to future
					versions too.
				</p>
			)}
		</div>
	);
}
