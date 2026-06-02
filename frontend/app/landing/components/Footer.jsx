import Diamond from "./Diamond.jsx";

export default function Footer() {
  const cols = [
    { h: "Product", l: ["Live signals", "Strategies", "Models", "Market Profile", "Changelog"] },
    { h: "Markets", l: ["Futures", "Equities", "FX", "Sectors", "Fear index"] },
    { h: "Company", l: ["About", "Methodology", "Contact", "Status"] },
  ];
  return (
    <footer className="footer">
      <div className="wrap">
        <div className="footer-top">
          <div>
            <a href="#" className="brand">
              <Diamond />
              <span className="word">DOMY<b>TRADE</b></span>
            </a>
            <p className="blurb">Market-profile, volatility and time-frame signals for traders who live at the open.</p>
          </div>
          {cols.map((c, k) => (
            <div className="fcol" key={k}>
              <h5>{c.h}</h5>
              {c.l.map((x, j) => <a href="#" key={j}>{x}</a>)}
            </div>
          ))}
        </div>
        <div className="footer-bottom">
          <p className="disc">© 2026 Domytrade. Signals are informational and not investment advice. Trading futures, equities and FX involves substantial risk of loss.</p>
          <div className="legal"><a href="#">Terms</a><a href="#">Privacy</a><a href="#">Disclosures</a></div>
        </div>
      </div>
    </footer>
  );
}
