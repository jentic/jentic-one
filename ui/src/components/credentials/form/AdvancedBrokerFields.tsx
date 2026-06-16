import { ChevronRight } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { Label } from '@/components/ui/Label';
import { Textarea } from '@/components/ui/Textarea';

/**
 * The collapsible "Advanced broker settings" block. Lets the user
 * override how the broker injects this credential — useful when the
 * spec doesn't declare the auth shape correctly, or when the credential
 * needs to flow on routes that don't match the API's declared
 * `servers[].url`.
 *
 * Two fields:
 *  - `Scheme (JSON)` — overrides the auto-derived `securityScheme`
 *    object. Free-form JSON; parent parses and validates on submit
 *    (so we can show errors inline near the submit button rather than
 *    here). Examples in the placeholder.
 *  - `Routes` — newline-separated list of host:port strings that this
 *    credential should be injected on. Parent splits + filters empty
 *    lines. Useful for self-hosted servers behind custom DNS.
 *
 * Hidden entirely by the parent when `auth_type === 'oauth2'` because
 * Pipedream owns the scheme/routes for broker-managed credentials and
 * any local override would be ignored.
 *
 * Open/closed state is parent-owned so the OAuth case can keep the
 * panel collapsed even after a refresh, and so the form-fields
 * container can re-mount cleanly when switching APIs.
 */
export interface AdvancedBrokerFieldsProps {
	open: boolean;
	onToggle: () => void;
	schemeJson: string;
	onSchemeJsonChange: (next: string) => void;
	routesText: string;
	onRoutesTextChange: (next: string) => void;
}

export function AdvancedBrokerFields({
	open,
	onToggle,
	schemeJson,
	onSchemeJsonChange,
	routesText,
	onRoutesTextChange,
}: AdvancedBrokerFieldsProps) {
	return (
		<div className="border-border rounded-lg border">
			<Button
				variant="ghost"
				type="button"
				className="text-muted-foreground hover:text-foreground flex w-full items-center justify-between px-4 py-3 text-xs font-medium transition-colors"
				onClick={onToggle}
			>
				<span>Advanced broker settings</span>
				<ChevronRight
					className={`h-3.5 w-3.5 transition-transform ${open ? 'rotate-90' : ''}`}
				/>
			</Button>
			{open && (
				<div className="border-border space-y-4 border-t px-4 pt-3 pb-4">
					<p className="text-muted-foreground text-xs">
						Override how the broker injects this credential. Leave blank to use
						spec-based inference.
					</p>
					<div>
						<Label
							htmlFor="cred-scheme"
							className="text-muted-foreground mb-1 block text-xs"
						>
							Scheme (JSON)
						</Label>
						<Textarea
							id="cred-scheme"
							value={schemeJson}
							onChange={(e) => onSchemeJsonChange(e.target.value)}
							rows={4}
							placeholder='{"in":"header","name":"X-API-KEY"}'
							resizable="vertical"
							className="bg-background font-mono text-xs"
						/>
					</div>
					<div>
						<Label
							htmlFor="cred-routes"
							className="text-muted-foreground mb-1 block text-xs"
						>
							Routes (one per line)
						</Label>
						<Textarea
							id="cred-routes"
							value={routesText}
							onChange={(e) => onRoutesTextChange(e.target.value)}
							rows={3}
							placeholder="10.0.0.2:9443"
							resizable="vertical"
							className="bg-background font-mono text-xs"
						/>
					</div>
				</div>
			)}
		</div>
	);
}
