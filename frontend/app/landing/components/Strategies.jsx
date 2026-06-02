import { Ico, ICONS } from "./Icon.jsx";

export default function Strategies() {
  return (
    <section className="section" id="strategies">
      <div className="wrap">
        <div className="sec-head reveal">
          <span className="kicker">Three forces, one score</span>
          <h2 className="sec-title">Every signal is graded on the things that actually move price.</h2>
          <p className="sec-sub">We don't guess direction. We weight where price is trading, how violently it's moving, and the horizon you trade — then only surface setups that line up.</p>
        </div>
        <div className="pillars">
          <article className="pillar reveal">
            <span className="num">01</span>
            <div className="ph-icon"><Ico d={ICONS.profile} /></div>
            <h3>Market Profile</h3>
            <p>Value area, point of control and acceptance zones tell us whether price is fair, stretched, or rejecting. Each setup is tagged <b>Good</b>, <b>Neutral</b> or <b>Avoid</b>.</p>
            <div className="viz viz-profile">
              {[18, 28, 40, 58, 74, 86, 72, 54, 38, 26, 16].map((h, k) => (
                <span key={k} style={{ height: h + "%", background: k === 5 ? "var(--accent)" : undefined }} />
              ))}
            </div>
          </article>
          <article className="pillar reveal">
            <span className="num">02</span>
            <div className="ph-icon"><Ico d={ICONS.vol} /></div>
            <h3>Asset Volatility</h3>
            <p>Live realized vs. typical range sizes every stop and target. The <b>daily swing</b> meter shows exactly how much of the day's expected move is already spent.</p>
            <div className="viz">
              <svg viewBox="0 0 200 86" preserveAspectRatio="none">
                <polyline points="0,60 20,52 40,64 60,40 80,55 100,28 120,46 140,20 160,38 180,14 200,30" fill="none" stroke="var(--gold)" strokeWidth="2" />
                <polyline points="0,60 20,52 40,64 60,40 80,55 100,28 120,46 140,20 160,38 180,14 200,30 200,86 0,86" fill="oklch(0.80 0.14 84 / 0.12)" stroke="none" />
              </svg>
            </div>
          </article>
          <article className="pillar reveal">
            <span className="num">03</span>
            <div className="ph-icon"><Ico d={ICONS.clock} /></div>
            <h3>Trading Time Frame</h3>
            <p>Scalp, intraday or swing — pick your horizon and signals recalibrate. Pre-market, regular and after-hours sessions are scored independently.</p>
            <div className="viz viz-tf">
              {[40, 65, 30, 80, 55, 90, 45, 70, 35, 60].map((h, k) => (
                <span key={k} className={k === 5 ? "on" : ""} style={{ height: h + "%" }} />
              ))}
            </div>
          </article>
        </div>
      </div>
    </section>
  );
}
