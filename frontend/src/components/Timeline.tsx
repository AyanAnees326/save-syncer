import { useEffect, useState } from "react";
import { api } from "../api";
import { absoluteTime, bytes, plural, relativeTime } from "../format";
import { Button, Card, EmptyState, SectionTitle, Spinner } from "./primitives";
import type { Revision, RevisionDetail } from "../types";

function Chip({ children, tone }: { children: string; tone: "head" | "base" | "disk" }) {
  const styles = {
    head: "bg-sky-500/10 text-sky-700 ring-sky-600/25 dark:bg-sky-500/15 dark:text-sky-300 dark:ring-sky-500/30",
    base: "bg-slate-500/10 text-slate-700 ring-slate-500/25 dark:bg-slate-500/15 dark:text-slate-300 dark:ring-slate-500/30",
    disk: "bg-emerald-500/10 text-emerald-700 ring-emerald-600/25 dark:bg-emerald-500/15 dark:text-emerald-300 dark:ring-emerald-500/30",
  };
  return (
    <span
      className={`rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ring-1 ring-inset ${styles[tone]}`}
    >
      {children}
    </span>
  );
}

function Detail({ detail }: { detail: RevisionDetail }) {
  const { added, changed, removed } = detail.diff_vs_disk;
  const differs = added.length + changed.length + removed.length;

  return (
    <div className="border-t border-slate-200 bg-slate-50 px-4 py-3 dark:border-slate-800 dark:bg-slate-950/40">
      <p className="text-xs text-slate-500 dark:text-slate-400">
        {detail.matches_disk ? (
          <span className="text-emerald-700 dark:text-emerald-300">
            This is exactly what is in your save folder right now.
          </span>
        ) : (
          <>
            Restoring this would change {plural(differs, "file")} in your save folder
            {added.length > 0 && ` (${added.length} removed`}
            {added.length > 0 && changed.length > 0 && ", "}
            {changed.length > 0 && `${added.length > 0 ? "" : "("}${changed.length} replaced`}
            {removed.length > 0 &&
              `${added.length > 0 || changed.length > 0 ? ", " : "("}${removed.length} added back`}
            {(added.length > 0 || changed.length > 0 || removed.length > 0) && ")"}.
          </>
        )}
      </p>
      <ul className="mt-3 max-h-56 space-y-1 overflow-y-auto pr-2">
        {detail.files.map((file) => (
          <li key={file.path} className="flex justify-between gap-4 text-[11px]">
            <span className="truncate font-mono text-slate-600 dark:text-slate-300" title={file.path}>
              {changed.includes(file.path) && (
                <span className="text-amber-600 dark:text-amber-400">~ </span>
              )}
              {removed.includes(file.path) && (
                <span className="text-emerald-600 dark:text-emerald-400">+ </span>
              )}
              {file.path}
            </span>
            <span className="shrink-0 text-slate-500" title={absoluteTime(file.mtime)}>
              {bytes(file.size)}
            </span>
          </li>
        ))}
      </ul>
      {added.length > 0 && (
        <p className="mt-2 text-[11px] text-rose-700 dark:text-rose-300">
          Not in this revision, so it would be removed from your save folder:{" "}
          <span className="font-mono">{added.slice(0, 5).join(", ")}</span>
          {added.length > 5 && ` and ${added.length - 5} more`}
        </p>
      )}
    </div>
  );
}

export function Timeline({
  profileId,
  revisions,
  loading,
  busy,
  onRestore,
}: {
  profileId: string;
  revisions: Revision[];
  loading: boolean;
  busy: string | null;
  onRestore: (rev: number) => void;
}) {
  const [open, setOpen] = useState<number | null>(null);
  const [detail, setDetail] = useState<RevisionDetail | null>(null);

  // The expanded panel says what restoring would change, so it has to be reloaded
  // whenever the revisions themselves change - after a restore it would otherwise
  // still be describing the save folder as it was before.
  const signature = revisions
    .map((rev) => `${rev.rev}:${rev.is_head}:${rev.is_base}:${rev.matches_disk}`)
    .join(",");

  useEffect(() => {
    if (open === null) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    api
      .revision(profileId, open)
      .then((loaded) => {
        if (!cancelled) setDetail(loaded);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [open, profileId, signature]);

  const toggle = (rev: number) => {
    setOpen((current) => (current === rev ? null : rev));
    setDetail(null);
  };

  return (
    <Card>
      <SectionTitle
        aside={
          <span className="text-xs text-slate-500">
            Newest first &middot; pick any version to put on this desktop
          </span>
        }
      >
        History
      </SectionTitle>

      {loading && revisions.length === 0 ? (
        <div className="px-4 pb-5 text-xs text-slate-500">Reading the relay...</div>
      ) : revisions.length === 0 ? (
        <EmptyState title="No revisions yet">
          Push from either desktop to create the first one. Every push is kept, so you can
          always come back to an earlier save.
        </EmptyState>
      ) : (
        <ul className="divide-y divide-slate-200 border-t border-slate-200 dark:divide-slate-800 dark:border-slate-800">
          {revisions.map((rev) => (
            <li key={rev.rev}>
              <div className="flex items-center gap-3 px-4 py-3 hover:bg-slate-100/60 dark:hover:bg-slate-800/30">
                <button
                  onClick={() => toggle(rev.rev)}
                  className="flex min-w-0 flex-1 items-center gap-3 text-left"
                  aria-expanded={open === rev.rev}
                >
                  <span className="w-10 shrink-0 font-mono text-sm text-slate-500 dark:text-slate-400">
                    #{rev.rev}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-center gap-2">
                      <span className="text-sm text-slate-800 dark:text-slate-200">
                        {rev.note || <span className="text-slate-500">no note</span>}
                      </span>
                      {rev.is_head && <Chip tone="head">relay head</Chip>}
                      {rev.is_base && <Chip tone="base">your base</Chip>}
                      {rev.matches_disk && <Chip tone="disk">on disk now</Chip>}
                    </span>
                    <span className="mt-0.5 block text-xs text-slate-500">
                      {rev.from_this_machine ? "this desktop" : rev.machine} &middot;{" "}
                      <span title={absoluteTime(rev.created_at)}>
                        {relativeTime(rev.created_at)}
                      </span>{" "}
                      &middot; {plural(rev.file_count, "file")} &middot; {bytes(rev.total_size)}
                    </span>
                  </span>
                </button>
                <Button
                  size="sm"
                  variant={rev.matches_disk ? "ghost" : "secondary"}
                  disabled={busy !== null || rev.matches_disk}
                  title={
                    rev.matches_disk
                      ? "This is already what is on disk"
                      : "Put this version in your save folder"
                  }
                  onClick={() => onRestore(rev.rev)}
                >
                  {busy === `restore:${rev.rev}` && <Spinner />}
                  Restore
                </Button>
              </div>
              {open === rev.rev &&
                (detail && detail.rev === rev.rev ? (
                  <Detail detail={detail} />
                ) : (
                  <div className="border-t border-slate-200 px-4 py-3 text-xs text-slate-500 dark:border-slate-800">
                    Loading file list...
                  </div>
                ))}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
