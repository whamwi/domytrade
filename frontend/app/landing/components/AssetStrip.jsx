export default function AssetStrip() {
  const chips = [
    ["240+", "instruments"],
    ["/ES", "/NQ /YM /RTY"],
    ["/GC", "/CL /HG"],
    ["EQUITIES", ""],
    ["FX", "majors"],
    ["SECTORS", "11"],
  ];
  return (
    <div className="assets">
      <div className="wrap">
        <span className="lbl">Tracks across</span>
        {chips.map((c, k) => (
          <span className="asset-chip" key={k}><b>{c[0]}</b> {c[1]}</span>
        ))}
      </div>
    </div>
  );
}
