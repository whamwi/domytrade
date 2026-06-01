# ═══════════════════════════════════════════════════════════════════
# Big3 Squeeze — SCANNER  (First Squeeze Bar)
#
# Parameters match the Big3 Squeeze indicator exactly:
#   length=21, EMA, BB ±2.0, KC 1.5x ATR
#
# HOW TO USE:
#   Scanner → Add Study Filter → Custom → paste → name it
#   Condition: "Scan"  is equal to  1
#   Aggregation: must match your chart (Daily or 65-min)
# ═══════════════════════════════════════════════════════════════════

input length = 21;    # must match indicator's length input
input nBB    = 2.0;   # Bollinger Band width
input nK     = 1.5;   # KC multiplier: 1.0=extra 1.5=original 2.0=pre-squeeze

# ── EMA (matches indicator's AverageType.EXPONENTIAL) ──────────────
def ema = ExpAverage(close, length);

# ── Bollinger Bands ─────────────────────────────────────────────────
def sd  = StDev(close, length);
def bbu = ema + nBB * sd;
def bbl = ema - nBB * sd;

# ── True Range — manual to avoid function quirks ────────────────────
def prevC = close[1];
def tr    = Max(high - low, Max(AbsValue(high - prevC), AbsValue(low - prevC)));

# ── Keltner Channel ─────────────────────────────────────────────────
def atr = ExpAverage(tr, length);
def kcu = ema + nK * atr;
def kcl = ema - nK * atr;

# ── EMA warm-up guard: skip first 3×length bars ─────────────────────
# EMA needs history to converge — without this the scanner sees false squeezes
def warmup = BarNumber() <= length * 3;

# ── Squeeze: BB fully inside KC (exclude warm-up bars) ──────────────
def sq = if warmup then 0 else if bbu < kcu and bbl > kcl then 1 else 0;

# ── Prior bar ───────────────────────────────────────────────────────
def sq1 = sq[1];

# ── First bar of squeeze ─────────────────────────────────────────────
plot Scan = if sq == 1 and sq1 == 0 then 1 else 0;
Scan.SetDefaultColor(Color.YELLOW);
