import { useState, useEffect, useRef } from "react";
import { SIGNALS, fmt } from "../data.js";
import Diamond from "./Diamond.jsx";

function SignalRow({ r }) {
  const [last, setLast] = useState(r.last);
  const [flash, setFlash] = useState("");
  const seed = useRef(r.last);

  useEffect(() => {
    let alive = true;
    const tick = () => {
      if (!alive) return;
      const step = (Math.random() - 0.48) * (seed.current * 0.0002 + 0.01);
      const next = last + step;
      setFlash(next >= last ? "flash-up" : "flash-dn");
      setLast(next);
      setTimeout(() => alive && setFlash(""), 600);
    };
    const id = setInterval(tick, 1800 + Math.random() * 2600);
    return () => { alive = false; clearInterval(id); };
  }, [last]);

  const swColor = r.swCls === "neg" ? "var(--neg)" : r.swCls === "gold" ? "var(--gold)" : "var(--pos)";
  return (
    <tr style={{ "--edge": r.edge }}>
      <td className="l"><span className="t-idx">{r.i}</span></td>
      <td className="l"><span className="t-sym">{r.sym}</span></td>
      <td><span className={"t-last " + flash}>{fmt(last)}</span></td>
      <td className={r.chg >= 0 ? "pos" : "neg"}>
        {r.chg >= 0 ? "+" : ""}{fmt(r.chg)} <span style={{ opacity: 0.7 }}>({r.pct >= 0 ? "+" : ""}{r.pct}%)</span>
      </td>
      <td className="l"><span className={"badge b-" + r.model.toLowerCase()}>{r.model}</span></td>
      <td className="l"><span className={"pill " + r.profCls}>{r.prof} {r.profDir}</span></td>
      <td className="l"><span className={r.side === "LONG" ? "side-long" : "side-short"}>{r.side}</span></td>
      <td className="l"><span className={r.alertCls}>{r.alert !== "NEUTRAL" ? "• " : ""}{r.alert}</span></td>
      <td>{fmt(r.entry)}</td>
      <td className="t-stop">{fmt(r.stop)}</td>
      <td className="t-tgt">{fmt(r.tgt)}</td>
      <td className="l">
        <div className="swing">
          <div className="swing-track"><div className="swing-fill" style={{ width: r.sw + "%", background: swColor }} /></div>
          <div className="swing-txt"><b>{r.sw}%</b> vs {r.typ} typ</div>
        </div>
      </td>
    </tr>
  );
}

function LiveClock() {
  const [t, setT] = useState("");
  useEffect(() => {
    const f = () => {
      const d = new Date();
      const p = (n) => String(n).padStart(2, "0");
      setT(`${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())} ET`);
    };
    f();
    const id = setInterval(f, 1000);
    return () => clearInterval(id);
  }, []);
  return <span>{t}</span>;
}

export default function Terminal() {
  return (
    <div className="terminal">
      <div className="t-bar">
        <div className="t-brand">
          <Diamond size={20} inner={{ width: 7, height: 7 }} /> DOMYTRADE <span className="t-pre">PRE-MARKET</span>
        </div>
        <div className="t-stats">
          <span>Signals <b>30</b></span>
          <span>Bull <b style={{ color: "var(--pos)" }}>12</b></span>
          <span>Bear <b style={{ color: "var(--neg)" }}>18</b></span>
          <span>Fear Index <b style={{ color: "var(--gold)" }}>16.12</b></span>
        </div>
        <div className="t-right">
          <LiveClock />
          <span className="live"><span className="live-dot" /> LIVE</span>
        </div>
      </div>
      <div className="t-filters">
        <span className="t-tab on">ALL</span>
        <span className="t-tab">LONGS</span>
        <span className="t-tab">SHORTS</span>
        <span className="t-sep" />
        <span className="t-tab grp">ALL MODELS</span>
        <span className="t-tab">AGGRO</span>
        <span className="t-tab">CONSERV</span>
        <span className="t-tab">WIDE</span>
        <span className="t-sep" />
        <span className="t-tab grp">ALL ASSETS</span>
        <span className="t-tab">EQUITIES</span>
        <span className="t-tab">FUTURES</span>
        <span className="t-tab">★ WATCH</span>
      </div>
      <div style={{ overflowX: "auto" }}>
        <table className="t-table">
          <thead>
            <tr>
              <th className="l">#</th>
              <th className="l">Symbol</th>
              <th>Last</th>
              <th>Chg</th>
              <th className="l">Model</th>
              <th className="l">Profile</th>
              <th className="l">Side</th>
              <th className="l">Alert</th>
              <th>Entry</th>
              <th>Stop</th>
              <th>Target</th>
              <th className="l">Daily Swing</th>
            </tr>
          </thead>
          <tbody>
            {SIGNALS.map((r) => <SignalRow key={r.i} r={r} />)}
          </tbody>
        </table>
      </div>
      <div className="term-overlay">
        <button className="btn btn-primary btn-lg">Open the live terminal →</button>
      </div>
    </div>
  );
}
