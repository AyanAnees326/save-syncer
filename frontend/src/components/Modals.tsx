import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { absoluteTime, bytes, plural, relativeTime } from "../format";
import { Button, Field, Modal, inputClass } from "./primitives";
import { ACCENTS, useTheme, type ThemeMode } from "../theme";
import type { AccountStatus, CloudRoot, DiscoveredProfile, PathCheck, Profile, Settings } from "../types";

function usePathCheck(path: string): PathCheck | null {
  const [check, setCheck] = useState<PathCheck | null>(null);
  useEffect(() => {
    if (!path.trim()) {
      setCheck(null);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      try {
        const result = await api.checkPath(path);
        if (!cancelled) setCheck(result);
      } catch {
        if (!cancelled) setCheck(null);
      }
    }, 350);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [path]);
  return check;
}

function PathHint({ check, kind }: { check: PathCheck | null; kind: "save" | "relay" }) {
  if (!check) return <>Paste the full path. Environment variables like %APPDATA% work.</>;
  if (!check.exists)
    return (
      <span className="text-amber-600 dark:text-amber-400">
        Nothing there yet.{" "}
        {kind === "relay"
          ? "It will be created on the first push."
          : "Pull will create it from the relay."}
      </span>
    );
  if (!check.is_dir)
    return <span className="text-rose-600 dark:text-rose-400">That is a file, not a folder.</span>;
  return (
    <span className="text-emerald-600 dark:text-emerald-400">
      Found {plural(check.file_count, "file")}
      {check.total_size !== undefined ? `, ${bytes(check.total_size)}` : ""}.
    </span>
  );
}

/**
 * A text field plus a native Windows folder picker. The dialog runs on the backend
 * (this app never talks to a remote server), so it looks and behaves the same
 * whether you're in the desktop window or a plain browser tab.
 */
function PathField({
  value,
  onChange,
  placeholder,
  dialogTitle,
}: {
  value: string;
  onChange: (path: string) => void;
  placeholder: string;
  dialogTitle: string;
}) {
  const [opening, setOpening] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const browse = async () => {
    setOpening(true);
    setError(null);
    try {
      const result = await api.pickFolder(value, dialogTitle);
      if (result.path) onChange(result.path);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not open the folder picker.");
    } finally {
      setOpening(false);
    }
  };

  return (
    <div>
      <div className="flex gap-2">
        <input
          className={inputClass}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          spellCheck={false}
        />
        <Button variant="secondary" onClick={browse} disabled={opening}>
          {opening ? "Opening..." : "Browse..."}
        </Button>
      </div>
      {error && <p className="mt-1.5 text-xs text-rose-600 dark:text-rose-400">{error}</p>}
    </div>
  );
}

