import { Component, type ErrorInfo, type ReactNode } from 'react';

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    if (typeof console !== 'undefined' && console.error) {
      console.error('[ErrorBoundary]', error, info.componentStack);
    }
    if (
      typeof window !== 'undefined' &&
      typeof (window as unknown as { reportError?: (e: unknown) => void }).reportError === 'function'
    ) {
      try {
        (window as unknown as { reportError: (e: unknown) => void }).reportError(error);
      } catch {
        /* ignore */
      }
    }
  }

  private handleRetry = () => {
    this.setState({ error: null });
  };

  private handleReload = () => {
    window.location.reload();
  };

  render(): ReactNode {
    if (this.state.error) {
      return (
        <div className="grid min-h-screen place-items-center p-6">
          <div className="max-w-sm space-y-4 rounded-xl border border-[var(--color-line)] bg-white p-6 text-center">
            <p className="text-[15px] font-bold">Something went wrong</p>
            <p className="text-[13px] leading-6 text-[var(--color-muted)]">
              {this.state.error.message || 'An unexpected error occurred.'}
            </p>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={this.handleRetry}
                className="flex-1 rounded-lg border border-[var(--color-line)] bg-white py-2 text-[13px]"
              >
                Try again
              </button>
              <button
                type="button"
                onClick={this.handleReload}
                className="flex-1 rounded-lg bg-[var(--color-accent)] py-2 text-[13px] font-bold text-white"
              >
                Reload
              </button>
            </div>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
