export default function Diamond({ size = 26, inner }) {
  return (
    <span className="diamond" style={{ width: size, height: size, ...(size <= 20 ? { borderRadius: 5 } : {}) }}>
      <i style={inner} />
    </span>
  );
}
