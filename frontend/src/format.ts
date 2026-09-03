export function bytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return "-";
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = n / 1024;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[i]}`;
}

export function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

/** ISO string or unix seconds -> "3 minutes ago". */
export function relativeTime(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return "never";
  const then = typeof value === "number" ? value * 1000 : Date.parse(value);
  if (Number.isNaN(then)) return "unknown";

  const seconds = Math.round((Date.now() - then) / 1000);
  if (seconds < 0) return "just now";
  if (seconds < 45) return "just now";
  const steps: [number, Intl.RelativeTimeFormatUnit][] = [
    [60, "second"],
    [3600, "minute"],
    [86400, "hour"],
    [604800, "day"],
    [2629800, "week"],
    [31557600, "month"],
  ];
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  let previous = 1;
  for (const [limit, unit] of steps) {
    if (seconds < limit) return formatter.format(-Math.round(seconds / previous), unit);
    previous = limit;
  }
  return formatter.format(-Math.round(seconds / 31557600), "year");
}

export function absoluteTime(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return "-";
  const date = new Date(typeof value === "number" ? value * 1000 : value);
  if (Number.isNaN(date.getTime())) return "-";
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
