"use client";

import type { AnchorHTMLAttributes, ButtonHTMLAttributes, ReactNode } from "react";

/**
 * The button scale. `primary` (navy) is reserved for the one committing action
 * on a view; everything else is `secondary` or `ghost`, so a page never has two
 * things competing to be pressed. Gold appears only as accent, never as a fill.
 */
type Variant = "primary" | "secondary" | "ghost" | "danger" | "onDark";

const VARIANTS: Record<Variant, string> = {
  primary:
    "bg-ink-900 text-white ring-1 ring-ink-900 shadow-sm hover:bg-ink-800 hover:ring-ink-800 disabled:bg-ink-100 disabled:text-ink-400 disabled:ring-ink-100 disabled:shadow-none",
  secondary:
    "bg-white text-ink-800 ring-1 ring-ink-200 shadow-sm hover:bg-ink-50 hover:ring-ink-300 disabled:text-ink-300 disabled:shadow-none",
  ghost: "text-ink-600 hover:bg-ink-50 hover:text-ink-900 disabled:text-ink-300",
  danger: "bg-white text-red-700 ring-1 ring-red-200 shadow-sm hover:bg-red-50 disabled:text-red-300",
  onDark:
    "bg-white/10 text-white ring-1 ring-white/25 backdrop-blur hover:bg-white/20 disabled:text-white/40",
};

const SIZES = {
  sm: "px-2.5 py-1.5 text-xs gap-1.5",
  md: "px-4 py-2.5 text-sm gap-2",
  lg: "px-5 py-3 text-sm gap-2",
};

const BUTTON_BASE =
  "inline-flex items-center justify-center rounded-lg font-medium transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-gold-500 disabled:cursor-not-allowed";

export function Button({
  variant = "secondary",
  size = "md",
  loading,
  icon,
  children,
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: keyof typeof SIZES;
  loading?: boolean;
  icon?: ReactNode;
}) {
  return (
    <button
      type="button"
      {...props}
      disabled={props.disabled || loading}
      className={`${BUTTON_BASE} ${SIZES[size]} ${VARIANTS[variant]} ${className}`}
    >
      {loading ? <Spinner /> : icon}
      {children}
    </button>
  );
}

/** Anchor styled as a button — for real navigation and downloads. */
export function LinkButton({
  variant = "secondary",
  size = "md",
  icon,
  children,
  className = "",
  ...props
}: AnchorHTMLAttributes<HTMLAnchorElement> & {
  variant?: Variant;
  size?: keyof typeof SIZES;
  icon?: ReactNode;
}) {
  return (
    <a {...props} className={`${BUTTON_BASE} ${SIZES[size]} ${VARIANTS[variant]} ${className}`}>
      {icon}
      {children}
    </a>
  );
}

/** Square button for a bare icon. `label` is required — it's the only a11y name. */
export function IconButton({
  label,
  variant = "ghost",
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { label: string; variant?: Variant }) {
  return (
    <button
      type="button"
      {...props}
      aria-label={label}
      title={label}
      className={`${BUTTON_BASE} h-9 w-9 shrink-0 ${VARIANTS[variant]}`}
    >
      {children}
    </button>
  );
}

export function Spinner({ className = "h-3.5 w-3.5" }: { className?: string }) {
  return <span className={`${className} animate-spin rounded-full border-2 border-current/30 border-t-current`} />;
}

/** Uppercase kicker above a heading, with the gold rule that ties the system together. */
export function Eyebrow({ children }: { children: ReactNode }) {
  return (
    <p className="eyebrow flex items-center gap-2 text-gold-600">
      <span className="h-px w-5 bg-gold-400" aria-hidden />
      {children}
    </p>
  );
}

export function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon?: ReactNode;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center rounded-xl border border-dashed border-ink-200 bg-white/70 px-6 py-16 text-center">
      {icon && (
        <span className="mb-4 flex h-11 w-11 items-center justify-center rounded-full bg-gold-50 text-gold-600 ring-1 ring-gold-100">
          {icon}
        </span>
      )}
      <h3 className="font-display text-lg text-ink-900">{title}</h3>
      <p className="mt-1.5 max-w-sm text-sm leading-relaxed text-ink-500">{description}</p>
      {action && <div className="mt-6">{action}</div>}
    </div>
  );
}

/** Primary call-to-action used to launch a scrape across every portal. */
export function StartButton({
  running,
  starting,
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { running?: boolean; starting?: boolean }) {
  const label = running ? "Run in progress…" : starting ? "Starting…" : children;
  return (
    <Button {...props} variant="primary" size="lg" loading={running || starting} icon={<PlayIcon />}>
      {label}
    </Button>
  );
}


/** Neutral secondary action — the small All / None style controls. */
export function MiniButton({ children, ...props }: ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      {...props}
      className="rounded-md px-2 py-1 text-xs font-medium text-ink-500 transition hover:bg-ink-50 hover:text-ink-900 disabled:cursor-not-allowed disabled:opacity-40"
    >
      {children}
    </button>
  );
}

export function ErrorBanner({ message }: { message: string }) {
  return (
    <div role="alert" className="flex items-start gap-3 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
      <svg viewBox="0 0 20 20" fill="currentColor" className="mt-0.5 h-4 w-4 shrink-0 text-red-500" aria-hidden>
        <path
          fillRule="evenodd"
          d="M10 18a8 8 0 1 0 0-16 8 8 0 0 0 0 16Zm.75-11.25a.75.75 0 0 0-1.5 0v3.5a.75.75 0 0 0 1.5 0v-3.5Zm-.75 7.5a.9.9 0 1 0 0-1.8.9.9 0 0 0 0 1.8Z"
          clipRule="evenodd"
        />
      </svg>
      <span>{message}</span>
    </div>
  );
}

