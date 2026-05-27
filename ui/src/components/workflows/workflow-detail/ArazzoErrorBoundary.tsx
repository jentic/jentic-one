import { Component } from 'react';
import type { ReactNode } from 'react';
import { AlertTriangle } from 'lucide-react';

interface Props {
	/** Reset error state when the user navigates to a different slug. */
	slug?: string;
	children: ReactNode;
}

interface State {
	error: Error | null;
}

/**
 * Catches render errors thrown inside the Arazzo viewer (an external
 * library that occasionally barfs on malformed documents) and shows a
 * graceful fallback so the whole workflow detail page doesn't crash.
 *
 * Error state resets when `slug` changes so navigating to a different
 * workflow doesn't get stuck on a stale error from the previous one.
 */
export class ArazzoErrorBoundary extends Component<Props, State> {
	state: State = { error: null };

	static getDerivedStateFromError(error: Error): State {
		return { error };
	}

	componentDidUpdate(prevProps: Props) {
		if (prevProps.slug !== this.props.slug && this.state.error) {
			this.setState({ error: null });
		}
	}

	render() {
		if (this.state.error) {
			return (
				<div className="border-border bg-muted rounded-xl border p-8 text-center">
					<AlertTriangle className="text-warning mx-auto mb-3 h-8 w-8" />
					<p className="text-foreground mb-1 text-sm font-medium">
						Workflow visualization failed to render
					</p>
					<p className="text-muted-foreground text-xs">{this.state.error.message}</p>
				</div>
			);
		}
		return this.props.children;
	}
}
