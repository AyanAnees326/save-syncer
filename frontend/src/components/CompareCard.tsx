import { Button, Spinner } from "./primitives";
import { absoluteTime, bytes, plural, relativeTime } from "../format";
import type { Action, Diff, ManifestSummary, Status } from "../types";

function Side({
  title,
  subtitle,
  manifest,
  tone,
}: {
  title: string;
  subtitle: string;
  manifest: ManifestSummary | null;
  tone: "local" | "remote";
}) {
  const ring =
    tone === "local" ? "border-amber-600/30 dark:border-amber-500/30" : "border-sky-600/30 dark:border-sky-500/30";
  const label = tone === "local" ? "text-amber-700 dark:text-amber-300" : "text-sky-700 dark:text-sky-300";
  return (
    <div className={`rounded-lg border ${ring} bg-slate-50 dark:bg-slate-950/40 p-4`}>
      <h4 className={`text-xs font-semibold uppercase tracking-wide ${label}`}>{title}</h4>
      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{subtitle}</p>
      {manifest ? (
        <dl className="mt-3 space-y-1.5 text-xs">
          <div className="flex justify-between gap-3">
            <dt className="text-slate-500">Files</dt>
            <dd className="text-slate-800 dark:text-slate-200">{plural(manifest.file_count, "file")}</dd>
          </div>
          <div className="flex justify-between gap-3">
            <dt className="text-slate-500">Size</dt>
            <dd className="text-slate-800 dark:text-slate-200">{bytes(manifest.total_size)}</dd>
          </div>
          <div className="flex justify-between gap-3">
            <dt className="text-slate-500">Newest file</dt>
            <dd className="text-slate-800 dark:text-slate-200" title={absoluteTime(manifest.newest_mtime)}>
              {relativeTime(manifest.newest_mtime)}
            </dd>
          </div>
          {manifest.note && (
            <div className="flex justify-between gap-3">
              <dt className="text-slate-500">Note</dt>
              <dd
                className="max-w-[60%] truncate text-right text-slate-800 dark:text-slate-200"
                title={manifest.note}
              >
                {manifest.note}
              </dd>
            </div>
          )}
        </dl>
      ) : (
        <p className="mt-3 text-xs text-slate-500">Nothing here.</p>
      )}
    </div>
  );
}

function DiffList({ diff }: { diff: Diff }) {
  const groups: [string, string[], string][] = [
    ["Only on this desktop", diff.added, "text-emerald-700 dark:text-emerald-300"],
    ["Different on each side", diff.changed, "text-amber-700 dark:text-amber-300"],
    ["Only on the relay", diff.removed, "text-rose-700 dark:text-rose-300"],
  ];
  const shown = groups.filter(([, files]) => files.length > 0);
  if (shown.length === 0) return null;

  return (
    <div className="mt-4 grid gap-3 sm:grid-cols-3">
      {shown.map(([label, files, colour]) => (
        <div key={label}>
          <h5 className={`text-[11px] font-semibold uppercase tracking-wide ${colour}`}>
            {label} ({files.length})
          </h5>
          <ul className="mt-1.5 space-y-0.5">
            {files.slice(0, 8).map((file) => (
              <li
                key={file}
                className="truncate font-mono text-[11px] text-slate-500 dark:text-slate-400"
                title={file}
              >
                {file}
              </li>
            ))}
            {files.length > 8 && (
              <li className="text-[11px] text-slate-400 dark:text-slate-600">
                and {files.length - 8} more
              </li>
            )}
          </ul>
        </div>
      ))}
    </div>
  );
}

/**
 * Shown for conflicts and for a desktop that has never synced this profile: the two
 * candidate versions side by side, with the choice attached to the evidence rather
 * than floating in a banner somewhere else.
 */
export function CompareCard({
  status,
  machine,
  busy,
  onAction,
}: {
  status: Status;
  machine: string;
  busy: string | null;
  onAction: (action: Action) => void;
}) {
  const conflict = status.state === "conflict";

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5 dark:border-slate-800 dark:bg-slate-900/60">
      <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
        {conflict ? "Both sides changed - choose one" : "Which copy should this desktop keep?"}
      </h3>
      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
        Whichever you pick, the other version stays recoverable: relay revisions are never
        deleted, and this desktop is backed up before anything is overwritten.
      </p>

      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <Side
          tone="local"
          title="This desktop"
          subtitle={machine}
          manifest={status.local}
        />
        <Side
          tone="remote"
          title="The relay"
          subtitle={
            status.remote
              ? `Revision ${status.remote_rev} from ${status.remote.machine}, ${relativeTime(
                  status.remote.created_at,
                )}`
              : "No revisions yet"
          }
          manifest={status.remote}
        />
      </div>

      {status.diff && <DiffList diff={status.diff} />}

      <div className="mt-5 flex flex-wrap gap-2 border-t border-slate-200 pt-4 dark:border-slate-800">
        <Button
          variant="primary"
          disabled={busy !== null}
          onClick={() => onAction("use_local")}
        >
          {busy === "use_local" && <Spinner />}
          Keep this desktop
        </Button>
        <Button variant="secondary" disabled={busy !== null} onClick={() => onAction("use_remote")}>
          {busy === "use_remote" && <Spinner />}
          Take the relay copy
        </Button>
        {conflict && (
          <Button variant="ghost" disabled={busy !== null} onClick={() => onAction("keep_both")}>
            {busy === "keep_both" && <Spinner />}
            Keep both
          </Button>
        )}
      </div>
      {conflict && (
        <p className="mt-2 text-[11px] text-slate-500">
          Keep both publishes this desktop and also writes the relay copy into your backups
          folder so you can open it.
        </p>
      )}
    </section>
  );
}
