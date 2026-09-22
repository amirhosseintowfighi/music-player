import { useEffect, useRef, useState } from 'react';

import { loginWithPassword, loginWithWidget } from '@/api/client';
import { Button, Card } from '@/components/ui';

declare global {
  interface Window {
    onTelegramAuth?: (user: Record<string, unknown>) => void;
  }
}

/**
 * Two ways in, one door.
 *
 * Username + password is the default: it works on any device and does not depend on
 * Telegram, on the bot, or on the one login domain BotFather allows per bot. The
 * Telegram widget stays below it for whoever prefers it — its payload is untrusted
 * either way, the server re-verifies the HMAC before minting anything. The panel
 * never decides who is an admin.
 */
export function Login({ onLoggedIn }: { onLoggedIn: () => void }) {
  const [error, setError] = useState('');

  return (
    <div className="grid min-h-screen place-items-center p-6">
      <Card className="w-full max-w-sm space-y-4">
        <h1 className="text-center text-[18px] font-bold">پنل مدیریت TMusic</h1>
        <PasswordLogin onLoggedIn={onLoggedIn} onError={setError} />
        <TelegramLogin onLoggedIn={onLoggedIn} onError={setError} />
        {error && <p className="text-center text-[12px] text-red-600">{error}</p>}
        <DevLogin onLoggedIn={onLoggedIn} onError={setError} />
      </Card>
    </div>
  );
}

interface DoorProps {
  onLoggedIn: () => void;
  onError: (message: string) => void;
}

function PasswordLogin({ onLoggedIn, onError }: DoorProps) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    onError('');
    loginWithPassword(username, password)
      .then(onLoggedIn)
      .catch(() => onError('نام کاربری یا رمز عبور درست نیست.'))
      .finally(() => setBusy(false));
  };

  const field =
    'w-full rounded-lg border border-[var(--color-line)] bg-transparent p-2 text-[14px]';

  return (
    <form onSubmit={submit} className="space-y-2">
      <input
        aria-label="username"
        placeholder="نام کاربری"
        autoComplete="username"
        dir="ltr"
        value={username}
        onChange={(event) => setUsername(event.target.value)}
        className={field}
      />
      <input
        aria-label="password"
        type="password"
        placeholder="رمز عبور"
        autoComplete="current-password"
        dir="ltr"
        value={password}
        onChange={(event) => setPassword(event.target.value)}
        className={field}
      />
      <Button type="submit" tone="primary" disabled={busy}>
        {busy ? '…' : 'ورود'}
      </Button>
    </form>
  );
}

function TelegramLogin({ onLoggedIn, onError }: DoorProps) {
  const holder = useRef<HTMLDivElement | null>(null);
  const botUsername = import.meta.env.VITE_BOT_USERNAME as string | undefined;

  useEffect(() => {
    window.onTelegramAuth = (user) => {
      loginWithWidget(user)
        .then(onLoggedIn)
        .catch((err: unknown) => onError(err instanceof Error ? err.message : 'ورود ناموفق بود'));
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
  }, [botUsername, onLoggedIn, onError]);

  if (!botUsername) return null;
  return (
    <details className="text-[12px] text-[var(--color-muted)]">
      <summary className="cursor-pointer text-center">ورود با تلگرام</summary>
      <div ref={holder} className="mt-3 flex justify-center" />
      <p className="mt-2 text-center">
        نیاز دارد که در BotFather دستور <code dir="ltr">/setdomain</code> روی همین دامنه تنظیم شده
        باشد.
      </p>
    </details>
  );
}

/** Dev-only shortcut: paste a signed payload instead of hosting the widget locally. */
function DevLogin({ onLoggedIn, onError }: DoorProps) {
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
