export default function Models() {
  const models = [
    { cls: "b-agg", name: "AGG", title: "Aggressive", desc: "Tight entries that fire early — built for traders who want the first move and accept more noise.", a: ["Tighter", "Earlier", "More"] },
    { cls: "b-con", name: "CON", title: "Conservative", desc: "Waits for confirmation and value alignment. Fewer setups, higher conviction, wider room to work.", a: ["Confirmed", "Wider", "Higher"] },
    { cls: "b-wide", name: "WIDE", title: "Wide / Position", desc: "Structural levels for swing and position trades. Sized to the full daily and weekly range.", a: ["Range-based", "Swing", "Lower"] },
  ];
  const metaKeys = ["Risk", "Timing", "Volume"];
  return (
    <section className="section" id="models" style={{ background: "var(--bg-2)", borderTop: "1px solid var(--line)", borderBottom: "1px solid var(--line)" }}>
      <div className="wrap">
        <div className="sec-head reveal">
          <span className="kicker">Pick your style</span>
          <h2 className="sec-title">Three models on every instrument.</h2>
          <p className="sec-sub">The same market, scored three ways. Run them side by side and trade the one that fits your risk.</p>
        </div>
        <div className="models">
          {models.map((m, k) => (
            <article className="model-card reveal" key={k}>
              <span className={"badge " + m.cls}>{m.name}</span>
              <h4>{m.title}</h4>
              <p>{m.desc}</p>
              <div className="meta">
                {m.a.map((x, j) => (
                  <div key={j}><div className="mk">{metaKeys[j]}</div><div className="mv">{x}</div></div>
                ))}
              </div>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}