/** Cloud-sync folders already installed on this machine, offered as one-click picks. */
function CloudRootChips({ onPick }: { onPick: (path: string) => void }) {
  const [roots, setRoots] = useState<CloudRoot[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .cloudRoots()
      .then((found) => {
        if (!cancelled) setRoots(found);
      })
      .catch(() => {
        if (!cancelled) setRoots([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!roots || roots.length === 0) return null;

  return (
    <div className="mt-2 flex flex-wrap items-center gap-1.5">
      <span className="text-xs text-slate-500">Found on this PC:</span>
      {roots.map((root) => (
        <button
          key={root.path}
          type="button"
          onClick={() => onPick(`${root.path}\\SaveSyncer`)}
          className="rounded-full bg-slate-100 px-2.5 py-1 text-xs text-slate-700 ring-1 ring-inset ring-slate-300 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-200 dark:ring-slate-700 dark:hover:bg-slate-700"
          title={`Use ${root.path}\\SaveSyncer`}
        >
          {root.label}
        </button>
      ))}
    </div>
  );
}

/** source "cloud" fetches once (there's no path to debounce on); "folder" debounces
 * on relayPath the way a text field being typed into needs. */
function useDiscovery(
  source: "folder" | "cloud",
  relayPath: string,
): { loading: boolean; results: DiscoveredProfile[] } {
  const [results, setResults] = useState<DiscoveredProfile[]>([]);
  const [loading, setLoading] = useState(false);
  const enabled = source === "cloud" || relayPath.trim() !== "";

  useEffect(() => {
    if (!enabled) {
      setResults([]);
      return;
    }
    let cancelled = false;
    setLoading(true);
    const run = async () => {
      try {
        const found = source === "cloud" ? await api.accountDiscover() : await api.discoverRelay(relayPath);
        if (!cancelled) setResults(found);
      } catch {
        if (!cancelled) setResults([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    if (source === "folder") {
      const timer = window.setTimeout(run, 350);
      return () => {
        cancelled = true;
        window.clearTimeout(timer);
      };
    }
    void run();
    return () => {
      cancelled = true;
    };
  }, [source, relayPath, enabled]);

  return { loading, results };
}

/**
 * What already exists in the relay folder or account, so a second desktop can pick a
 * save it recognises instead of having to retype the first desktop's profile name.
 */
function DiscoveredSaves({
  source,
  relayPath,
  selectedId,
  onPick,
}: {
  source: "folder" | "cloud";
  relayPath: string;
  selectedId: string | null;
  onPick: (entry: DiscoveredProfile) => void;
}) {
  const { loading, results } = useDiscovery(source, relayPath);
  const { accent } = useTheme();
  const place = source === "cloud" ? "your account" : "this folder";

  if ((source === "folder" && !relayPath.trim()) || (!loading && results.length === 0)) return null;

  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 dark:border-slate-800 dark:bg-slate-950/40">
      <h4 className="text-xs font-semibold text-slate-700 dark:text-slate-300">
        {loading && results.length === 0 ? `Looking in ${place}...` : `Found in ${place}`}
      </h4>
      {results.length > 0 && (
        <ul className="mt-2 space-y-2">
          {results.map((entry) => {
            const active = entry.id === selectedId;
            return (
              <li
                // Re-keying on accent when active forces a fresh node for the same
                // reason Button does (see its comment) - the border/bg here are also
                // var(--accent-*)-driven.
                key={active ? `${entry.id}-${accent}` : entry.id}
                className={`rounded-lg border p-2.5 ${
                  active
                    ? "border-[var(--accent-ring)] bg-[var(--accent-soft-bg)]"
                    : "border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900/60"
                }`}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-slate-900 dark:text-slate-100">
                      {entry.name}
                    </p>
                    <p className="mt-0.5 text-xs text-slate-500">
                      Last synced from {entry.machine} &middot;{" "}
                      <span title={absoluteTime(entry.created_at)}>
                        {relativeTime(entry.created_at)}
                      </span>{" "}
                      &middot; {plural(entry.file_count, "file")} &middot; {bytes(entry.total_size)}
                    </p>
                    {entry.note && (
                      <p className="mt-0.5 truncate text-xs text-slate-500" title={entry.note}>
                        &ldquo;{entry.note}&rdquo;
                      </p>
                    )}
                  </div>
                  {entry.already_added ? (
                    <span className="shrink-0 rounded-full bg-slate-100 px-2 py-1 text-[11px] text-slate-500 dark:bg-slate-800 dark:text-slate-400">
                      Already set up
                    </span>
                  ) : (
                    <Button size="sm" variant={active ? "primary" : "secondary"} onClick={() => onPick(entry)}>
                      {active ? "Selected" : "Use this save"}
                    </Button>
                  )}
                </div>
                {entry.source_local_path && (active || !entry.already_added) && (
                  <p className="mt-2 border-t border-slate-900/5 pt-2 text-[11px] text-slate-500 dark:border-white/5">
                    On {entry.machine} this lived at{" "}
                    <code className="rounded bg-slate-100 px-1 text-slate-600 dark:bg-slate-800 dark:text-slate-400">
                      {entry.source_local_path}
                    </code>
                    . The path on this PC will likely differ - same idea, different
                    username or account id.
                  </p>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function CloudSetupGuide({ defaultOpen }: { defaultOpen: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-950/40">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center justify-between px-3 py-2 text-left text-xs font-medium text-slate-700 dark:text-slate-300"
      >
        <span>How to set up cloud sync</span>
        <span className="text-slate-500">{open ? "Hide" : "Show"}</span>
      </button>
      {open && (
        <ol className="space-y-2.5 border-t border-slate-200 px-3 py-3 text-xs text-slate-500 dark:border-slate-800 dark:text-slate-400">
          <li>
            <strong className="text-slate-800 dark:text-slate-300">
              1. Install a cloud sync client on both desktops
            </strong>
            <br />
            Dropbox, OneDrive, or Google Drive - whichever you already use. Sign in with the same
            account on both, or use a folder they both share.
          </li>
          <li>
            <strong className="text-slate-800 dark:text-slate-300">
              2. Point the relay field at a folder inside it
            </strong>
            <br />
            Use a chip above, or Browse into that folder and create one called something like{" "}
            <code className="rounded bg-slate-200 px-1 dark:bg-slate-800">SaveSyncer</code>. It will
            be created automatically on the first sync if it doesn't exist yet.
          </li>
          <li>
            <strong className="text-slate-800 dark:text-slate-300">
              3. The path itself can differ between desktops
            </strong>
            <br />
            Only the cloud folder's <em>contents</em> need to match - the drive letter or Windows
            username in the path doesn't matter, so you don't have to type the same thing twice.
          </li>
          <li>
            <strong className="text-slate-800 dark:text-slate-300">
              4. Make sure the folder actually downloads on both sides
            </strong>
            <br />
            OneDrive: right-click it &rarr; <em>Always keep on this device</em> (Files On-Demand
            can leave it as a placeholder). Dropbox: Preferences &rarr; Sync &rarr; check it isn't
            excluded by Selective Sync. Google Drive: set it to <em>Mirror files</em>, not{" "}
            <em>Stream files</em>.
          </li>
          <li>
            <strong className="text-slate-800 dark:text-slate-300">
              5. Give each save its own subfolder
            </strong>
            <br />
            Don't point two profiles at the same relay folder - each one needs its own history.
          </li>
          <li className="text-slate-500">
            If a sync ever runs against a folder that hasn't finished downloading, Save Syncer
            detects the incomplete copy and refuses to apply it rather than risk your save - but
            waiting for the cloud app to say "up to date" avoids that delay entirely.
          </li>
        </ol>
      )}
    </div>
  );
}

export function ProfileFormModal({
  profile,
  onClose,
  onSaved,
  onDeleted,
}: {
  profile?: Profile;
  onClose: () => void;
  onSaved: (profile: Profile) => void;
  onDeleted?: (id: string) => void;
}) {
  const editing = Boolean(profile);
  const { accent } = useTheme();
  const [storage, setStorage] = useState<"folder" | "cloud">(
    (profile?.remote_kind as "folder" | "cloud") ?? "folder",
  );
  const [account, setAccount] = useState<AccountStatus | null>(null);
  const [name, setName] = useState(profile?.name ?? "");
  const [localPath, setLocalPath] = useState(profile?.local_path ?? "");
  const [relayPath, setRelayPath] = useState(profile?.relay_path ?? "");
  const [policy, setPolicy] = useState(profile?.policy ?? "ask");
  const [guards, setGuards] = useState((profile?.guard_processes ?? []).join(", "));
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  // Set when the user picks an existing save out of the relay folder or account
  // instead of typing a name - this desktop then links to that history instead of
  // starting a new one.
  const [adopt, setAdopt] = useState<DiscoveredProfile | null>(null);

  useEffect(() => {
    api.account().then(setAccount).catch(() => undefined);
  }, []);

  const localCheck = usePathCheck(localPath);
  const relayCheck = usePathCheck(relayPath);
  const valid = Boolean(
    name.trim() && localPath.trim() && (storage === "cloud" || relayPath.trim()),
  );

  const updateRelayPath = (value: string) => {
    setRelayPath(value);
    setAdopt(null); // a different relay folder invalidates whatever was found before
  };

  const pickDiscovered = (entry: DiscoveredProfile) => {
    setAdopt(entry);
    setName(entry.name);
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    const body = {
      name: name.trim(),
      local_path: localPath.trim(),
      relay_path: storage === "cloud" ? "" : relayPath.trim(),
      policy,
      guard_processes: guards
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean),
      remote_kind: storage,
    };
    try {
      const saved = profile
        ? await api.updateProfile(profile.id, body)
        : adopt
          ? await api.adoptProfile({ ...body, id: adopt.id })
          : await api.createProfile(body);
      onSaved(saved);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title={editing ? `Edit ${profile!.name}` : "Add a save folder"} onClose={onClose}>
      <div className="space-y-4">
        {!editing && (
          <Field label="Where should this be stored?">
            <div className="inline-flex rounded-lg bg-slate-100 p-1 dark:bg-slate-800">
              {(["folder", "cloud"] as const).map((kind) => (
                <button
                  key={kind}
                  type="button"
                  onClick={() => {
                    setStorage(kind);
                    setAdopt(null);
                  }}
                  className={`rounded-md px-3 py-1.5 text-xs font-medium transition ${
                    storage === kind
                      ? "bg-white text-slate-900 shadow-sm dark:bg-slate-950 dark:text-slate-100"
                      : "text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200"
                  }`}
                >
                  {kind === "folder" ? "A shared folder" : "Your account"}
                </button>
              ))}
            </div>
          </Field>
        )}

        {storage === "cloud" ? (
          account && !account.signed_in ? (
            <p className="rounded-lg border border-amber-600/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-800 dark:border-amber-500/30 dark:text-amber-200">
              You're not signed in. Open Settings and sign in or create an account first.
            </p>
          ) : (
            <>
              {account?.signed_in && (
                <p className="text-xs text-slate-500">
                  Signed in as <span className="font-medium">{account.username}</span> on{" "}
                  {account.server_url}
                </p>
              )}
              {!editing && (
                <DiscoveredSaves
                  source="cloud"
                  relayPath=""
                  selectedId={adopt?.id ?? null}
                  onPick={pickDiscovered}
                />
              )}
            </>
          )
        ) : (
          <>
            <Field label="Shared relay folder" hint={<PathHint check={relayCheck} kind="relay" />}>
              <PathField
                value={relayPath}
                onChange={updateRelayPath}
                placeholder="C:\Users\you\Dropbox\SaveSyncer"
                dialogTitle="Choose or create a folder inside your cloud-synced folder"
              />
              <CloudRootChips onPick={updateRelayPath} />
            </Field>

            {!editing && (
              <DiscoveredSaves
                source="folder"
                relayPath={relayPath}
                selectedId={adopt?.id ?? null}
                onPick={pickDiscovered}
              />
            )}
          </>
        )}

        <Field
          label="Name"
          hint={
            adopt ? (
              <span key={accent} className="text-[var(--accent-soft-text)]">
                Linking to the save above.{" "}
                <button
                  type="button"
                  className="underline underline-offset-2 hover:opacity-80"
                  onClick={() => setAdopt(null)}
                >
                  Start a new save instead
                </button>
              </span>
            ) : (
              "Usually the game. Pick one above, or type a new name to start a fresh history."
            )
          }
        >
          <input
            className={inputClass}
            value={name}
            onChange={(e) => {
              setName(e.target.value);
              setAdopt(null); // typing a name means "not the one I picked" any more
            }}
            placeholder="Elden Ring"
            autoFocus={!editing}
          />
        </Field>

        <Field
          label="Save folder on this desktop"
          hint={
            adopt?.source_local_path ? (
              <>
                On {adopt.machine} this was at{" "}
                <code className="rounded bg-slate-100 px-1 text-slate-600 dark:bg-slate-800 dark:text-slate-400">
                  {adopt.source_local_path}
                </code>
                . Point this at the same game's folder on this PC.
              </>
            ) : (
              <PathHint check={localCheck} kind="save" />
            )
          }
        >
          <PathField
            value={localPath}
            onChange={setLocalPath}
            placeholder="C:\Users\you\AppData\Roaming\EldenRing\76561..."
            dialogTitle="Choose the save folder on this desktop"
          />
        </Field>

        {!editing && storage === "folder" && <CloudSetupGuide defaultOpen={!relayPath.trim()} />}

        <Field
          label="When both desktops changed"
          hint={
            policy === "ask"
              ? "You get the comparison screen and decide. Recommended."
              : "The side whose files were touched most recently wins. Relies on both clocks being right."
          }
        >
          <select
            className={inputClass}
            value={policy}
            onChange={(e) => setPolicy(e.target.value)}
          >
            <option value="ask">Ask me</option>
            <option value="latest_wins">Take the most recently changed</option>
          </select>
        </Field>

        <Field
          label="Do not sync while these are running"
          hint="Comma separated executables, e.g. eldenring.exe. Leave blank to skip the check."
        >
          <input
            className={inputClass}
            value={guards}
            onChange={(e) => setGuards(e.target.value)}
            placeholder="eldenring.exe"
            spellCheck={false}
          />
        </Field>

        {error && (
          <p className="rounded-lg border border-rose-600/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-800 dark:border-rose-500/30 dark:text-rose-200">
            {error}
          </p>
        )}

        <div className="flex items-center justify-between gap-3 border-t border-slate-200 pt-4 dark:border-slate-800">
          <div>
            {editing && onDeleted && (
              <Button
                variant={confirmDelete ? "danger" : "ghost"}
                size="sm"
                onClick={async () => {
                  if (!confirmDelete) {
                    setConfirmDelete(true);
                    return;
                  }
                  await api.deleteProfile(profile!.id);
                  onDeleted(profile!.id);
                }}
              >
                {confirmDelete ? "Really remove it?" : "Remove profile"}
              </Button>
            )}
          </div>
          <div className="flex gap-2">
            <Button variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button variant="primary" disabled={!valid || saving} onClick={save}>
              {editing ? "Save changes" : "Add profile"}
            </Button>
          </div>
        </div>
        {editing && confirmDelete && (
          <p className="text-xs text-slate-500">
            Removing the profile only forgets it here. Your save folder and the relay history
            are left alone.
          </p>
        )}
      </div>
    </Modal>
  );
}

/**
 * Sign in, register, or sign out of the account server. This is what makes cloud
 * profiles work: a profile stored "in your account" reads settings.account_token to
 * know where to push/pull, exactly the way a folder profile reads relay_path.
 */
function AccountSection() {
  const [account, setAccount] = useState<AccountStatus | null>(null);
  const [mode, setMode] = useState<"login" | "register">("login");
  const [serverUrl, setServerUrl] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = () => api.account().then(setAccount).catch(() => undefined);
  useEffect(() => {
    refresh();
  }, []);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const call = mode === "login" ? api.accountLogin : api.accountRegister;
      const result = await call(serverUrl.trim(), username.trim(), password);
      setAccount(result);
      setPassword("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reach that server.");
    } finally {
      setBusy(false);
    }
  };

  if (account?.signed_in) {
    return (
      <Field label="Account">
        <div className="flex items-center justify-between gap-3 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2.5 dark:border-slate-800 dark:bg-slate-950/40">
          <div className="min-w-0">
            <p className="truncate text-sm text-slate-800 dark:text-slate-200">
              Signed in as <span className="font-medium">{account.username}</span>
            </p>
            <p className="truncate text-xs text-slate-500">{account.server_url}</p>
          </div>
          <Button
            size="sm"
            variant="ghost"
            onClick={async () => {
              await api.accountLogout();
              refresh();
            }}
          >
            Sign out
          </Button>
        </div>
      </Field>
    );
  }

  return (
    <Field
      label="Account"
      hint="Lets a profile be stored on a server you control instead of a Dropbox/OneDrive folder - see Add a save folder for the option to use it."
    >
      <div className="space-y-2 rounded-lg border border-slate-200 bg-slate-50 p-3 dark:border-slate-800 dark:bg-slate-950/40">
        <div className="inline-flex rounded-lg bg-slate-100 p-1 dark:bg-slate-800">
          {(["login", "register"] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => {
                setMode(m);
                setError(null);
              }}
              className={`rounded-md px-3 py-1 text-xs font-medium transition ${
                mode === m
                  ? "bg-white text-slate-900 shadow-sm dark:bg-slate-950 dark:text-slate-100"
                  : "text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200"
              }`}
            >
              {m === "login" ? "Sign in" : "Create account"}
            </button>
          ))}
        </div>
        <input
          className={inputClass}
          value={serverUrl}
          onChange={(e) => setServerUrl(e.target.value)}
          placeholder="http://your-server:8420"
          spellCheck={false}
        />
        <input
          className={inputClass}
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          placeholder="Username"
          spellCheck={false}
        />
        <input
          type="password"
          className={inputClass}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="Password"
        />
        {error && <p className="text-xs text-rose-600 dark:text-rose-400">{error}</p>}
        <Button
          size="sm"
          variant="primary"
          disabled={busy || !serverUrl.trim() || !username.trim() || !password}
          onClick={submit}
        >
          {mode === "login" ? "Sign in" : "Create account"}
        </Button>
      </div>
    </Field>
  );
}

export function SettingsModal({
  settings,
  onClose,
  onSaved,
}: {
  settings: Settings;
  onClose: () => void;
  onSaved: (settings: Settings) => void;
}) {
  const [machine, setMachine] = useState(settings.machine);
  const [retention, setRetention] = useState(String(settings.backup_retention));
  const theme = useTheme();

  const MODES: { id: ThemeMode; label: string }[] = [
    { id: "light", label: "Light" },
    { id: "dark", label: "Dark" },
    { id: "system", label: "System" },
  ];

  return (
    <Modal title="Settings" onClose={onClose}>
      <div className="space-y-4">
        <Field label="Theme">
          <div className="inline-flex rounded-lg bg-slate-100 p-1 dark:bg-slate-800">
            {MODES.map((m) => (
              <button
                key={m.id}
                type="button"
                onClick={() => theme.setMode(m.id)}
                className={`rounded-md px-3 py-1.5 text-xs font-medium transition ${
                  theme.mode === m.id
                    ? "bg-white text-slate-900 shadow-sm dark:bg-slate-950 dark:text-slate-100"
                    : "text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200"
                }`}
              >
                {m.label}
              </button>
            ))}
          </div>
        </Field>

        <Field label="Accent color">
          <div className="flex flex-wrap gap-2">
            {ACCENTS.map((a) => (
              <button
                key={a.id}
                type="button"
                onClick={() => theme.setAccent(a.id)}
                title={a.label}
                aria-label={a.label}
                aria-pressed={theme.accent === a.id}
                className={`h-8 w-8 rounded-full ring-2 ring-offset-2 ring-offset-white transition dark:ring-offset-slate-900 ${
                  theme.accent === a.id ? "ring-slate-900 dark:ring-slate-100" : "ring-transparent"
                }`}
                style={{ backgroundColor: a.swatch }}
              />
            ))}
          </div>
        </Field>

        <AccountSection />

        <Field
          label="This desktop is called"
          hint="Shown against every revision this machine publishes, so you can tell the two apart."
        >
          <input className={inputClass} value={machine} onChange={(e) => setMachine(e.target.value)} />
        </Field>

        <Field
          label="Backups to keep"
          hint="A snapshot of your save folder is taken before anything overwrites it. Older ones past this limit are deleted."
        >
          <input
            type="number"
            min={0}
            max={100}
            className={inputClass}
            value={retention}
            onChange={(e) => setRetention(e.target.value)}
          />
        </Field>

        <Field label="App data lives in">
          <code className="block break-all rounded-lg border border-slate-200 bg-slate-100 px-3 py-2 font-mono text-xs text-slate-500 dark:border-slate-800 dark:bg-slate-950/60 dark:text-slate-400">
            {settings.home}
          </code>
        </Field>

        <div className="flex justify-end gap-2 border-t border-slate-200 pt-4 dark:border-slate-800">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            onClick={async () => {
              const saved = await api.saveSettings({
                machine: machine.trim() || settings.machine,
                backup_retention: Math.max(0, Number(retention) || 0),
              });
              onSaved(saved);
            }}
          >
            Save
          </Button>
        </div>
      </div>
    </Modal>
  );
}
