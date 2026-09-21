import { useEffect, useRef, useState } from 'react';

import { loginWithWidget } from '@/api/client';
import { Button, Card } from '@/components/ui';

declare global {
  interface Window {
    onTelegramAuth?: (user: Record<string, unknown>) => void;
  }
}

/**
 * Telegram Login Widget.
 *
 * The widget is a third-party script, so the payload it hands back is untrusted: the
 * server re-verifies its HMAC against the bot token before minting anything. The
 * panel never decides who is an admin.
 */
export function Login({ onLoggedIn }: { onLoggedIn: () => void }) {
  const holder = useRef<HTMLDivElement | null>(null);
  const [error, setError] = useState('');
  const botUsername = import.meta.env.VITE_BOT_USERNAME as string | undefined;

  useEffect(() => {
    window.onTelegramAuth = (user) => {
      loginWithWidget(user)
        .then(onLoggedIn)
        .catch((err: unknown) =>
          setError(err instanceof Error ? err.message : 'ورود ناموفق بود'),
        );
    };
    if (!holder.current || !botUsername) return;
    const script = document.createElement('script');
    script.src = 'https://telegram.org/js/telegram-widget.js?22';
    script.async = true;
    script.setAttribute('data-telegram-login', botUsername);
    script.setAttribute('data-size', 'large');
    script.setAttribute('data-onauth', 'onTelegramAuth(user)');
    script.setAttribute('data-request-access', 'write');
    holder.current.appendChild(script);
    return () => {
      delete window.onTelegramAuth;
    };
  }, [botUsername, onLoggedIn]);

  return (
    <div className="grid min-h-screen place-items-center p-6">
      <Card className="w-full max-w-sm space-y-4 text-center">
        <h1 className="text-[18px] font-bold">پنل مدیریت TMusic</h1>
        <p className="text-[13px] text-[var(--color-muted)]">
          با همان اکانت تلگرامی وارد شو که در فهرست مدیران ثبت شده است.
        </p>
        <div ref={holder} className="flex justify-center" />
        {!botUsername && (
          <p className="text-[12px] text-red-600">VITE_BOT_USERNAME تنظیم نشده است.</p>
        )}
        {error && <p className="text-[12px] text-red-600">{error}</p>}
        <DevLogin onLoggedIn={onLoggedIn} onError={setError} />
      </Card>
    </div>
  );
}

/** Dev-only shortcut: paste a signed payload instead of hosting the widget locally. */
function DevLogin({
  onLoggedIn,
  onError,
}: {
  onLoggedIn: () => void;
  onError: (message: string) => void;
}) {
  const [payload, setPayload] = useState('');
  if (!import.meta.env.DEV) return null;
  return (
    <details className="text-start">
      <summary className="cursor-pointer text-[12px] text-[var(--color-muted)]">
        ورود دستی (فقط در حالت توسعه)
      </summary>
      <textarea
        aria-label="dev-payload"
        value={payload}
        onChange={(event) => setPayload(event.target.value)}
        rows={4}
        placeholder='{"id":1,"first_name":"A","auth_date":0,"hash":"…"}'
        className="mt-2 w-full rounded-lg border border-[var(--color-line)] p-2 font-mono text-[11px]"
      />
      <Button
        onClick={() => {
          try {
            void loginWithWidget(JSON.parse(payload) as Record<string, unknown>)
              .then(onLoggedIn)
              .catch((err: unknown) =>
                onError(err instanceof Error ? err.message : 'ورود ناموفق بود'),
              );
          } catch {
            onError('JSON نامعتبر');
          }
        }}
      >
        ورود
      </Button>
    </details>
  );
}
