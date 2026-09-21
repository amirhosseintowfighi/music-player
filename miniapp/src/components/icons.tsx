/** Inline icons — no icon package, so nothing extra lands in the bundle. */
import type { SVGProps } from 'react';

type Props = SVGProps<SVGSVGElement> & { size?: number };

function Icon({ size = 22, children, ...rest }: Props & { children: React.ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      {...rest}
    >
      {children}
    </svg>
  );
}

export const HomeIcon = (p: Props) => (
  <Icon {...p}>
    <path d="M3 10.5 12 3l9 7.5" />
    <path d="M5.5 9.5V21h13V9.5" />
  </Icon>
);
export const SearchIcon = (p: Props) => (
  <Icon {...p}>
    <circle cx="11" cy="11" r="7" />
    <path d="m20 20-3.2-3.2" />
  </Icon>
);
export const LibraryIcon = (p: Props) => (
  <Icon {...p}>
    <path d="M4 5v14M9 5v14" />
    <path d="m14 6 5 13" />
  </Icon>
);
export const MoreIcon = (p: Props) => (
  <Icon {...p}>
    <circle cx="5" cy="12" r="1.4" fill="currentColor" />
    <circle cx="12" cy="12" r="1.4" fill="currentColor" />
    <circle cx="19" cy="12" r="1.4" fill="currentColor" />
  </Icon>
);
export const PlayIcon = ({ size = 22, ...p }: Props) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden {...p}>
    <path d="M7 4.5v15l13-7.5z" />
  </svg>
);
export const PauseIcon = ({ size = 22, ...p }: Props) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden {...p}>
    <rect x="6" y="4.5" width="4" height="15" rx="1.4" />
    <rect x="14" y="4.5" width="4" height="15" rx="1.4" />
  </svg>
);
export const NextIcon = ({ size = 22, ...p }: Props) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden {...p}>
    <path d="M6 5.5v13l9-6.5z" />
    <rect x="16" y="5.5" width="2.6" height="13" rx="1.2" />
  </svg>
);
export const PrevIcon = ({ size = 22, ...p }: Props) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden {...p}>
    <path d="M18 5.5v13L9 12z" />
    <rect x="5.4" y="5.5" width="2.6" height="13" rx="1.2" />
  </svg>
);
export const ShuffleIcon = (p: Props) => (
  <Icon {...p}>
    <path d="M17 4h4v4M21 4l-6.5 6.5M3 20l7-7M17 20h4v-4M21 20 14.5 13.5M3 4l4 4" />
  </Icon>
);
export const RepeatIcon = (p: Props) => (
  <Icon {...p}>
    <path d="M4 12V9a4 4 0 0 1 4-4h9" />
    <path d="m14 2 3 3-3 3" />
    <path d="M20 12v3a4 4 0 0 1-4 4H7" />
    <path d="m10 22-3-3 3-3" />
  </Icon>
);
export const HeartIcon = ({ filled, ...p }: Props & { filled?: boolean }) => (
  <Icon {...p} fill={filled ? 'currentColor' : 'none'}>
    <path d="M12 20s-7-4.35-7-9.2A4 4 0 0 1 12 8a4 4 0 0 1 7 2.8C19 15.65 12 20 12 20Z" />
  </Icon>
);
export const PlusIcon = (p: Props) => (
  <Icon {...p}>
    <path d="M12 5v14M5 12h14" />
  </Icon>
);
export const ChevronIcon = (p: Props) => (
  <Icon {...p}>
    <path d="m9 5 7 7-7 7" />
  </Icon>
);
export const CloseIcon = (p: Props) => (
  <Icon {...p}>
    <path d="m6 6 12 12M18 6 6 18" />
  </Icon>
);
export const QueueIcon = (p: Props) => (
  <Icon {...p}>
    <path d="M4 7h11M4 12h11M4 17h7" />
    <path d="M18 9v8" />
    <circle cx="19.5" cy="17.5" r="1.8" />
  </Icon>
);
export const ClockIcon = (p: Props) => (
  <Icon {...p}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M12 7.5V12l3 2" />
  </Icon>
);
export const SpeedIcon = (p: Props) => (
  <Icon {...p}>
    <path d="M4 15a8 8 0 1 1 16 0" />
    <path d="m12 14 4-4" />
  </Icon>
);
export const RadioIcon = (p: Props) => (
  <Icon {...p}>
    <rect x="3" y="8" width="18" height="12" rx="3" />
    <path d="m16 3-7 4" />
    <circle cx="8" cy="14" r="2.4" />
    <path d="M15 12h3M15 16h3" />
  </Icon>
);
export const DownloadIcon = (p: Props) => (
  <Icon {...p}>
    <path d="M12 4v11" />
    <path d="m7.5 11 4.5 4.5L16.5 11" />
    <path d="M5 19h14" />
  </Icon>
);
export const SendIcon = (p: Props) => (
  <Icon {...p}>
    <path d="M21 4 3 11l7 2.5L12.5 21z" />
  </Icon>
);

