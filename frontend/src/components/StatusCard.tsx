import { ACTION_LABEL, Button, PathText, STATE_STYLE, Spinner, StatePill } from "./primitives";
import { bytes, plural, relativeTime } from "../format";
import type { Action, Status } from "../types";

const PRIMARY: Record<string, "primary" | "secondary" | "danger"> = {
  push: "primary",
  pull: "primary",
  use_local: "secondary",
  use_remote: "secondary",
  keep_both: "secondary",
};

export function StatusCard({
  status,
  busy,
  onAction,
}: {
  status: Status;
  busy: string | null;
  onAction: (action: Action) => void;
}) {
  const style = STATE_STYLE[status.state];
  const blocked = status.blocking_processes.length > 0;
  // In a conflict the choice belongs in the comparison panel, next to the evidence.
  const showActions = status.state !== "conflict" && status.state !== "unlinked";

  return (
    <section className={`rounded-xl border p-5 ${style.panel}`}>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <StatePill state={status.state} />
          <p className="mt-3 max-w-2xl text-sm text-slate-700 dark:text-slate-200">{status.message}</p>

          <dl className="mt-4 flex flex-wrap gap-x-6 gap-y-2 text-xs text-slate-500 dark:text-slate-400">
            <div>
              <dt className="inline text-slate-500">Relay revision </dt>
              <dd className="inline font-medium text-slate-700 dark:text-slate-300">
                {status.remote_rev ?? "none"}
              </dd>
            </div>
            <div>
              <dt className="inline text-slate-500">This desktop is based on </dt>
              <dd className="inline font-medium text-slate-700 dark:text-slate-300">
                {status.base_rev ?? "nothing"}
              </dd>
            </div>
            <div>
              <dt className="inline text-slate-500">Last sync </dt>
              <dd className="inline font-medium text-slate-700 dark:text-slate-300">
                {relativeTime(status.last_sync_at)}
              </dd>
            </div>
            {status.local && (
              <div>
                <dt className="inline text-slate-500">On disk </dt>
                <dd className="inline font-medium text-slate-700 dark:text-slate-300">
                  {plural(status.local.file_count, "file")}, {bytes(status.local.total_size)}
                </dd>
              </div>
            )}
          </dl>
        </div>

        {showActions && status.actions.length > 0 && (
          <div className="flex shrink-0 flex-wrap gap-2">
            {status.actions.map((action) => (
              <Button
                key={action}
                variant={PRIMARY[action] ?? "secondary"}
                disabled={busy !== null}
                onClick={() => onAction(action)}
              >
                {busy === action && <Spinner />}
                {ACTION_LABEL[action] ?? action}
              </Button>
            ))}
          </div>
        )}
      </div>

      {blocked && (
        <p className="mt-4 rounded-lg border border-rose-600/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-800 dark:border-rose-500/30 dark:text-rose-200">
          <strong className="font-semibold">
            {status.blocking_processes.join(", ")} is running.
          </strong>{" "}
          Syncing is held back until you close it - a running game rewrites its save files
          while you work.
        </p>
      )}

      <div className="mt-4 grid gap-1 border-t border-slate-900/5 pt-3 dark:border-white/5">
        <div className="flex gap-2 text-xs">
          <span className="w-12 shrink-0 text-slate-500">Save</span>
          <PathText>{status.local_path}</PathText>
        </div>
        <div className="flex gap-2 text-xs">
          <span className="w-12 shrink-0 text-slate-500">
            {status.remote_kind === "cloud" ? "Account" : "Relay"}
          </span>
          {status.remote_kind === "cloud" ? (
            <span className="text-xs text-slate-500 dark:text-slate-400">Your account</span>
          ) : (
            <PathText>{status.relay_path}</PathText>
          )}
        </div>
      </div>
    </section>
  );
}
