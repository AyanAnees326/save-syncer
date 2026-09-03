import type { ReactNode } from "react";
import { useEffect } from "react";
import { useTheme } from "../theme";
import type { SyncState } from "../types";

/**
 * One place that decides how each sync state looks, so the whole UI agrees. Status
 * colors carry meaning (success/warning/danger/...) so they stay fixed hues across
 * themes - only the exact shade changes, via light-default + dark: pairs, so text
 * stays readable on both a white and a near-black surface.
 */
export const STATE_STYLE: Record<SyncState, { label: string; dot: string; chip: string; panel: string }> = {
  in_sync: {
    label: "In sync",
    dot: "bg-emerald-500 dark:bg-emerald-400",
    chip: "bg-emerald-500/10 text-emerald-700 ring-emerald-600/20 dark:bg-emerald-500/15 dark:text-emerald-300 dark:ring-emerald-500/30",
    panel: "border-emerald-600/25 bg-emerald-500/5 dark:border-emerald-500/30 dark:bg-emerald-500/5",
  },
  local_ahead: {
    label: "This desktop is ahead",
    dot: "bg-amber-500 dark:bg-amber-400",
    chip: "bg-amber-500/10 text-amber-700 ring-amber-600/20 dark:bg-amber-500/15 dark:text-amber-300 dark:ring-amber-500/30",
    panel: "border-amber-600/25 bg-amber-500/5 dark:border-amber-500/30 dark:bg-amber-500/5",
  },
  remote_ahead: {
    label: "Relay is ahead",
    dot: "bg-sky-500 dark:bg-sky-400",
    chip: "bg-sky-500/10 text-sky-700 ring-sky-600/20 dark:bg-sky-500/15 dark:text-sky-300 dark:ring-sky-500/30",
    panel: "border-sky-600/25 bg-sky-500/5 dark:border-sky-500/30 dark:bg-sky-500/5",
  },
  conflict: {
    label: "Conflict",
    dot: "bg-rose-500 dark:bg-rose-400",
    chip: "bg-rose-500/10 text-rose-700 ring-rose-600/20 dark:bg-rose-500/15 dark:text-rose-300 dark:ring-rose-500/30",
    panel: "border-rose-600/30 bg-rose-500/5 dark:border-rose-500/40 dark:bg-rose-500/5",
  },
  unlinked: {
    label: "Not linked yet",
    dot: "bg-violet-500 dark:bg-violet-400",
    chip: "bg-violet-500/10 text-violet-700 ring-violet-600/20 dark:bg-violet-500/15 dark:text-violet-300 dark:ring-violet-500/30",
    panel: "border-violet-600/25 bg-violet-500/5 dark:border-violet-500/30 dark:bg-violet-500/5",
  },
  no_remote: {
    label: "Relay is empty",
    dot: "bg-slate-400 dark:bg-slate-500",
    chip: "bg-slate-500/10 text-slate-700 ring-slate-500/25 dark:bg-slate-500/15 dark:text-slate-300 dark:ring-slate-500/30",
    panel: "border-slate-400/40 bg-slate-500/5 dark:border-slate-600/40 dark:bg-slate-500/5",
  },
  local_missing: {
    label: "Save folder missing",
    dot: "bg-orange-500 dark:bg-orange-400",
    chip: "bg-orange-500/10 text-orange-700 ring-orange-600/20 dark:bg-orange-500/15 dark:text-orange-300 dark:ring-orange-500/30",
    panel: "border-orange-600/25 bg-orange-500/5 dark:border-orange-500/30 dark:bg-orange-500/5",
  },
};

export const ACTION_LABEL: Record<string, string> = {
  push: "Push to relay",
  pull: "Pull from relay",
  use_local: "Keep this desktop",
  use_remote: "Take the relay copy",
  keep_both: "Keep both",
};

