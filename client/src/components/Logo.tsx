/** The mark: stacked strata (a data store) under a gold collection arc. */
export default function Logo({ className = "h-9 w-9" }: { className?: string }) {
  return (
    <span className={`${className} flex shrink-0 items-center justify-center rounded-lg bg-ink-900`}>
      <svg viewBox="0 0 24 24" className="h-[55%] w-[55%]" fill="none" aria-hidden>
        <path
          d="M4 7c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3Z"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinejoin="round"
          className="text-white"
        />
        <path
          d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinejoin="round"
          className="text-white/70"
        />
        <path
          d="M4 7v10c0 1.7 3.6 3 8 3s8-1.3 8-3V7"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinejoin="round"
          className="text-gold-400"
        />
      </svg>
    </span>
  );
}
