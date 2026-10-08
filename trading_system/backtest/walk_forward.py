from copy import deepcopy
from dataclasses import dataclass

import pandas as pd

from .engine import BacktestEngine
from .metrics import metrics


@dataclass(frozen=True)
class Window:
    train_start: str
    train_end: str
    validation_start: str
    validation_end: str


def windows(start, end, train_years=5, validate_years=1, step_years=1):
    if min(train_years, validate_years, step_years) < 1:
        raise ValueError("Window sizes must be positive years")
    cursor, end = pd.Timestamp(start), pd.Timestamp(end)
    while (
        cursor
        + pd.DateOffset(years=train_years + validate_years)
        - pd.Timedelta(days=1)
        <= end
    ):
        validation = cursor + pd.DateOffset(years=train_years)
        yield Window(
            str(cursor.date()),
            str((validation - pd.Timedelta(days=1)).date()),
            str(validation.date()),
            str(
                (
                    validation
                    + pd.DateOffset(years=validate_years)
                    - pd.Timedelta(days=1)
                ).date()
            ),
        )
        cursor += pd.DateOffset(years=step_years)


def evaluate_walk_forward(
    dataset, settings, folds, mode="daily", selector=None, progress=None
):
    """Selector receives TRAIN-ONLY data. Frozen settings evaluate validation.

    No optimizer or final-test fitting is provided. Pre-validation data remains
    available to initialize causal indicators, while trading starts at validation.
    """
    from trading_system.data.historical import Dataset

    results = []
    folds = list(folds)
    for i, fold in enumerate(folds):
        params = deepcopy(settings)
        if selector:
            begin, cutoff = (
                pd.Timestamp(fold.train_start, tz="Asia/Kolkata"),
                pd.Timestamp(fold.train_end, tz="Asia/Kolkata") + pd.Timedelta(days=1),
            )

            def training(frame):
                return (
                    frame[
                        (frame.timestamp >= begin) & (frame.timestamp < cutoff)
                    ].copy()
                    if frame is not None
                    else None
                )

            train_universe = dataset.universe.copy()
            train_universe = train_universe[
                train_universe.active_from.isna()
                | (train_universe.active_from < cutoff.tz_localize(None))
            ].copy()
            train_universe.loc[
                train_universe.active_to >= cutoff.tz_localize(None), "active_to"
            ] = pd.NaT
            train = Dataset(
                training(dataset.daily),
                training(dataset.benchmark),
                train_universe,
                training(dataset.intraday),
                training(dataset.benchmark_intraday),
                dataset.synthetic,
            )
            params = selector(train, params)
        result = BacktestEngine(params).run(
            dataset,
            start=fold.validation_start,
            end=fold.validation_end,
            mode=mode,
            progress=(
                lambda f, m: progress(
                    (i + f) / len(folds), f"Fold {i + 1}/{len(folds)} · {m}"
                )
            )
            if progress
            else None,
        )
        results.append(
            {
                "train_start": fold.train_start,
                "train_end": fold.train_end,
                "validation_start": fold.validation_start,
                "validation_end": fold.validation_end,
                **metrics(result),
            }
        )
    return pd.DataFrame(results)


def robustness(
    dataset,
    settings,
    strategy,
    parameter,
    values,
    start=None,
    end=None,
    mode="daily",
    progress=None,
):
    if len(values) > 5:
        raise ValueError("Use at most five broad neighborhood values")
    rows = []
    for i, value in enumerate(values):
        params = deepcopy(settings)
        params["strategies"][strategy][parameter] = value
        r = BacktestEngine(params).run(
            dataset,
            start=start,
            end=end,
            mode=mode,
            progress=(
                lambda f, m: progress(
                    (i + f) / len(values), f"{parameter}={value} · {m}"
                )
            )
            if progress
            else None,
        )
        rows.append({"parameter": parameter, "value": value, **metrics(r)})
    return pd.DataFrame(rows)
