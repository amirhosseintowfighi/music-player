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

/** Two listeners: the Jam. */
export const JamIcon = (p: Props) => (
  <Icon {...p}>
    <circle cx="9" cy="8" r="3.2" />
    <path d="M3.5 19.5a5.5 5.5 0 0 1 11 0" />
    <path d="M15.5 5.2a3.2 3.2 0 0 1 0 6.1" />
    <path d="M17.5 14.4a5.5 5.5 0 0 1 3 5.1" />
  </Icon>
);
export const LinkIcon = (p: Props) => (
  <Icon {...p}>
    <path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1" />
    <path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1" />
  </Icon>
);
/** A speech bubble with lines: lyrics. */
export const LyricsIcon = (p: Props) => (
  <Icon {...p}>
    <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v8a2.5 2.5 0 0 1-2.5 2.5H10l-4.5 4v-4A2.5 2.5 0 0 1 3 13.5" />
    <path d="M8 8h8M8 11.5h5" />
  </Icon>
);
export const SparkleIcon = (p: Props) => (
  <Icon {...p}>
    <path d="M12 3.5 13.8 9l5.7 1.9-5.7 1.9L12 18.5l-1.8-5.7L4.5 10.9 10.2 9z" />
    <path d="M19 3v3M17.5 4.5h3" />
  </Icon>
);
export const FolderIcon = (p: Props) => (
  <Icon {...p}>
    <path d="M3.5 7.5A2 2 0 0 1 5.5 5.5h4l2 2h7a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z" />
  </Icon>
);
export const EyeOffIcon = (p: Props) => (
  <Icon {...p}>
    <path d="M3 3l18 18" />
    <path d="M10.6 5.2A9.6 9.6 0 0 1 12 5c5 0 8.5 4.5 9.5 7a13 13 0 0 1-2.6 3.8M6.3 6.8A13 13 0 0 0 2.5 12c1 2.5 4.5 7 9.5 7a9.4 9.4 0 0 0 4.3-1" />
    <path d="M9.9 9.9a3 3 0 0 0 4.2 4.2" />
  </Icon>
);
export const MicIcon = (p: Props) => (
  <Icon {...p}>
    <rect x="9" y="3" width="6" height="11" rx="3" />
    <path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21" />
  </Icon>
);
export const BlendIcon = (p: Props) => (
  <Icon {...p}>
    <circle cx="9" cy="12" r="5.5" />
    <circle cx="15" cy="12" r="5.5" />
  </Icon>
);
