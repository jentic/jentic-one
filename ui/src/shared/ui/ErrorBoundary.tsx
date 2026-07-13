import { Component } from 'react';
import type { ReactNode, ErrorInfo } from 'react';
import { AlertTriangle } from 'lucide-react';
import { Button } from '@/shared/ui/Button';

interface Props {
	children: ReactNode;
	fallback?: ReactNode;
	resetKey?: string;
}

interface State {
	error: Error | null;
	prevResetKey?: string;
}

export class ErrorBoundary extends Component<Props, State> {
	state: State = { error: null, prevResetKey: this.props.resetKey };

	static getDerivedStateFromError(error: Error): Partial<State> {
		return { error };
	}

	static getDerivedStateFromProps(props: Props, state: State): Partial<State> | null {
		if (props.resetKey !== state.prevResetKey) {
			return { error: null, prevResetKey: props.resetKey };
		}
		return null;
	}

	componentDidCatch(error: Error, info: ErrorInfo) {
		console.error('[ErrorBoundary]', error, info.componentStack);
	}

	render() {
		if (this.state.error) {
			if (this.props.fallback) return this.props.fallback;

			return (
				<div
					role="alert"
					className="border-border bg-muted rounded-xl border p-8 text-center"
				>
					<AlertTriangle className="text-warning mx-auto mb-3 h-8 w-8" />
					<p className="text-foreground mb-1 text-sm font-medium">Something went wrong</p>
					<p className="text-muted-foreground mb-4 text-xs">{this.state.error.message}</p>
					<Button
						variant="secondary"
						size="sm"
						onClick={() => this.setState({ error: null })}
					>
						Try again
					</Button>
				</div>
			);
		}

		return this.props.children;
	}
}
