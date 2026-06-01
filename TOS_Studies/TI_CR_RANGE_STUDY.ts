# Name:       TI_CR_STUDY.ts
# Strategy:   Clearing Range (CR) Breach-Retreat
# CR:         9:30–10:00 AM ET Initial Balance (6 x 5-min bars)
# Breach:     10:00–10:30 AM bar CLOSES >= CR Top + 2 ticks  (triggers LONG setup)
#             10:00–10:30 AM bar CLOSES <= CR Bot - 2 ticks  (triggers SHORT setup)
# Entry:      CR Mid on retreat | T1: Breach level | Stop: Opposite CR extreme
# Backtest:   91.9% WR | ~$20K net/contract/year (/ES 365 days)
#             82–96% WR across /GC /CL /SI /PL /HG /BTC

input alertsOn   = no;
input showLabels = yes;
input breachTicks = 2;

AddLabel(showLabels, "CR Breach-Retreat", Color.WHITE);

# ─── IB Window: 9:30–10:00 AM ET (6 x 5-min bars) ──────────────────────────
def newDay = GetYYYYMMDD() != GetYYYYMMDD()[1];
def inIB   = SecondsTillTime(1000) > 0 and SecondsTillTime(930) <= 0;
def ibDone = SecondsTillTime(1000) <= 0;

# Accumulate MAX high / MIN low across all 5-min bars in the IB window
# Resets on each new trading day
def crHraw = if newDay    then (if inIB then high else 0)
             else if inIB then Max(crHraw[1], high)
             else              crHraw[1];

def crLraw = if newDay    then (if inIB then low else 999999)
             else if inIB then Min(crLraw[1], low)
             else              crLraw[1];

# Lock and display levels only after IB is complete (10:00 AM onward)
def crH = if ibDone and crHraw > 0      then crHraw else Double.NaN;
def crL = if ibDone and crLraw < 999999 then crLraw else Double.NaN;

def crM = if !IsNaN(crH) and !IsNaN(crL) then (crH + crL) / 2 else Double.NaN;

# Breach confirmation levels (2 ticks outside IB extremes)
def ts         = TickSize();
def breachUp   = if !IsNaN(crH) then Round((crH + breachTicks * ts) / ts, 0) * ts else Double.NaN;
def breachDown = if !IsNaN(crL) then Round((crL - breachTicks * ts) / ts, 0) * ts else Double.NaN;

# ─── Breach detection: 10:00–10:30 AM bar only ───────────────────────────────
def inBreachBar = SecondsTillTime(1030) > 0 and SecondsTillTime(1000) <= 0;
def topBreach   = inBreachBar and close >= breachUp;
def botBreach   = inBreachBar and close <= breachDown;

# Carry breach state forward through the session (reset each new day)
# LOCK rule: once a breach is recorded, it cannot be overridden by an opposite breach.
# Priority: (1) already locked → hold; (2) first breach seen → lock it; (3) no breach → 0
def breachState = if newDay              then 0
                  else if breachState[1] != 0 then breachState[1]
                  else if topBreach          then  1
                  else if botBreach          then -1
                  else                           0;

# ─── Plots ────────────────────────────────────────────────────────────────────
plot pCRTop  = crH;
plot pCRBot  = crL;
plot pCRMid  = crM;
plot pBrchUp = breachUp;
plot pBrchDn = breachDown;

# CR Top — orange solid
pCRTop.SetDefaultColor(Color.ORANGE);
pCRTop.SetPaintingStrategy(PaintingStrategy.LINE);
pCRTop.SetStyle(Curve.FIRM);
pCRTop.SetLineWeight(2);

# CR Bottom — orange solid
pCRBot.SetDefaultColor(Color.ORANGE);
pCRBot.SetPaintingStrategy(PaintingStrategy.LINE);
pCRBot.SetStyle(Curve.FIRM);
pCRBot.SetLineWeight(2);

# CR Mid — yellow dashed (retreat entry)
pCRMid.SetDefaultColor(Color.YELLOW);
pCRMid.SetPaintingStrategy(PaintingStrategy.LINE);
pCRMid.SetStyle(Curve.SHORT_DASH);
pCRMid.SetLineWeight(1);

