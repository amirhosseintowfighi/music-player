import { Component, type ErrorInfo, type ReactNode } from 'react';

import { Card } from '@/components/ui';

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('[admin:ErrorBoundary]', error, info);
    if (typeof window !== 'undefined' && typeof (window as unknown as { reportError?: (e: unknown) => void }).reportError === 'function') {
      (window as unknown as { reportError: (e: unknown) => void }).reportError(error);
    }
  }

  private reset = () => this.setState({ hasError: false, error: null });

  render() {
    if (this.state.hasError) {
      return (
        <div className="grid min-h-[50vh] place-items-center p-6">
          <Card className="max-w-md space-y-3 text-center">
            <p className="text-[14px] font-bold">مشکلی پیش آمد</p>
            <p className="text-[12px] leading-6 text-[var(--color-muted)]">{this.state.error?.message ?? 'Unexpected error'}</p>
            <div className="flex gap-2 pt-1">
              <button
                type="button"
                onClick={this.reset}
                className="flex-1 rounded-lg bg-[var(--color-accent)] px-3 py-2 text-[13px] font-medium text-white"
              >
                تلاش دوباره
              </button>
              <button
                type="button"
                onClick={() => window.location.reload()}
                className="flex-1 rounded-lg border border-[var(--color-line)] bg-white px-3 py-2 text-[13px]"
              >
                بارگذاری مجدد
              </button>
            </div>
          </Card>
        </div>
      );
    }
    return this.props.children;
  }
}

export default ErrorBoundary;
