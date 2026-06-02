import Diamond from "./Diamond.jsx";

export default function Nav() {
  return (
    <nav className="nav">
      <div className="nav-inner">
        <a href="#" className="brand">
          <Diamond />
          <span className="word">DOMY<b>TRADE</b></span>
        </a>
        <div className="nav-links">
          <a href="#terminal">Live Signals</a>
          <a href="#strategies">Strategies</a>
          <a href="#models">Models</a>
          <a href="#market-profile">Market Profile</a>
        </div>
        <div className="nav-right">
          <div className="status-pill">
            <span className="live-dot" /> MARKET OPEN · <span style={{ color: "var(--gold)" }}>Fear 16.12</span>
          </div>
          <a href="#" className="btn btn-ghost">Sign in</a>
          <a href="#signup" className="btn btn-primary">Create account</a>
        </div>
      </div>
    </nav>
  );
}
