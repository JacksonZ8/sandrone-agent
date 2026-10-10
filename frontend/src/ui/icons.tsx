// 线性图标，统一 currentColor 描边；装饰性图标对读屏隐藏。
import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement> & { size?: number };

function Svg({ size = 18, children, ...rest }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...rest}
    >
      {children}
    </svg>
  );
}

export const MenuIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M3.5 6h13M3.5 10h13M3.5 14h13" />
  </Svg>
);
export const BackIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12.5 4 6.5 10l6 6" />
  </Svg>
);
export const DownIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="m5 8 5 5 5-5" />
  </Svg>
);
export const ChevronIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="m8 5 5 5-5 5" />
  </Svg>
);
export const CopyIcon = (p: IconProps) => (
  <Svg {...p}>
    <rect x="7" y="7" width="9" height="9" rx="2" />
    <path d="M13 7V5.5A1.5 1.5 0 0 0 11.5 4h-6A1.5 1.5 0 0 0 4 5.5v6A1.5 1.5 0 0 0 5.5 13H7" />
  </Svg>
);
export const CheckIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="m4.5 10.5 3.5 3.5 7.5-8" />
  </Svg>
);
export const PlayIcon = (p: IconProps) => (
  <Svg {...p} fill="currentColor" stroke="none">
    <path d="M6 3.5v13l10-6.5z" />
  </Svg>
);
export const PauseIcon = (p: IconProps) => (
  <Svg {...p} fill="currentColor" stroke="none">
    <rect x="5" y="4" width="3.5" height="12" rx="1.2" />
    <rect x="11.5" y="4" width="3.5" height="12" rx="1.2" />
  </Svg>
);
export const ReplayIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 10a6 6 0 1 0 1.8-4.3M4 3.5v3.8h3.8" />
  </Svg>
);
export const MicIcon = (p: IconProps) => (
  <Svg {...p}>
    <rect x="7.25" y="2.5" width="5.5" height="10" rx="2.75" />
    <path d="M4 9.5a6 6 0 0 0 12 0M10 15.5v2.5" />
  </Svg>
);
export const SendIcon = (p: IconProps) => (
  <Svg {...p} strokeWidth={1.9}>
    <path d="M10 16V4M5 9l5-5 5 5" />
  </Svg>
);
export const PlusIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M10 4v12M4 10h12" />
  </Svg>
);
export const SearchIcon = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="9" cy="9" r="5" />
    <path d="m13 13 3.5 3.5" />
  </Svg>
);
export const CloseIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="m5 5 10 10M15 5 5 15" />
  </Svg>
);
export const TrashIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 6h12M8 6V4.5h4V6M6 6l.8 10h6.4L14 6" />
  </Svg>
);
export const RegenerateIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M16 10a6 6 0 1 1-1.8-4.3M16 3.5v3.8h-3.8" />
  </Svg>
);
export const MicOffIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M7.25 7.5v2a2.75 2.75 0 0 0 4.7 1.95M12.75 9V5.25a2.75 2.75 0 0 0-5.3-1.05" />
    <path d="M4 9.5a6 6 0 0 0 9.6 4.8M16 9.5a6 6 0 0 1-.4 2.1M10 15.5v2.5M3 3l14 14" />
  </Svg>
);
export const HangUpIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M2.5 11.5c4.2-3.6 10.8-3.6 15 0l-1.6 2.2-3.1-1.2v-2.1a9 9 0 0 0-5.6 0v2.1l-3.1 1.2z" />
  </Svg>
);
