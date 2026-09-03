export type SyncState =
  | "in_sync"
  | "local_ahead"
  | "remote_ahead"
  | "conflict"
  | "unlinked"
  | "no_remote"
  | "local_missing";

export type Action = "push" | "pull" | "use_local" | "use_remote" | "keep_both";

export interface ManifestSummary {
  rev: number | null;
  parent: number | null;
  machine: string;
  created_at: string;
  note: string;
  file_count: number;
  total_size: number;
  newest_mtime: number | null;
  content_id: string;
}

export interface Diff {
  added: string[];
  changed: string[];
  removed: string[];
  is_empty: boolean;
}

export type RemoteKind = "folder" | "cloud";

export interface Status {
  profile_id: string;
  profile_name: string;
  local_path: string;
  relay_path: string;
  remote_kind: RemoteKind;
  policy: string;
  state: SyncState;
  message: string;
  actions: Action[];
  base_rev: number | null;
  remote_rev: number | null;
  local: ManifestSummary | null;
  remote: ManifestSummary | null;
  diff: Diff | null;
  last_sync_at: string | null;
  blocking_processes: string[];
}

export interface Profile {
  id: string;
  name: string;
  local_path: string;
  relay_path: string;
  excludes: string[];
  policy: string;
  guard_processes: string[];
  created_at: string;
  remote_kind: RemoteKind;
}

export interface ProfileEntry {
  profile: Profile;
  status: Status | null;
  error?: string;
}

export interface AccountStatus {
  signed_in: boolean;
  server_url: string;
  username: string;
}

export interface Revision extends ManifestSummary {
  rev: number;
  is_head: boolean;
  is_base: boolean;
  from_this_machine: boolean;
  matches_disk: boolean;
}

export interface FileEntry {
  path: string;
  hash: string;
  size: number;
  mtime: number;
}

export interface RevisionDetail extends ManifestSummary {
  files: FileEntry[];
  diff_vs_disk: Diff;
  matches_disk: boolean;
}

export interface Backup {
  id: string;
  path: string;
  created_at: string;
  file_count: number;
  total_size: number;
}

export interface ActionResult {
  action: string;
  message: string;
  rev: number | null;
  backup_path: string | null;
  remote_copy_path?: string | null;
}

export interface Settings {
  machine: string;
  backup_retention: number;
  home: string;
}

export interface PathCheck {
  path: string;
  exists: boolean;
  is_dir: boolean;
  file_count: number;
  total_size?: number;
}

export interface CloudRoot {
  label: string;
  path: string;
}

export interface DiscoveredProfile {
  id: string;
  name: string;
  rev: number;
  machine: string;
  created_at: string;
  note: string;
  file_count: number;
  total_size: number;
  source_local_path: string;
  already_added: boolean;
}
