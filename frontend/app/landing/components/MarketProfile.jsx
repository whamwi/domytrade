import { TPO, INSTRUMENTS } from "../data.js";

function Spark() {
  return (
    <svg className="spark" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M12 2l1.8 6.2L20 10l-6.2 1.8L12 18l-1.8-6.2L4 10l6.2-1.8z" />
    </svg>
  );
}

export default function MarketProfile() {
  return (
    <section className="section" id="market-profile" style={{ background: "var(--bg-2)", borderTop: "1px solid var(--line)", borderBottom: "1px solid var(--line)" }}>
      <div className="wrap">
        <div className="sec-head reveal">
          <span className="kicker">Inside every signal</span>
          <h2 className="sec-title">Read the auction like an institution.</h2>
          <p className="sec-sub">Each signal is built on a live J. Dalton TPO profile — value area, point of control, initial balance — then our AI narrates the auction in plain English, in real time.</p>
        </div>
      </div>
      <div className="mp-shell reveal">
        <div className="mp">
          <div className="mp-bar">
            <div>
              <div className="mp-title">Market Profile</div>
              <div className="mp-sub">J. DALTON TPO · VALUE AREA · OPENING TYPE · DAY CLASSIFICATION · 7:52 AM ET</div>
            </div>
            <div className="mp-chips">
              {INSTRUMENTS.map((s, k) => (
                <span className={"mp-chip" + (k === 0 ? " on" : "")} key={k}>{s}</span>
              ))}
            </div>
          </div>

          <div className="mp-stats">
            <div className="mp-last"><span className="l">/ES</span><span className="v">7598.00</span></div>
            <div className="mp-grp">
              <span className="gh">Prior RTH</span>
              <div className="gr">
                <div><div className="gk">POC</div><div className="gv">7589.50</div></div>
                <div><div className="gk">VAH</div><div className="gv">7614.50</div></div>
                <div><div className="gk">VAL</div><div className="gv">7581.50</div></div>
              </div>
            </div>
            <div className="mp-grp ov">
              <span className="gh">Overnight</span>
              <div className="gr">
                <div><div className="gk">ONH</div><div className="gv">7612.00</div></div>
                <div><div className="gk">ONL</div><div className="gv">7576.50</div></div>
              </div>
            </div>
            <span className="mp-tag ib">Initial Balance</span>
            <span className="mp-tag dev">Developing</span>
          </div>

          <div className="mp-body">
            <div className="mp-ladder">
              <div className="lh"><span>Prior ON · 31P</span><span>Prior RTH</span><span>Overnight · 28P</span><span>Today · OP</span></div>
              {TPO.map((r, k) => (
                <div className={"tpo-row " + (r[2] === "poc" ? "poc va" : r[2] === "ib" ? "ib-row" : r[2])} key={k}>
                  <span className="tpo-px">{r[0]}</span>
                  <span className="tpo-letters">{r[1]}</span>
                </div>
              ))}
            </div>

            <div className="mp-read">
              <div className="read-head"><span className="rh">AI Live Read</span><span className="pm">Pre-Market</span></div>
              <div className="read-guide">
                <div className="gt">Guidance</div>
                <div className="gx">RTH has not started. Overnight range <b>7576.50 – 7612.00</b>. ON POC <b>7595.50</b>.</div>
              </div>
              <div className="regime"><span className="rl">Prior session</span><span className="rb">↔ No-Trade · LONG regime</span></div>
              <div className="read-card">
                <h6>OA open — overnight traders in control</h6>
                <p>Open printed inside the overnight range. Two-sided auction expected: buy VAL, sell VAH until a decisive break.</p>
              </div>
              <div className="read-card">
                <h6>IB probed below ONL, closed back inside</h6>
                <p>The probe was rejected — overnight buyers held the low. Treat as two-sided OA; watch for a close below ONL to confirm acceptance.</p>
              </div>
              <div className="read-card hot">
                <h6>Inventory misalignment — potential unwind</h6>
                <p>Overnight trended higher but IB sits below ON POC. Longs are offside — expect liquidation pressure. Bearish until IB reclaims ON POC.</p>
              </div>
              <button className="ask-ai"><Spark /> Ask AI</button>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