# Breach Up — green dashed (must CLOSE above for top breach)
pBrchUp.SetDefaultColor(Color.LIGHT_GREEN);
pBrchUp.SetPaintingStrategy(PaintingStrategy.LINE);
pBrchUp.SetStyle(Curve.SHORT_DASH);
pBrchUp.SetLineWeight(1);

# Breach Down — red dashed (must CLOSE below for bottom breach)
pBrchDn.SetDefaultColor(Color.LIGHT_RED);
pBrchDn.SetPaintingStrategy(PaintingStrategy.LINE);
pBrchDn.SetStyle(Curve.SHORT_DASH);
pBrchDn.SetLineWeight(1);

# CR zone shading (full IB range)
AddCloud(pCRTop, pCRBot, Color.DARK_GRAY, Color.DARK_GRAY);

# 38.2%–61.8% Fibonacci golden zone around the 50% mid (entry zone)
# Uses crH/crL (actual IB range), not the breach buffer levels
AddCloud(
    if !IsNaN(crH) then crL + (crH - crL) * 0.618 else Double.NaN,
    if !IsNaN(crH) then crL + (crH - crL) * 0.382 else Double.NaN,
    CreateColor(128, 128, 128), CreateColor(128, 128, 128));

# ─── Vertical lines ───────────────────────────────────────────────────────────
AddVerticalLine(SecondsTillTime(1000) == 0, "IB End", Color.ORANGE, Curve.SHORT_DASH);
AddVerticalLine(SecondsTillTime(1030) == 0, "Breach Window End", Color.DARK_ORANGE, Curve.SHORT_DASH);

# ─── Labels ───────────────────────────────────────────────────────────────────
AddLabel(showLabels and !IsNaN(crH),
    "CR Top: " + AsPrice(Round(crH / ts, 0) * ts) +
    "  Breach >: " + AsPrice(Round(breachUp / ts, 0) * ts),
    Color.ORANGE);

AddLabel(showLabels and !IsNaN(crL),
    "CR Bot: " + AsPrice(Round(crL / ts, 0) * ts) +
    "  Breach <: " + AsPrice(Round(breachDown / ts, 0) * ts),
    Color.ORANGE);

AddLabel(showLabels and !IsNaN(crM),
    "CR Mid: " + AsPrice(Round(crM / ts, 0) * ts),
    Color.YELLOW);

# Active trade setup — top breach
AddLabel(showLabels and breachState == 1,
    "BUY DIPS  >>  Long @ " + AsPrice(Round(crM / ts, 0) * ts) +
    "  T1: " + AsPrice(Round(breachUp / ts, 0) * ts) +
    "  Stop: " + AsPrice(Round(breachDown / ts, 0) * ts),
    Color.LIGHT_GREEN);

# Active trade setup — bottom breach
AddLabel(showLabels and breachState == -1,
    "SELL RALLIES  >>  Short @ " + AsPrice(Round(crM / ts, 0) * ts) +
    "  T1: " + AsPrice(Round(breachDown / ts, 0) * ts) +
    "  Stop: " + AsPrice(Round(breachUp / ts, 0) * ts),
    Color.LIGHT_RED);

# ─── Alerts ───────────────────────────────────────────────────────────────────
Alert(alertsOn and topBreach, "CR TOP BREACH — Trending Up: Buy Dips. Watch retreat to Mid for LONG entry.", Alert.ONCE, Sound.Chimes);
Alert(alertsOn and botBreach, "CR BOT BREACH — Trending Down: Sell Rallies. Watch bounce to Mid for SHORT entry.", Alert.ONCE, Sound.Chimes);
Alert(alertsOn and breachState == 1 and low <= crM, "CR LONG ENTRY — Price at CR Mid.", Alert.ONCE, Sound.Chimes);
Alert(alertsOn and breachState == -1 and high >= crM, "CR SHORT ENTRY — Price at CR Mid.", Alert.ONCE, Sound.Chimes);
