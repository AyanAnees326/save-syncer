import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, subscribeToEvents } from "./api";
import { relativeTime } from "./format";
import { Backups } from "./components/Backups";
import { CompareCard } from "./components/CompareCard";
import { ProfileFormModal, SettingsModal } from "./components/Modals";
import { StatusCard } from "./components/StatusCard";
import { Timeline } from "./components/Timeline";
import { Button, EmptyState, STATE_STYLE } from "./components/primitives";
import { useTheme } from "./theme";
import type { Action, ActionResult, Backup, ProfileEntry, Revision, Settings } from "./types";

type Toast = { tone: "ok" | "error"; message: string };

function Sidebar({
  entries,
  selectedId,
  machine,
  onSelect,
  onAdd,
  onSettings,
}: {
  entries: ProfileEntry[];
  selectedId: string | null;
  machine: string;
  onSelect: (id: string) => void;
  onAdd: () => void;
  onSettings: () => void;
}) {
  const { accent } = useTheme();

  return (
    <aside className="flex w-64 shrink-0 flex-col border-r border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-900/40">
      <div className="border-b border-slate-200 px-4 py-4 dark:border-slate-800">
        <h1 className="text-sm font-semibold tracking-tight text-slate-900 dark:text-slate-100">
          Save Syncer
        </h1>
        <p className="mt-0.5 truncate text-xs text-slate-500" title={machine}>
          {machine}
        </p>
      </div>

      <nav className="flex-1 overflow-y-auto p-2">
        {entries.length === 0 ? (
          <p className="px-2 py-4 text-xs text-slate-500">No save folders yet.</p>
        ) : (
          <ul className="space-y-1">
            {entries.map(({ profile, status }) => {
              const style = status ? STATE_STYLE[status.state] : null;
              const active = profile.id === selectedId;
              return (
                <li key={profile.id}>
                  <button
                    // See the comment on Button's primary variant: an active row's
                    // background comes from the CSS accent variable, and some engines
                    // won't re-resolve that on an existing node after the variable
                    // changes. Keying on accent forces a fresh node when it does.
                    key={active ? accent : undefined}
                    onClick={() => onSelect(profile.id)}
                    className={`flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left transition ${
                      active
                        ? "bg-[var(--accent-soft-bg)] text-slate-900 dark:text-slate-100"
                        : "text-slate-600 hover:bg-slate-200/60 dark:text-slate-300 dark:hover:bg-slate-800/50"
                    }`}
                  >
                    <span
                      className={`h-2 w-2 shrink-0 rounded-full ${style?.dot ?? "bg-slate-400 dark:bg-slate-600"}`}
                      title={style?.label ?? "unknown"}
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm">{profile.name}</span>
                      <span className="block truncate text-[11px] text-slate-500">
                        {style?.label ?? "unavailable"}
                      </span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </nav>

      <div className="space-y-1 border-t border-slate-200 p-2 dark:border-slate-800">
        <button
          onClick={onAdd}
          className="w-full rounded-lg px-2.5 py-2 text-left text-sm text-slate-600 hover:bg-slate-200/60 dark:text-slate-300 dark:hover:bg-slate-800/50"
        >
          + Add a save folder
        </button>
        <button
          onClick={onSettings}
          className="w-full rounded-lg px-2.5 py-2 text-left text-sm text-slate-500 hover:bg-slate-200/60 dark:text-slate-400 dark:hover:bg-slate-800/50"
        >
          Settings
        </button>
      </div>
    </aside>
  );
}

export default function App() {
  const [entries, setEntries] = useState<ProfileEntry[]>([]);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [revisions, setRevisions] = useState<Revision[]>([]);
  const [backups, setBackups] = useState<Backup[]>([]);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [toast, setToast] = useState<Toast | null>(null);
  const [offline, setOffline] = useState(false);
  const [showAdd, setShowAdd] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [editing, setEditing] = useState(false);

  const selectedIdRef = useRef<string | null>(null);
  selectedIdRef.current = selectedId;

  const selected = entries.find((entry) => entry.profile.id === selectedId) ?? null;

  const notify = useCallback((tone: Toast["tone"], message: string) => {
    setToast({ tone, message });
    window.setTimeout(() => setToast(null), tone === "error" ? 9000 : 6000);
  }, []);

  const loadProfiles = useCallback(async () => {
    try {
      const loaded = await api.profiles();
      setOffline(false);
      setEntries(loaded);
      setSelectedId((current) => {
        if (current && loaded.some((entry) => entry.profile.id === current)) return current;
        return loaded[0]?.profile.id ?? null;
      });
    } catch (err) {
      if (err instanceof ApiError && err.kind === "offline") setOffline(true);
    }
  }, []);

  const loadDetail = useCallback(async (id: string, quiet = false) => {
    if (!quiet) setLoadingDetail(true);
    try {
      const [revs, backupList] = await Promise.all([api.revisions(id), api.backups(id)]);
      if (selectedIdRef.current !== id) return;
      setRevisions(revs);
      setBackups(backupList);
    } catch {
      if (selectedIdRef.current === id) {
        setRevisions([]);
        setBackups([]);
      }
    } finally {
      setLoadingDetail(false);
    }
  }, []);

  const refresh = useCallback(
    async (quiet = true) => {
      await loadProfiles();
      const id = selectedIdRef.current;
      if (id) await loadDetail(id, quiet);
    },
    [loadProfiles, loadDetail],
  );

  useEffect(() => {
    void loadProfiles();
    api.settings().then(setSettings).catch(() => undefined);
  }, [loadProfiles]);

  useEffect(() => {
    // Clear first: showing the previous profile's revisions under this profile's
    // header, with live Restore buttons, is how someone restores the wrong save.
    setRevisions([]);
    setBackups([]);
    if (selectedId) void loadDetail(selectedId);
  }, [selectedId, loadDetail]);

  // Live updates when this app acts, plus a slow poll so a push from the other
  // desktop shows up on its own once the relay folder finishes syncing.
  useEffect(() => {
    const unsubscribe = subscribeToEvents(() => void refresh(true));
    const timer = window.setInterval(() => void refresh(true), 8000);
    return () => {
      unsubscribe();
      window.clearInterval(timer);
    };
  }, [refresh]);

  const run = useCallback(
    async (key: string, call: () => Promise<ActionResult>) => {
      setBusy(key);
      try {
        const result = await call();
        notify("ok", result.message);
        await refresh(true);
      } catch (err) {
        notify("error", err instanceof Error ? err.message : String(err));
        await refresh(true);
      } finally {
        setBusy(null);
      }
    },
    [notify, refresh],
  );

  const onAction = useCallback(
    (action: Action) => {
      if (!selectedId) return;
      const calls: Record<Action, () => Promise<ActionResult>> = {
        push: () => api.push(selectedId),
        pull: () => api.pull(selectedId),
        use_local: () => api.resolve(selectedId, "use_local"),
        use_remote: () => api.resolve(selectedId, "use_remote"),
        keep_both: () => api.resolve(selectedId, "keep_both"),
      };
      void run(action, calls[action]);
    },
    [selectedId, run],
  );

  return (
    <div className="flex h-screen">
      <Sidebar
        entries={entries}
        selectedId={selectedId}
        machine={settings?.machine ?? "this desktop"}
        onSelect={setSelectedId}
        onAdd={() => setShowAdd(true)}
        onSettings={() => setShowSettings(true)}
      />

      <main className="flex-1 overflow-y-auto bg-white dark:bg-slate-950">
        {offline && (
          <div className="border-b border-rose-600/30 bg-rose-500/10 px-6 py-2 text-xs text-rose-700 dark:border-rose-500/30 dark:text-rose-200">
            Cannot reach the Save Syncer service. Is it still running?
          </div>
        )}

        <div className="mx-auto max-w-4xl space-y-4 p-6">
          {!selected ? (
            <div className="rounded-xl border border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-900/60">
              <EmptyState title="Nothing set up yet">
                Add the save folder you want mirrored, and point it at a folder both desktops
                can see - a Dropbox, OneDrive or Drive folder, or a network share. The two
                machines never need to be online at the same time.
              </EmptyState>
              <div className="flex justify-center pb-6">
                <Button variant="primary" onClick={() => setShowAdd(true)}>
                  Add a save folder
                </Button>
              </div>
            </div>
          ) : selected.status === null ? (
            <div className="rounded-xl border border-rose-600/30 bg-rose-500/5 p-5 dark:border-rose-500/30">
              <h2 className="text-sm font-semibold text-rose-700 dark:text-rose-200">
                {selected.profile.name}
              </h2>
              <p className="mt-1 text-xs text-rose-700/80 dark:text-rose-200/80">{selected.error}</p>
              <div className="mt-4">
                <Button onClick={() => setEditing(true)}>Check the paths</Button>
              </div>
            </div>
          ) : (
            <>
              <header className="flex flex-wrap items-center justify-between gap-3">
                <div className="min-w-0">
                  <h2 className="truncate text-lg font-semibold text-slate-900 dark:text-slate-100">
                    {selected.profile.name}
                  </h2>
                  <p className="text-xs text-slate-500">
                    Last synced {relativeTime(selected.status.last_sync_at)}
                  </p>
                </div>
                <div className="flex gap-2">
                  <Button size="sm" variant="ghost" onClick={() => void refresh(false)}>
                    Refresh
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>
                    Edit
                  </Button>
                </div>
              </header>

              <StatusCard status={selected.status} busy={busy} onAction={onAction} />

              {(selected.status.state === "conflict" || selected.status.state === "unlinked") && (
                <CompareCard
                  status={selected.status}
                  machine={settings?.machine ?? "this desktop"}
                  busy={busy}
                  onAction={onAction}
                />
              )}

              <Timeline
                profileId={selected.profile.id}
                revisions={revisions}
                loading={loadingDetail}
                busy={busy}
                onRestore={(rev) =>
                  void run(`restore:${rev}`, () => api.restore(selected.profile.id, rev))
                }
              />

              <Backups
                backups={backups}
                busy={busy}
                onRestore={(id) =>
                  void run(`backup:${id}`, () => api.restoreBackup(selected.profile.id, id))
                }
              />
            </>
          )}
        </div>
      </main>

      {toast && (
        <div
          className={`fixed bottom-5 right-5 z-40 max-w-md rounded-lg border px-4 py-3 text-sm shadow-xl ${
            toast.tone === "ok"
              ? "border-emerald-600/30 bg-emerald-50 text-emerald-900 dark:border-emerald-500/30 dark:bg-emerald-950/90 dark:text-emerald-100"
              : "border-rose-600/40 bg-rose-50 text-rose-900 dark:border-rose-500/40 dark:bg-rose-950/90 dark:text-rose-100"
          }`}
          role="status"
        >
          {toast.message}
          <button
            className="ml-3 text-xs opacity-60 hover:opacity-100"
            onClick={() => setToast(null)}
          >
            dismiss
          </button>
        </div>
      )}

      {showAdd && (
        <ProfileFormModal
          onClose={() => setShowAdd(false)}
          onSaved={(profile) => {
            setShowAdd(false);
            setSelectedId(profile.id);
            void refresh(false);
          }}
        />
      )}

      {editing && selected && (
        <ProfileFormModal
          profile={selected.profile}
          onClose={() => setEditing(false)}
          onSaved={() => {
            setEditing(false);
            void refresh(false);
          }}
          onDeleted={() => {
            setEditing(false);
            setSelectedId(null);
            void refresh(false);
          }}
        />
      )}

      {showSettings && settings && (
        <SettingsModal
          settings={settings}
          onClose={() => setShowSettings(false)}
          onSaved={(saved) => {
            setSettings(saved);
            setShowSettings(false);
            void refresh(false);
          }}
        />
      )}
    </div>
  );
}