export function StatePill({ state }: { state: SyncState }) {
  const style = STATE_STYLE[state];
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ring-1 ring-inset ${style.chip}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${style.dot}`} />
      {style.label}
    </span>
  );
}

type ButtonProps = {
  children: ReactNode;
  onClick?: () => void;
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md";
  disabled?: boolean;
  title?: string;
  type?: "button" | "submit";
};

// primary uses the user's chosen accent (CSS vars from index.css); everything else
// is a fixed neutral so the accent reads clearly as "the interactive color".
const VARIANTS: Record<string, string> = {
  primary:
    "bg-[var(--accent)] text-[var(--accent-text)] hover:bg-[var(--accent-hover)] focus-visible:outline-[var(--accent)]",
  secondary:
    "bg-slate-100 text-slate-900 ring-1 ring-inset ring-slate-300 hover:bg-slate-200 focus-visible:outline-slate-400 " +
    "dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700 dark:hover:bg-slate-700 dark:focus-visible:outline-slate-500",
  ghost:
    "text-slate-600 hover:bg-slate-100 hover:text-slate-900 focus-visible:outline-slate-400 " +
    "dark:text-slate-300 dark:hover:bg-slate-800 dark:hover:text-slate-100 dark:focus-visible:outline-slate-600",
  danger: "bg-rose-600 text-white hover:bg-rose-500 focus-visible:outline-rose-500 dark:bg-rose-600/90",
};

export function Button({
  children,
  onClick,
  variant = "secondary",
  size = "md",
  disabled,
  title,
  type = "button",
}: ButtonProps) {
  const { accent } = useTheme();
  return (
    <button
      // Remounts this one node when the accent changes. Its background comes from
      // bg-[var(--accent)]; some browser engines pin an existing element's resolved
      // value for a var()-driven color and never re-resolve it after the variable
      // changes, even though the variable itself reads correctly afterwards. A fresh
      // node always resolves it correctly on first paint, so this sidesteps the bug
      // without remounting anything else (an open modal containing this button, for
      // instance, must not close just because its Save button re-keys).
      key={variant === "primary" ? accent : undefined}
      type={type}
      title={title}
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex items-center justify-center gap-2 rounded-lg font-medium transition
        focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2
        disabled:cursor-not-allowed disabled:opacity-40
        ${size === "sm" ? "px-2.5 py-1 text-xs" : "px-3.5 py-2 text-sm"}
        ${VARIANTS[variant]}`}
    >
      {children}
    </button>
  );
}

export function Card({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`rounded-xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900/60 ${className}`}
    >
      {children}
    </section>
  );
}

export function SectionTitle({ children, aside }: { children: ReactNode; aside?: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 px-4 py-3">
      <h2 className="text-sm font-semibold tracking-wide text-slate-700 uppercase dark:text-slate-200">
        {children}
      </h2>
      {aside}
    </div>
  );
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-0.5 text-sm text-slate-900 dark:text-slate-200" title={hint}>
        {value}
      </div>
    </div>
  );
}

export function PathText({ children }: { children: ReactNode }) {
  return (
    <code
      className="break-all font-mono text-xs text-slate-500 dark:text-slate-400"
      title={String(children)}
    >
      {children}
    </code>
  );
}

export function Modal({
  title,
  children,
  onClose,
  wide,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-500/30 p-6 backdrop-blur-sm dark:bg-slate-950/80"
      onClick={onClose}
    >
      <div
        className={`mt-10 w-full ${wide ? "max-w-3xl" : "max-w-lg"} rounded-xl border border-slate-200 bg-white shadow-2xl dark:border-slate-800 dark:bg-slate-900`}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-3.5 dark:border-slate-800">
          <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">{title}</h2>
          <button
            onClick={onClose}
            className="rounded-md px-2 py-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-200"
            aria-label="Close"
          >
            &#10005;
          </button>
        </div>
        <div className="px-5 py-4">{children}</div>
      </div>
    </div>
  );
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: ReactNode;
  children: ReactNode;
}) {
  return (
    <label className="block">
      <span className="text-xs font-medium text-slate-700 dark:text-slate-300">{label}</span>
      <div className="mt-1.5">{children}</div>
      {hint && <p className="mt-1.5 text-xs text-slate-500">{hint}</p>}
    </label>
  );
}

export const inputClass =
  "w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 " +
  "placeholder:text-slate-400 focus:border-[var(--accent)] focus:outline-none " +
  "focus:ring-2 focus:ring-[var(--accent-ring)] " +
  "dark:border-slate-700 dark:bg-slate-950/60 dark:text-slate-100 dark:placeholder:text-slate-600";

export function Spinner() {
  return (
    <span className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-slate-300 border-t-transparent dark:border-slate-500" />
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="px-4 py-10 text-center">
      <p className="text-sm font-medium text-slate-700 dark:text-slate-300">{title}</p>
      {children && <p className="mx-auto mt-1.5 max-w-md text-xs text-slate-500">{children}</p>}
    </div>
  );
}
