import type {
  AccountStatus,
  ActionResult,
  Backup,
  CloudRoot,
  DiscoveredProfile,
  PathCheck,
  Profile,
  ProfileEntry,
  RemoteKind,
  Revision,
  RevisionDetail,
  Settings,
  Status,
} from "./types";

// The desktop shell mints a token per launch and passes it in the URL. In dev there
// is no token and the local server does not ask for one.
const TOKEN = new URLSearchParams(window.location.search).get("token") ?? "";

export class ApiError extends Error {
  kind: string;
  status: number;
  constructor(message: string, kind: string, status: number) {
    super(message);
    this.kind = kind;
    this.status = status;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { ...((init.headers as Record<string, string>) ?? {}) };
  if (init.body) headers["Content-Type"] = "application/json";
  if (TOKEN) headers["X-Savesync-Token"] = TOKEN;

  let response: Response;
  try {
    response = await fetch(path, { ...init, headers });
  } catch {
    throw new ApiError("Cannot reach the Save Syncer service.", "offline", 0);
  }

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    let kind = "http";
    try {
      const body = await response.json();
      detail = body.detail ?? detail;
      kind = body.kind ?? kind;
    } catch {
      /* a non-JSON error body is not worth reporting in detail */
    }
    throw new ApiError(detail, kind, response.status);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const api = {
  profiles: () => request<ProfileEntry[]>("/api/profiles"),
  createProfile: (body: Partial<Profile>) => post<Profile>("/api/profiles", body),
  adoptProfile: (body: {
    id: string;
    name: string;
    local_path: string;
    relay_path?: string;
    policy?: string;
    guard_processes?: string[];
    remote_kind?: RemoteKind;
  }) => post<Profile>("/api/profiles/adopt", body),
  updateProfile: (id: string, body: Partial<Profile>) =>
    request<Profile>(`/api/profiles/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteProfile: (id: string) => request<void>(`/api/profiles/${id}`, { method: "DELETE" }),

  status: (id: string) => request<Status>(`/api/profiles/${id}/status`),
  revisions: (id: string) => request<Revision[]>(`/api/profiles/${id}/revisions`),
  revision: (id: string, rev: number) =>
    request<RevisionDetail>(`/api/profiles/${id}/revisions/${rev}`),

  push: (id: string, note = "") => post<ActionResult>(`/api/profiles/${id}/push`, { note }),
  pull: (id: string) => post<ActionResult>(`/api/profiles/${id}/pull`),
  sync: (id: string, note = "") => post<ActionResult>(`/api/profiles/${id}/sync`, { note }),
  restore: (id: string, rev: number) => post<ActionResult>(`/api/profiles/${id}/restore`, { rev }),
  resolve: (id: string, choice: string, note = "") =>
    post<ActionResult>(`/api/profiles/${id}/resolve`, { choice, note }),

  backups: (id: string) => request<Backup[]>(`/api/profiles/${id}/backups`),
  restoreBackup: (id: string, backupId: string) =>
    post<ActionResult>(`/api/profiles/${id}/backups/${encodeURIComponent(backupId)}/restore`),

  settings: () => request<Settings>("/api/settings"),
  saveSettings: (body: Partial<Settings>) =>
    request<Settings>("/api/settings", { method: "PATCH", body: JSON.stringify(body) }),

  checkPath: (path: string) =>
    request<PathCheck>(`/api/fs/check?path=${encodeURIComponent(path)}`),
  cloudRoots: () => request<CloudRoot[]>("/api/fs/cloud-roots"),
  pickFolder: (initial?: string, title?: string) =>
    post<{ path: string | null }>("/api/fs/pick-folder", { initial, title }),
  discoverRelay: (relayPath: string) =>
    post<DiscoveredProfile[]>("/api/relay/discover", { relay_path: relayPath }),

  account: () => request<AccountStatus>("/api/account"),
  accountDiscover: () => request<DiscoveredProfile[]>("/api/account/discover"),
  accountRegister: (serverUrl: string, username: string, password: string) =>
    post<AccountStatus>("/api/account/register", { server_url: serverUrl, username, password }),
  accountLogin: (serverUrl: string, username: string, password: string) =>
    post<AccountStatus>("/api/account/login", { server_url: serverUrl, username, password }),
  accountLogout: () => post<AccountStatus>("/api/account/logout"),
};

/** Subscribe to engine events. Returns an unsubscribe function. */
export function subscribeToEvents(onEvent: () => void): () => void {
  let socket: WebSocket | null = null;
  let retry: number | undefined;
  let closed = false;

  const connect = () => {
    if (closed) return;
    const scheme = window.location.protocol === "https:" ? "wss" : "ws";
    const query = TOKEN ? `?token=${encodeURIComponent(TOKEN)}` : "";
    socket = new WebSocket(`${scheme}://${window.location.host}/api/events${query}`);
    socket.onmessage = () => onEvent();
    socket.onclose = () => {
      if (!closed) retry = window.setTimeout(connect, 2000);
    };
    socket.onerror = () => socket?.close();
  };

  connect();
  return () => {
    closed = true;
    window.clearTimeout(retry);
    socket?.close();
  };
}
