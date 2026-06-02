// Tiny inline line-icons. Swap for your codebase's icon set if it has one.
export const ICONS = {
  profile: ["M3 20h18", "M6 20V10", "M10 20V5", "M14 20V8", "M18 20V13"],
  vol: ["M3 12h3l2-7 4 14 2-9 2 4h5"],
  clock: ["M12 7v5l3 2", "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z"],
  bell: ["M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9", "M13.7 21a2 2 0 0 1-3.4 0"],
  target: ["M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z", "M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z", "M12 13a1 1 0 1 0 0-2 1 1 0 0 0 0 2Z"],
  layers: ["M12 2 2 7l10 5 10-5-10-5Z", "m2 17 10 5 10-5", "m2 12 10 5 10-5"],
  gauge: ["M12 14a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z", "M13.4 12.6 19 7", "M5 19a9 9 0 1 1 14 0"],
  flash: ["M13 2 3 14h7l-1 8 10-12h-7l1-8Z"],
  check: ["M20 6 9 17l-5-5"],
};

export function Ico({ d, size = 22, sw = 1.6 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round">
      {Array.isArray(d) ? d.map((p, k) => <path key={k} d={p} />) : <path d={d} />}
    </svg>
  );
}
