export default function Hero({ headline }) {
  const [pre, hi, post] = headline.split("|");
  return (
    <header className="hero">
      <div className="hero-grid-bg" />
      <div className="wrap">
        <span className="eyebrow">
          <span className="live-dot" /> AI signal engine · 30 active setups
        </span>
        <h1 className="hero-title">
          {pre}
          <span className="vol">
            {hi}
            <svg viewBox="0 0 200 12" preserveAspectRatio="none" aria-hidden="true">
              <path d="M2 8 Q 50 2 100 7 T 198 5" fill="none" stroke="var(--accent)" strokeWidth="3" strokeLinecap="round" opacity="0.5" />
            </svg>
          </span>
          {post}
        </h1>
        <p className="hero-sub">
          AI-scored buy &amp; sell signals across <b>Market Profile</b>, <b>asset volatility</b>, and your
          <b> trading time frame</b> — each setup ships with entry, stop and target. Across futures, equities and FX.
        </p>
        <div className="hero-cta">
          <a href="#signup" className="btn btn-primary btn-lg">Create free account →</a>
          <a href="#terminal" className="btn btn-line btn-lg">View live signals</a>
        </div>
        <div className="hero-trust">
          <span className="live-dot" style={{ width: 6, height: 6 }} /> No card required · Streaming since pre-market · Cancel anytime
        </div>

        <div className="statstrip">
          <div className="stat"><div className="k">Active signals</div><div className="v mono">30</div></div>
          <div className="stat"><div className="k">Bull / Bear</div><div className="v mono"><span style={{ color: "var(--pos-dim)" }}>12</span> / <span style={{ color: "var(--neg-dim)" }}>18</span></div></div>
          <div className="stat"><div className="k">Fear index</div><div className="v mono" style={{ color: "var(--gold)" }}>16.12</div></div>
          <div className="stat"><div className="k">Assets tracked</div><div className="v mono">240+</div></div>
        </div>
      </div>
    </header>
  );
}
