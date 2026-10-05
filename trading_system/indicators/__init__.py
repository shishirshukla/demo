import numpy as np
import pandas as pd


def sma(s, n):
    return s.rolling(n, min_periods=n).mean()


def ema(s, n):
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def wilder(s, n):
    """Wilder smoothing seeded with the first n valid values' mean."""
    values = s.to_numpy(dtype=float)
    output = np.full(len(values), np.nan)
    seed = []
    last = np.nan
    for i, value in enumerate(values):
        if np.isnan(value):
            continue
        if np.isnan(last):
            seed.append(value)
            if len(seed) == n:
                last = float(np.mean(seed))
                output[i] = last
        else:
            last = (last * (n - 1) + value) / n
            output[i] = last
    return pd.Series(output, index=s.index)


def atr(frame, n=14):
    previous = frame.close.shift()
    tr = pd.concat(
        [
            frame.high - frame.low,
            (frame.high - previous).abs(),
            (frame.low - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return wilder(tr, n)


def adx(frame, n=14):
    up, down = frame.high.diff(), -frame.low.diff()
    plus = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=frame.index)
    minus = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=frame.index)
    a = atr(frame, n).replace(0, np.nan)
    p, m = 100 * wilder(plus, n) / a, 100 * wilder(minus, n) / a
    dx = 100 * (p - m).abs() / (p + m).replace(0, np.nan)
    dx = dx.where((p + m) != 0, 0)
    return wilder(dx, n)


def rsi(s, n=5):
    diff = s.diff()
    gain, loss = wilder(diff.clip(lower=0), n), wilder(-diff.clip(upper=0), n)
    result = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    return result.where(loss != 0, 100).where((gain + loss) != 0, 50)


def vwap(frame):
    session = frame.timestamp.dt.date
    value = ((frame.high + frame.low + frame.close) / 3) * frame.volume
    return value.groupby(session).cumsum() / frame.volume.groupby(
        session
    ).cumsum().replace(0, np.nan)


def breadth(frame):
    eligible = frame[frame.sma50.notna()]
    return float((eligible.close > eligible.sma50).mean()) if len(eligible) else 0.5


def percentile(s):
    return s.rank(pct=True, method="average")
