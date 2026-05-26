# doMyTrade — Agent Instructions

## Trading Terminology — Do NOT treat as ticker symbols

Many 3–4 character abbreviations used in trading discussion are **market concepts, not stock tickers**.
Do NOT look up, quote, or resolve the following as ticker symbols:

| Term | Meaning |
|------|---------|
| ATH  | All-Time High |
| ATL  | All-Time Low |
| VAH  | Value Area High |
| VAL  | Value Area Low |
| POC  | Point of Control |
| VWAP | Volume Weighted Average Price |
| RTH  | Regular Trading Hours |
| ETH  | Extended Trading Hours |
| IB   | Initial Balance |
| PDH  | Previous Day High |
| PDL  | Previous Day Low |
| PDC  | Previous Day Close |
| HOD  | High of Day |
| LOD  | Low of Day |
| ORH  | Opening Range High |
| ORL  | Opening Range Low |
| COT  | Commitments of Traders |
| OI   | Open Interest |
| HVN  | High Volume Node |
| LVN  | Low Volume Node |

Only use the trading MCP tools (`mcp__trading__*`) when the user explicitly names a **ticker symbol**
(e.g. `/ES`, `/NQ`, `AAPL`, `SPY`) or asks for a quote/scan by name.
