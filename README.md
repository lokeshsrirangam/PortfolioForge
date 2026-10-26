# PortfolioForge

### Multi-Asset Portfolio Analytics Platform

PortfolioForge is a small investment workspace for keeping track of a portfolio and exploring how a simple trading rule would have behaved on historical prices. I built it to bring position tracking and practical quant research into one place.

## What you can do

- Add and edit positions across asset classes, and record purchase details and current prices.
- Review portfolio value, unrealized gains and losses, asset allocation, sector exposure, and concentration.
- Import or export holdings with CSV files.
- View recorded portfolio snapshots and generate a portfolio report.
- In the Strategy Lab, upload daily closing prices and compare a long-only moving-average strategy with buy-and-hold.
- Adjust the moving-average windows and transaction-cost assumption, then review returns, drawdown, volatility, Sharpe ratio, and market exposure.

## Run it on your computer

You’ll need Python 3.11 or newer.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
python main.py
```

Then open <http://127.0.0.1:5001> in your browser. The app uses SQLite by default and stores its database at `instance/portfolio.db`.

To use a different database, set `DATABASE_URL`. For a deployment, also set `SESSION_SECRET` to a private random value; don’t use the development default.

## Price data for the Strategy Lab

Upload a CSV with two columns: `date` and `close`. Use daily observations with unique dates and positive closing prices. The file is processed in memory and is not saved by the app.

The backtest is intentionally transparent: a signal calculated from one closing price is applied on the next bar, and estimated costs are charged when the position changes. It is a basic research example, not a live trading system. There is no live price feed, broker connection, short selling, leverage, or parameter optimization.

## A note on the numbers

Portfolio prices are entered by the user; the app does not fetch quotes or convert currencies. Positions must use the same reporting currency for portfolio totals. The concentration index is a measure of how portfolio value is distributed across holdings. The asset-class risk score is a rough heuristic, not a forecast of loss or volatility. Snapshot history starts when the app begins recording it, so it is not historical market data.

Backtest results depend on the uploaded data, the chosen period, and the assumptions. Past results do not predict future performance. PortfolioForge is for tracking and research, not investment advice.

## Built with

Python, Flask, Flask-SQLAlchemy, SQLite, Pandas, NumPy, Bootstrap, and Chart.js.

This project grew from an educational portfolio-tracking starter. If you redistribute or publish it, follow the starter project’s license and attribution terms.
