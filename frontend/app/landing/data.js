// Placeholder data for the home-page previews. Wire these to real feeds in production.

export function fmt(n) {
  const dec = Math.abs(n) >= 1000 ? 2 : Math.abs(n) < 10 ? 3 : 2;
  return n.toLocaleString("en-US", { minimumFractionDigits: dec, maximumFractionDigits: dec });
}

// Live Signals terminal rows
export const SIGNALS = [
  { i: 1, sym: "/NQ", model: "AGG", prof: "Neutral", profDir: "↓", profCls: "p-neutral", side: "SHORT", alert: "ENTRY", alertCls: "alert-entry", last: 30568.25, chg: 2.0, pct: 0.01, entry: 30566.84, stop: 30591.5, tgt: 30514.44, sw: 83.2, typ: 86.57, swCls: "neg", edge: "var(--violet)" },
  { i: 2, sym: "/HG", model: "AGG", prof: "Avoid", profDir: "", profCls: "p-avoid", side: "SHORT", alert: "ENTRY", alertCls: "alert-entry", last: 6.638, chg: 0.09, pct: 1.3, entry: 6.633, stop: 6.644, tgt: 6.615, sw: 94.3, typ: 0.04, swCls: "neg", edge: "var(--gold)" },
  { i: 3, sym: "/NQ", model: "CON", prof: "Neutral", profDir: "↓", profCls: "p-neutral", side: "SHORT", alert: "NEAR", alertCls: "alert-near", last: 30568.25, chg: 2.0, pct: 0.01, entry: 30593.52, stop: 30618.25, tgt: 30487.76, sw: 63.6, typ: 113.25, swCls: "gold", edge: "var(--violet)" },
  { i: 4, sym: "/ES", model: "AGG", prof: "Good", profDir: "↑", profCls: "p-good", side: "SHORT", alert: "NEUTRAL", alertCls: "alert-neu", last: 7601.5, chg: -11.75, pct: -0.15, entry: 7609.25, stop: 7615.0, tgt: 7591.67, sw: 48.7, typ: 18.49, swCls: "pos", edge: "var(--accent)" },
  { i: 5, sym: "/ES", model: "CON", prof: "Good", profDir: "", profCls: "p-good", side: "SHORT", alert: "NEUTRAL", alertCls: "alert-neu", last: 7601.5, chg: -11.75, pct: -0.15, entry: 7614.94, stop: 7620.75, tgt: 7585.97, sw: 37.2, typ: 24.19, swCls: "pos", edge: "var(--accent)" },
  { i: 6, sym: "/YM", model: "AGG", prof: "Neutral", profDir: "↓", profCls: "p-neutral", side: "LONG", alert: "NEUTRAL", alertCls: "alert-neu", last: 50919.0, chg: -215.0, pct: -0.42, entry: 50859.12, stop: 50817.0, tgt: 50977.7, sw: 48.8, typ: 124.97, swCls: "pos", edge: "var(--gold)" },
  { i: 7, sym: "/RTY", model: "AGG", prof: "Good", profDir: "↑", profCls: "p-good", side: "SHORT", alert: "NEUTRAL", alertCls: "alert-neu", last: 2907.9, chg: -1.8, pct: -0.06, entry: 2912.21, stop: 2915.4, tgt: 2901.41, sw: 40.9, typ: 10.5, swCls: "pos", edge: "var(--pos)" },
  { i: 8, sym: "/GC", model: "AGG", prof: "Good", profDir: "↑", profCls: "p-good", side: "LONG", alert: "ENTRY", alertCls: "alert-entry", last: 4557.4, chg: 51.1, pct: 1.13, entry: 4545.37, stop: 4538.4, tgt: 4573.37, sw: 33.2, typ: 25.33, swCls: "gold", edge: "var(--gold)" },
  { i: 9, sym: "/CL", model: "WIDE", prof: "Avoid", profDir: "↑", profCls: "p-avoid", side: "LONG", alert: "NEAR", alertCls: "alert-near", last: 71.84, chg: 0.42, pct: 0.59, entry: 71.62, stop: 71.3, tgt: 72.48, sw: 27.1, typ: 0.46, swCls: "pos", edge: "var(--violet)" },
];

// Ticker tape
export const TAPE = [
  { sym: "/ES", px: "7601.50", chg: "-0.15%", up: false },
  { sym: "/NQ", px: "30568.25", chg: "+0.01%", up: true },
  { sym: "/YM", px: "50919.00", chg: "-0.42%", up: false },
  { sym: "/RTY", px: "2907.90", chg: "-0.06%", up: false },
  { sym: "/GC", px: "4557.40", chg: "+1.13%", up: true },
  { sym: "/CL", px: "71.84", chg: "+0.59%", up: true },
  { sym: "/HG", px: "6.638", chg: "+1.30%", up: true },
  { sym: "BTC", px: "67204", chg: "+2.41%", up: true },
  { sym: "USD/JPY", px: "159.74", chg: "+0.08%", up: true },
  { sym: "EUR/USD", px: "1.1647", chg: "+0.14%", up: true },
  { sym: "VIX", px: "16.12", chg: "-3.20%", up: false },
  { sym: "NKY", px: "38114", chg: "-0.48%", up: false },
  { sym: "HSI", px: "19021", chg: "+1.84%", up: true },
];

// Market Profile TPO ladder: [price, letters, flag]  flag: "" | "va" | "poc" | "ib"
export const TPO = [
  ["7626.00", "k", ""],
  ["7625.50", "klm", ""],
  ["7625.00", "iklmn", ""],
  ["7624.50", "iklmno", "va"],
  ["7624.00", "hiklmnop", "va"],
  ["7623.50", "hiklmnopqr", "va"],
  ["7623.00", "hiklmnopqrs", "poc"],
  ["7622.50", "hiklmnopqr", "va"],
  ["7622.00", "hiklnopq", "va"],
  ["7621.50", "iklnop", "va"],
  ["7621.00", "iknop", "va"],
  ["7620.50", "iknp", ""],
  ["7620.00", "ikn", ""],
  ["7619.50", "ik2", ""],
  ["7619.00", "i2", ""],
  ["7618.50", "i2", ""],
  ["7618.00", "i12", "ib"],
  ["7617.50", "ils2", "ib"],
  ["7617.00", "lps2", "ib"],
  ["7616.50", "lpqrs2", "ib"],
  ["7616.00", "klnpqrs12", "ib"],
];

export const INSTRUMENTS = ["/ES", "/NQ", "/YM", "/RTY", "/GC", "/CL", "/SI", "/NG", "/HG", "/ZB", "/BTC"];
