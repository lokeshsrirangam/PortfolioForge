"""Small, auditable long-only moving-average backtest for uploaded price series."""

import io
import numpy as np
import pandas as pd


def run_sma_backtest(frame, fast_window=20, slow_window=50, cost_bps=5.0):
    """Run a close-to-close SMA crossover with next-bar execution.

    The signal is formed from close[t] and applied to return[t+1], avoiding
    lookahead. Costs are charged on each position change. No leverage or shorting.
    """
    fast_window = int(fast_window)
    slow_window = int(slow_window)
    cost_bps = float(cost_bps)
    if fast_window < 2 or slow_window <= fast_window or slow_window > 500:
        raise ValueError('Use windows where 2 ≤ fast < slow ≤ 500.')
    if not np.isfinite(cost_bps) or not 0 <= cost_bps <= 100:
        raise ValueError('Trading cost must be between 0 and 100 basis points.')
    if len(frame) <= slow_window:
        raise ValueError(f'Upload at least {slow_window + 1} rows for the selected slow window.')

    data = frame.copy().sort_values('date').reset_index(drop=True)
    prices = data['close'].astype(float)
    fast_average = prices.rolling(fast_window, min_periods=fast_window).mean()
    slow_average = prices.rolling(slow_window, min_periods=slow_window).mean()
    signal = (fast_average > slow_average).astype(float).where(slow_average.notna(), 0.0)
    position = signal.shift(1).fillna(0.0)
    asset_returns = prices.pct_change().fillna(0.0)
    turnover = position.diff().abs().fillna(position.abs())
    strategy_returns = position * asset_returns - turnover * (cost_bps / 10_000.0)
    strategy_returns = strategy_returns.clip(lower=-1.0)
    equity = (1.0 + strategy_returns).cumprod()
    benchmark_equity = (1.0 + asset_returns).cumprod()
    drawdown = equity / equity.cummax() - 1.0

    observations = max(len(data) - 1, 1)
    daily_vol = float(strategy_returns.std(ddof=1)) if len(strategy_returns) > 1 else 0.0
    sharpe = (float(strategy_returns.mean()) / daily_vol * np.sqrt(252)) if daily_vol > 0 else 0.0
    annualized_return = float(equity.iloc[-1] ** (252 / observations) - 1.0)
    annualized_volatility = daily_vol * float(np.sqrt(252))
    entries = int((position.diff().fillna(position.iloc[0]) > 0).sum())

    return {
        'start_date': data['date'].iloc[0].strftime('%Y-%m-%d'),
        'end_date': data['date'].iloc[-1].strftime('%Y-%m-%d'),
        'observations': len(data),
        'total_return_pct': float((equity.iloc[-1] - 1.0) * 100),
        'buy_hold_return_pct': float((benchmark_equity.iloc[-1] - 1.0) * 100),
        'annualized_return_pct': annualized_return * 100,
        'annualized_volatility_pct': annualized_volatility * 100,
        'sharpe_ratio': sharpe,
        'max_drawdown_pct': float(drawdown.min() * 100),
        'market_exposure_pct': float(position.mean() * 100),
        'entry_count': entries,
        'cost_bps': cost_bps,
        'dates': data['date'].dt.strftime('%Y-%m-%d').tolist(),
        'strategy_equity': equity.round(6).tolist(),
        'benchmark_equity': benchmark_equity.round(6).tolist(),
    }


def parse_price_csv(raw):
    """Parse and validate a UTF-8 CSV with date and close columns."""
    try:
        frame = pd.read_csv(io.BytesIO(raw), encoding='utf-8-sig')
    except Exception as error:
        raise ValueError('Could not read this CSV. Use a UTF-8 file with date and close columns.') from error
    frame.columns = [str(column).strip().lower() for column in frame.columns]
    if not {'date', 'close'}.issubset(frame.columns):
        raise ValueError('CSV must contain date and close columns.')
    if len(frame) < 3 or len(frame) > 10_000:
        raise ValueError('Include between 3 and 10,000 daily price rows.')
    frame = frame[['date', 'close']].copy()
    frame['date'] = pd.to_datetime(frame['date'], errors='coerce', utc=True).dt.tz_convert(None)
    frame['close'] = pd.to_numeric(frame['close'], errors='coerce')
    if frame.isna().any().any():
        raise ValueError('Every row needs a valid date and numeric close price.')
    if (frame['close'] <= 0).any() or not np.isfinite(frame['close']).all():
        raise ValueError('Close prices must be finite values above zero.')
    if frame['date'].duplicated().any():
        raise ValueError('Dates must be unique; remove duplicate rows before running the backtest.')
    frame = frame.sort_values('date').reset_index(drop=True)
    frame['date'] = frame['date'].dt.normalize()
    return frame