/** White panel grouping one logical step of the configuration form. */
export function Card({
  title,
  description,
  actions,
  children,
}: {
  title?: string;
  description?: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="rounded-xl border border-ink-200/70 bg-white shadow-sm shadow-ink-900/[0.03]">
      {(title || actions) && (
        <header className="flex items-start justify-between gap-4 border-b border-ink-100 px-5 py-4">
          <div>
            {title && <h3 className="font-display text-base text-ink-900">{title}</h3>}
            {description && <p className="mt-0.5 text-xs leading-relaxed text-ink-500">{description}</p>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-1">{actions}</div>}
        </header>
      )}
      <div className="p-5">{children}</div>
    </section>
  );
}

/** Text input with a label, matching the form language of the rest of the app. */
export function Field({
  label,
  hint,
  value,
  onChange,
  disabled,
  placeholder,
}: {
  label: string;
  hint?: string;
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
  placeholder?: string;
}) {
  return (
    <div>
      <label className="mb-1.5 block text-xs font-semibold text-ink-700">{label}</label>
      <input
        type="text"
        value={value}
        disabled={disabled}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-lg border border-ink-200 bg-white px-3 py-2 text-sm text-ink-900 shadow-sm transition placeholder:text-ink-400 focus:border-gold-400 focus:outline-none focus:ring-2 focus:ring-gold-400/25 disabled:cursor-not-allowed disabled:bg-ink-50 disabled:text-ink-400"
      />
      {hint && <p className="mt-1.5 text-xs text-ink-500">{hint}</p>}
    </div>
  );
}

/** Shared table shell: bordered, scrollable, with a quiet header row. */
export function DataTable({
  headers,
  caption,
  children,
}: {
  headers: { label: string; className?: string }[];
  caption?: string;
  children: ReactNode;
}) {
  return (
    <div className="overflow-hidden rounded-xl border border-ink-200/70 bg-white shadow-sm shadow-ink-900/[0.03]">
      {caption && (
        <div className="border-b border-ink-100 px-5 py-4">
          <h3 className="font-display text-base text-ink-900">{caption}</h3>
        </div>
      )}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-ink-200 bg-ink-50/70 text-left text-xs font-semibold text-ink-600">
              {headers.map((h) => (
                <th key={h.label} className={`whitespace-nowrap px-4 py-3 ${h.className ?? ""}`}>
                  {h.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-100">{children}</tbody>
        </table>
      </div>
    </div>
  );
}

/** Run lifecycle badge — shared by the live monitor and the history table. */
export function RunBadge({ status }: { status: "pending" | "running" | "completed" | "failed" }) {
  const map = {
    pending: { cls: "border-ink-200 bg-ink-50 text-ink-600", label: "Queued" },
    running: { cls: "border-gold-300 bg-gold-50 text-gold-700", label: "Running" },
    completed: { cls: "border-emerald-200 bg-emerald-50 text-emerald-700", label: "Completed" },
    failed: { cls: "border-red-200 bg-red-50 text-red-700", label: "Failed" },
  }[status];
  const live = status === "running" || status === "pending";
  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-0.5 text-xs font-medium ${map.cls}`}>
      {live && <span className="status-pulse h-1.5 w-1.5 rounded-full bg-current" />}
      {map.label}
    </span>
  );
}

/** Per-row outcome badge shared by every results table. */
export function DocStatus({ state, title }: { state: "ok" | "partial" | "failed" | "empty"; title?: string }) {
  const map = {
    ok: { cls: "border-emerald-200 bg-emerald-50 text-emerald-700", text: "Complete" },
    partial: { cls: "border-gold-300 bg-gold-50 text-gold-700", text: "Partial" },
    failed: { cls: "border-red-200 bg-red-50 text-red-700", text: "Failed" },
    empty: { cls: "border-ink-200 bg-ink-50 text-ink-500", text: "No documents" },
  }[state];
  return (
    <span className={`inline-block whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-medium ${map.cls}`} title={title}>
      {map.text}
    </span>
  );
}

/** Selection chip used for keywords, codes and filter options. */
export function Chip({
  active,
  mono,
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { active?: boolean; mono?: boolean }) {
  return (
    <button
      type="button"
      {...props}
      aria-pressed={active}
      className={`rounded-full border px-3 py-1 text-xs transition disabled:cursor-not-allowed disabled:opacity-50 ${
        mono ? "font-mono" : ""
      } ${
        active
          ? "border-gold-400 bg-gold-50 font-medium text-gold-700 hover:bg-gold-100"
          : "border-ink-200 bg-white text-ink-600 hover:border-ink-300 hover:bg-ink-50 hover:text-ink-900"
      }`}
    >
      {children}
    </button>
  );
}

/**
 * Sticky action bar holding the run trigger. Anchoring it to the bottom of the
 * viewport keeps the button reachable on long configuration forms.
 */
export function LaunchBar({ summary, children }: { summary: ReactNode; children: ReactNode }) {
  return (
    <div className="sticky bottom-4 z-10 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-ink-200/70 bg-white/95 px-5 py-4 shadow-lg shadow-ink-900/[0.06] backdrop-blur">
      <div className="min-w-0 text-sm text-ink-500">{summary}</div>
      {children}
    </div>
  );
}

function PlayIcon() {
  return (
    <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="currentColor" aria-hidden>
      <path d="M4 3.5v9a.5.5 0 0 0 .77.42l7-4.5a.5.5 0 0 0 0-.84l-7-4.5A.5.5 0 0 0 4 3.5Z" />
    </svg>
  );
}

