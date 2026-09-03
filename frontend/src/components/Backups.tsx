import { useState } from "react";
import { absoluteTime, bytes, plural, relativeTime } from "../format";
import { Button, Card, EmptyState, SectionTitle, Spinner } from "./primitives";
import type { Backup } from "../types";

/**
 * The undo list. Every operation that writes to the save folder snapshots it first,
 * so this is the escape hatch for "I picked the wrong side".
 */
export function Backups({
  backups,
  busy,
  onRestore,
}: {
  backups: Backup[];
  busy: string | null;
  onRestore: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);

  return (
    <Card>
      <SectionTitle
        aside={
          <button
            onClick={() => setOpen(!open)}
            className="text-xs text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200"
          >
            {open ? "Hide" : `Show (${backups.length})`}
          </button>
        }
      >
        Local backups
      </SectionTitle>

      {open &&
        (backups.length === 0 ? (
          <EmptyState title="No backups yet">
            One is taken automatically each time something overwrites your save folder.
          </EmptyState>
        ) : (
          <ul className="divide-y divide-slate-200 border-t border-slate-200 dark:divide-slate-800 dark:border-slate-800">
            {backups.map((backup) => (
              <li key={backup.id} className="flex items-center gap-3 px-4 py-2.5">
                <div className="min-w-0 flex-1">
                  <div className="font-mono text-xs text-slate-600 dark:text-slate-300">
                    {backup.id}
                  </div>
                  <div className="text-xs text-slate-500" title={absoluteTime(backup.created_at)}>
                    {relativeTime(backup.created_at)} &middot;{" "}
                    {plural(backup.file_count, "file")} &middot; {bytes(backup.total_size)}
                  </div>
                </div>
                <Button
                  size="sm"
                  disabled={busy !== null}
                  onClick={() => onRestore(backup.id)}
                  title="Put this snapshot back into the save folder"
                >
                  {busy === `backup:${backup.id}` && <Spinner />}
                  Restore
                </Button>
              </li>
            ))}
          </ul>
        ))}
    </Card>
  );
}
