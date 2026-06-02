import { Ico, ICONS } from "./Icon.jsx";

export default function Features() {
  const feats = [
    { ic: "bell", t: "Real-time alerts", d: "ENTRY and NEAR pings the instant a setup arms — push, email or in-terminal." },
    { ic: "target", t: "Entry · Stop · Target", d: "Every signal ships a complete plan with a pre-computed risk/reward ratio." },
    { ic: "gauge", t: "Daily swing meter", d: "See how much of the expected range is spent before you ever take the trade." },
    { ic: "layers", t: "Futures, equities & FX", d: "240+ instruments and 11 sectors, scored on one consistent framework." },
    { ic: "flash", t: "Fear & breadth index", d: "Live bull/bear breadth and a fear gauge to read the tape at a glance." },
    { ic: "clock", t: "Pre / regular / after", d: "Sessions tracked independently so signals never bleed across the bell." },
  ];
  return (
    <section className="section">
      <div className="wrap">
        <div className="sec-head reveal">
          <span className="kicker">Built for the open</span>
          <h2 className="sec-title">Everything you need on the chart, nothing you don't.</h2>
        </div>
        <div className="features">
          {feats.map((f, k) => (
            <div className="feature reveal" key={k}>
              <div className="fi"><Ico d={ICONS[f.ic]} size={20} /></div>
              <h4>{f.t}</h4>
              <p>{f.d}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
