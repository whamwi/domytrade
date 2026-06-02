import { TAPE } from "../data.js";

export default function TickerTape() {
  const items = [...TAPE, ...TAPE];
  return (
    <div className="tape" aria-hidden="true">
      <div className="tape-track">
        {items.map((t, k) => (
          <span className="tick" key={k}>
            <span className="sym">{t.sym}</span>
            <span className="px">{t.px}</span>
            <span className="chg" style={{ color: t.up ? "var(--pos)" : "var(--neg)" }}>{t.chg}</span>
          </span>
        ))}
      </div>
    </div>
  );
}
