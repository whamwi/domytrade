# domytrade.app

AI-powered intraday trading signals.

## Stack
- **Frontend:** Next.js → Vercel (domytrade.app)
- **Backend:** FastAPI (Python) → Railway
- **Database:** Supabase (PostgreSQL)
- **Data:** Schwab API

## Features
- **Hourly VB Models** — Conservative & Aggressive volatility signals, recalculating every hour during market hours

## Development
```bash
# Backend
cd backend && pip install -r requirements.txt
uvicorn main:app --reload --port 8000

# Frontend
cd frontend && npm install
npm run dev
```
