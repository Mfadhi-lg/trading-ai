"""Chronological evaluation with purged label horizons and hidden test labels."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from itertools import groupby
from typing import Callable, Mapping, Sequence


def aware(value: datetime) -> None:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError('timestamps must be timezone-aware datetimes')


@dataclass(frozen=True)
class Observation:
    key: str
    decision_at: datetime
    features_available_at: datetime
    label_available_at: datetime
    features: tuple[Decimal, ...]
    label: Decimal


@dataclass(frozen=True)
class Window:
    train_start: datetime
    test_start: datetime
    test_end: datetime


@dataclass(frozen=True)
class PredictionInput:
    key: str
    decision_at: datetime
    features: tuple[Decimal, ...]


@dataclass(frozen=True)
class WindowResult:
    window: Window
    train_keys: tuple[str, ...]
    test_keys: tuple[str, ...]
    predictions: tuple[Decimal, ...]
    metrics: Mapping[str, Decimal]


def walk_forward(
    observations: Sequence[Observation], windows: Sequence[Window], *,
    fit: Callable, predict: Callable, score: Callable,
    evaluation_at: datetime,
) -> tuple[WindowResult, ...]:
    """Fit a fresh model per window; score only labels known by evaluation_at.

    Windows are half-open and nonoverlapping. Labels reaching the training
    cutoff are purged. Callbacks must fit preprocessing on training rows only.
    Feature lineage/timestamps and callback behavior are caller attestations.
    """
    aware(evaluation_at)
    if len(windows) < 2:
        raise ValueError('at least two out-of-sample windows required')
    previous = None
    for w in windows:
        for t in (w.train_start, w.test_start, w.test_end):
            aware(t)
        if not w.train_start < w.test_start < w.test_end <= evaluation_at:
            raise ValueError('invalid chronological window')
        if previous is not None and w.test_start < previous:
            raise ValueError('out-of-sample windows overlap or are unordered')
        previous = w.test_end
    keys = set()
    width = None
    for row in observations:
        for t in (row.decision_at, row.features_available_at, row.label_available_at):
            aware(t)
        if not row.key or row.key in keys:
            raise ValueError('observation keys must be unique and nonempty')
        keys.add(row.key)
        if row.features_available_at > row.decision_at or row.label_available_at <= row.decision_at:
            raise ValueError('future features or invalid label horizon')
        if not isinstance(row.features, tuple) or not row.features:
            raise ValueError('features must be a nonempty immutable tuple')
        if width is not None and len(row.features) != width:
            raise ValueError('inconsistent feature width')
        width = len(row.features)
        if any(not isinstance(v, Decimal) or not v.is_finite() for v in (*row.features, row.label)):
            raise ValueError('features and labels must be finite Decimal values')
    ordered = sorted(observations, key=lambda r: (r.decision_at, r.key))
    results = []
    for w in windows:
        train = tuple(r for r in ordered if w.train_start <= r.decision_at < w.test_start
                      and r.label_available_at < w.test_start)
        test = tuple(r for r in ordered if w.test_start <= r.decision_at < w.test_end)
        if not train or not test:
            raise ValueError('each window requires training and test rows')
        if any(r.label_available_at > evaluation_at for r in test):
            raise ValueError('test outcomes are not yet available')
        model = fit(train)
        # Only contemporaneous rows may be ranked together. A window-wide batch
        # would expose later features to earlier decisions.
        outputs = []
        for _, group in groupby(test, key=lambda r: r.decision_at):
            inputs = tuple(PredictionInput(r.key, r.decision_at, r.features) for r in group)
            batch = tuple(predict(model, inputs))
            if len(batch) != len(inputs):
                raise ValueError('predict must return one value per contemporaneous row')
            outputs.extend(batch)
        predictions = tuple(outputs)
        if len(predictions) != len(test) or any(
            not isinstance(p, Decimal) or not p.is_finite() for p in predictions
        ):
            raise ValueError('predict must return one finite Decimal per test row')
        metrics = dict(score(tuple(r.label for r in test), predictions))
        if not metrics or any(not isinstance(v, Decimal) or not v.is_finite() for v in metrics.values()):
            raise ValueError('score must return finite Decimal metrics')
        results.append(WindowResult(w, tuple(r.key for r in train),
                                    tuple(r.key for r in test), predictions, metrics))
    return tuple(results)
