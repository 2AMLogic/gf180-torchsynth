"""Population-drift diagnostics: FAD, FAD-infinity, and distance-induced MMD.

See spec/POPULATION-METRICS.md. Population metrics answer population questions
only: does one set of sounds retain the embedding-space coverage of another?
They never establish per-sound identity or implementation correctness, are
never an acceptance oracle, and are never an optimization target (the One
Billion Sounds paper's optimized nebulae rewarded extreme pitches and drum
imposters that blind listeners rejected; docs/MEASUREMENT-PLAN.md).

The reference embedding is a DECLARED PIN: OpenL3 music / mel256 / 512 with L1
audio distance (arXiv:2104.12922). OpenL3 and Torch are deliberately not repo
dependencies; producing those embeddings refuses with an explicit reason until
the dependency is present. The statistics machinery itself is stdlib-only and
is qualified on tiny synthetic corpora with synthetic embeddings.

Numeric policy: inputs convert individually to binary64; accumulation uses
math.fsum (order-independent over a multiset). No NaN or Infinity is ever
returned; malformed input raises PopulationMetricsError before any result.
"""

from __future__ import annotations

import importlib
import math
import random
import sys
from collections.abc import Callable, Mapping, Sequence

MMD_ESTIMATOR = "obs2104.12922-eq2-distance-induced-v1"
FRECHET_ESTIMATOR = "frechet-gaussian-eq1-v1"
FAD_INFINITY_ESTIMATOR = "fadinfty-linear-extrapolation-gui23-v1"
ACCUMULATION = "fsum-binary64-v1"

REFERENCE_EMBEDDING_PIN = {
    "identity": "openl3-music-mel256-512-l1",
    "family": "openl3",
    "content_type": "music",
    "input_representation": "mel256",
    "embedding_size": 512,
    "audio_distance": "l1",
    "frame_aggregation": "mean-over-frames-v1",
    "hop_seconds": 0.5,
    "sources": [
        "arXiv:2104.12922 S4.3 Table 2 and S5: OpenL3 (music, mel256, 512) with "
        "L1 distance; corpus similarity via its Eq. 2 MMD",
        "arXiv:2311.01616: FAD sample-size bias and FAD-infinity extrapolation",
    ],
    "dependency_status": (
        "declared pin only; openl3/torch are not repo dependencies, so producing "
        "these embeddings refuses with an explicit reason until installed"
    ),
    "frame_aggregation_note": (
        "the OBS paper does not specify frame handling for its corpus MMD; "
        "mean-over-frames is this repo's declared default, following fadtk"
    ),
}

PROVISIONAL_ENVELOPE_V0 = {
    "identity": "provisional-envelope-v0",
    "definition": (
        "fixed count of equal windows: per-window RMS, plus whole-clip "
        "zero-crossing rate; one dimension per window plus one"
    ),
    "note": (
        "stdlib demonstration embedding; NOT the OBS reference pin; carries no "
        "perceptual meaning and no acceptance semantics"
    ),
}


class PopulationMetricsError(ValueError):
    """Malformed input or unavailable declared dependency, not a measured value."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise PopulationMetricsError(reason)


def _embedding_matrix(population, name) -> list[list[float]]:
    _require(
        isinstance(population, Sequence) and not isinstance(population, (str, bytes)),
        f"{name} must be a sequence of embeddings",
    )
    count = len(population)
    _require(count > 0, f"{name} must be non-empty")
    rows: list[list[float]] = []
    dim = None
    for i, row in enumerate(population):
        _require(
            isinstance(row, Sequence) and not isinstance(row, (str, bytes)),
            f"{name}[{i}] must be a one-dimensional embedding",
        )
        _require(len(row) > 0, f"{name}[{i}] must be non-empty")
        if dim is None:
            dim = len(row)
        _require(
            len(row) == dim,
            f"{name} embedding dimension mismatch at index {i}",
        )
        converted = []
        for j, value in enumerate(row):
            _require(
                type(value) is not bool and isinstance(value, (int, float)),
                f"{name}[{i}][{j}] must be a finite binary64 number",
            )
            v = float(value)
            _require(
                math.isfinite(v) and -sys.float_info.max <= v <= sys.float_info.max,
                f"{name}[{i}][{j}] must be a finite binary64 number",
            )
            converted.append(v)
        rows.append(converted)
    return rows


DISTANCES: dict[str, Callable[[Sequence[float], Sequence[float]], float]] = {}


def _register_distance(name):
    def register(fn):
        DISTANCES[name] = fn
        return fn

    return register


@_register_distance("l1")
def l1_distance(a, b) -> float:
    return math.fsum(abs(x - y) for x, y in zip(a, b))


@_register_distance("l2")
def l2_distance(a, b) -> float:
    return math.sqrt(math.fsum((x - y) ** 2 for x, y in zip(a, b)))


def _distance_fn(distance):
    if isinstance(distance, str):
        _require(
            distance in DISTANCES,
            f"unknown distance {distance!r}; registered: {sorted(DISTANCES)}",
        )
        return distance, DISTANCES[distance]
    _require(callable(distance), "distance must be a registered name or callable")
    identity = getattr(distance, "identity", None)
    _require(
        isinstance(identity, str) and identity.strip(),
        "callable distance must carry an `identity` string attribute",
    )
    return identity, distance


def mmd_obs_eq2(x, y, distance="l1") -> dict:
    """One Billion Sounds Eq. 2, with d the declared audio-space distance.

    ``MMD(X, Y) = (1/n^2) * sum_{i,j} [ 2 d(xi, yj) - d(xi, xj) - d(yi, yj) ]``
    over all ordered pairs, equal partition sizes ``n`` (the paper's own
    assumption). When ``y`` is a permutation of ``x`` every inner multiset is
    identical, so the value is exactly 0.0 under fsum accumulation: the
    identity-permutation negative control.
    """

    px = _embedding_matrix(x, "x")
    py = _embedding_matrix(y, "y")
    _require(
        len(px) == len(py),
        "the OBS Eq. 2 formulation assumes both partitions have n elements",
    )
    _require(
        len(px[0]) == len(py[0]),
        "x and y embedding dimensions must match",
    )
    identity, fn = _distance_fn(distance)
    n = len(px)
    total = 0.0
    cross = []
    within_x = []
    within_y = []
    for i in range(n):
        xi = px[i]
        yi = py[i]
        for j in range(n):
            cross.append(fn(xi, py[j]))
            within_x.append(fn(xi, px[j]))
            within_y.append(fn(yi, py[j]))
    total = 2.0 * math.fsum(cross) - math.fsum(within_x) - math.fsum(within_y)
    return {
        "value": total / (n * n),
        "estimator": MMD_ESTIMATOR,
        "distance": identity,
        "accumulation": ACCUMULATION,
        "n": n,
    }


def mean_and_covariance(population) -> tuple[list[float], list[list[float]]]:
    """Binary64 mean and unbiased (n-1) covariance of an embedding population."""

    rows = _embedding_matrix(population, "population")
    _require(
        len(rows) >= 2,
        "covariance requires at least two embeddings (unbiased n-1 estimate)",
    )
    dim = len(rows[0])
    n = len(rows)
    mean = [math.fsum(row[j] for row in rows) / n for j in range(dim)]
    centered = [[row[j] - mean[j] for j in range(dim)] for row in rows]
    denom = n - 1
    cov = [
        [
            math.fsum(centered[i][a] * centered[i][b] for i in range(n)) / denom
            for b in range(dim)
        ]
        for a in range(dim)
    ]
    return mean, cov


def _symmetric(matrix, name) -> list[list[float]]:
    rows = _embedding_matrix(matrix, name)
    dim = len(rows)
    _require(len(rows[0]) == dim, f"{name} must be square")
    for a in range(dim):
        for b in range(a + 1, dim):
            _require(
                abs(rows[a][b] - rows[b][a]) <= 1e-12 * max(1.0, abs(rows[a][b])),
                f"{name} must be symmetric",
            )
            rows[a][b] = rows[b][a] = (rows[a][b] + rows[b][a]) / 2.0
    return rows


def jacobi_eigenpairs(matrix) -> tuple[list[float], list[list[float]]]:
    """Cyclic Jacobi eigendecomposition of a symmetric matrix, stdlib-only.

    Exact for the small qualification dimensions this stdlib core is intended
    for; the pinned 512-dimensional OBS embedding is documented as outside this
    pure-Python performance envelope (spec/POPULATION-METRICS.md).
    """

    a = _symmetric(matrix, "matrix")
    dim = len(a)
    vectors = [[1.0 if i == j else 0.0 for j in range(dim)] for i in range(dim)]
    for _sweep in range(100):
        off = math.sqrt(
            math.fsum(a[i][j] ** 2 for i in range(dim) for j in range(dim) if i != j)
        )
        if off <= 1e-14 * max(
            1.0, math.sqrt(math.fsum(a[i][i] ** 2 for i in range(dim)))
        ):
            break
        for p in range(dim - 1):
            for q in range(p + 1, dim):
                if abs(a[p][q]) <= 1e-18 * max(1.0, abs(a[p][p]) + abs(a[q][q])):
                    continue
                theta = (a[q][q] - a[p][p]) / (2.0 * a[p][q])
                t = (
                    1.0 / (theta + math.sqrt(theta * theta + 1.0))
                    if theta >= 0
                    else -1.0 / (-theta + math.sqrt(theta * theta + 1.0))
                )
                c = 1.0 / math.sqrt(t * t + 1.0)
                s = t * c
                for k in range(dim):
                    akp, akq = a[k][p], a[k][q]
                    a[k][p] = c * akp - s * akq
                    a[k][q] = s * akp + c * akq
                for k in range(dim):
                    apk, aqk = a[p][k], a[q][k]
                    a[p][k] = c * apk - s * aqk
                    a[q][k] = s * apk + c * aqk
                for k in range(dim):
                    vkp, vkq = vectors[k][p], vectors[k][q]
                    vectors[k][p] = c * vkp - s * vkq
                    vectors[k][q] = s * vkp + c * vkq
    eigenvalues = [a[i][i] for i in range(dim)]
    order = sorted(range(dim), key=lambda i: eigenvalues[i])
    return (
        [eigenvalues[i] for i in order],
        [[row[i] for i in order] for row in vectors],
    )


def _psd_sqrt(matrix) -> list[list[float]]:
    rows = _symmetric(matrix, "matrix")
    eigenvalues, vectors = jacobi_eigenpairs(rows)
    dim = len(rows)
    sqrt_vals = [math.sqrt(v) if v > 0.0 else 0.0 for v in eigenvalues]
    # B = U diag(sqrt(λ)) U^T accumulated as (U · sqrt(Λ)) · U^T.
    uh = [[vectors[i][k] * sqrt_vals[k] for k in range(dim)] for i in range(dim)]
    out = [
        [math.fsum(uh[i][k] * vectors[j][k] for k in range(dim)) for j in range(dim)]
        for i in range(dim)
    ]
    return out


def frechet_gaussian(mu_r, cov_r, mu_g, cov_g) -> float:
    """FAD (Eq. 1, arXiv:2311.01616) on supplied Gaussian parameters."""

    mean_r = _embedding_matrix([mu_r], "mu_r")[0]
    mean_g = _embedding_matrix([mu_g], "mu_g")[0]
    _require(len(mean_r) == len(mean_g), "mean dimension mismatch")
    sr = _symmetric(cov_r, "cov_r")
    sg = _symmetric(cov_g, "cov_g")
    _require(len(sr) == len(sg), "covariance dimension mismatch")
    mean_sq = math.fsum((x - y) ** 2 for x, y in zip(mean_r, mean_g))
    root_g = _psd_sqrt(sg)
    dim = len(sr)
    temp = [
        [math.fsum(root_g[i][k] * sr[k][j] for k in range(dim)) for j in range(dim)]
        for i in range(dim)
    ]
    product = [
        [math.fsum(temp[i][k] * root_g[j][k] for k in range(dim)) for j in range(dim)]
        for i in range(dim)
    ]
    eigenvalues, _ = jacobi_eigenpairs(product)
    trace_sqrt = math.fsum(math.sqrt(v) if v > 0.0 else 0.0 for v in eigenvalues)
    return (
        mean_sq
        + math.fsum(sr[i][i] for i in range(dim))
        + math.fsum(sg[i][i] for i in range(dim))
        - 2.0 * trace_sqrt
    )


def frechet_from_embeddings(reference, test) -> dict:
    """FAD between two embedding populations (Gaussian fit, Eq. 1)."""

    mu_r, cov_r = mean_and_covariance(reference)
    mu_g, cov_g = mean_and_covariance(test)
    value = frechet_gaussian(mu_r, cov_r, mu_g, cov_g)
    return {
        "value": value,
        "estimator": FRECHET_ESTIMATOR,
        "accumulation": ACCUMULATION,
        "dim": len(mu_r),
        "n_reference": len(reference),
        "n_test": len(test),
    }


def fad_infinity_estimate(
    reference,
    test,
    *,
    sample_sizes,
    trials,
    seed,
) -> dict:
    """Extrapolate FAD to infinite test-sample size (arXiv:2311.01616 S3.3).

    For each declared sample size N, draw ``trials`` bootstrap resamples (with
    replacement) of size N from ``test``, compute FAD against the fixed
    reference, then fit per-size mean FAD linearly against 1/N; the intercept
    at 1/N = 0 is the FAD-infinity estimate. The curve rows are the
    sample-size-bias evidence the measurement plan requires.
    """

    ref = _embedding_matrix(reference, "reference")
    test_rows = _embedding_matrix(test, "test")
    _require(len(test_rows) >= 2, "test population needs at least two embeddings")
    sizes = list(sample_sizes)
    _require(len(sizes) >= 2, "at least two sample sizes are required to fit a line")
    for size in sizes:
        _require(
            type(size) is int and size >= 2,
            "sample sizes must be integers >= 2",
        )
    _require(
        type(trials) is int and trials >= 1,
        "trials must be a positive integer",
    )
    rng = random.Random(seed)
    curve = []
    for size in sizes:
        values = []
        for _ in range(trials):
            resample = [test_rows[rng.randrange(len(test_rows))] for _ in range(size)]
            values.append(frechet_from_embeddings(ref, resample)["value"])
        curve.append(
            {
                "n": size,
                "mean_fad": math.fsum(values) / len(values),
                "min_fad": min(values),
                "max_fad": max(values),
                "values": values,
            }
        )
    xs = [1.0 / row["n"] for row in curve]
    ys = [row["mean_fad"] for row in curve]
    x_mean = math.fsum(xs) / len(xs)
    y_mean = math.fsum(ys) / len(ys)
    sxx = math.fsum((x - x_mean) ** 2 for x in xs)
    slope = math.fsum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) / sxx
    intercept = y_mean - slope * x_mean
    return {
        "fadinfty": intercept,
        "fit_slope": slope,
        "estimator": FRECHET_ESTIMATOR,
        "fadinfty_estimator": FAD_INFINITY_ESTIMATOR,
        "accumulation": ACCUMULATION,
        "trials": trials,
        "seed": seed,
        "sample_sizes": sizes,
        "curve": curve,
    }


def provisional_envelope_embedding(samples, *, windows=8) -> list[float]:
    """Deterministic stdlib demonstration embedding (NOT the OBS pin).

    Per-window RMS over ``windows`` equal spans plus the whole-clip
    zero-crossing rate. Carries no perceptual meaning; it exists so the
    machinery, the conditions harness, and the receipts can run without the
    pinned dependency.
    """

    _require(
        type(windows) is int and windows >= 1, "windows must be a positive integer"
    )
    values = []
    for value in samples:
        _require(
            type(value) is not bool and isinstance(value, (int, float)),
            "samples must be finite real numbers",
        )
        v = float(value)
        _require(math.isfinite(v), "samples must be finite real numbers")
        values.append(v)
    _require(len(values) > 0, "samples must be non-empty")
    scale = max((abs(v) for v in values), default=0.0)
    if scale == 0.0:
        scale = 1.0
    span = len(values) // windows
    _require(span >= 1, "at least one sample per window")
    rms = []
    for w in range(windows):
        chunk = (
            values[w * span : (w + 1) * span]
            if w < windows - 1
            else values[(windows - 1) * span :]
        )
        scaled = [v / scale for v in chunk]
        mean_square = math.fsum(v * v for v in scaled) / len(scaled)
        rms.append(math.sqrt(mean_square) * scale)
    crossings = 0
    for a, b in zip(values, values[1:]):
        if (a < 0.0 <= b) or (b < 0.0 <= a):
            crossings += 1
    zcr = crossings / (len(values) - 1)
    return rms + [zcr]


def openl3_availability() -> dict:
    """Probe the declared pin's runtime dependency, honestly."""

    available = {}
    for module in ("numpy", "torch", "openl3"):
        try:
            importlib.import_module(module)
            available[module] = True
        except Exception:
            available[module] = False
    ready = available["openl3"] and available["torch"]
    missing = [m for m in ("torch", "openl3") if not available[m]]
    return {
        "pin": dict(REFERENCE_EMBEDDING_PIN),
        "modules": available,
        "available": ready,
        "reason": (
            "declared pin ready"
            if ready
            else "openl3_dependency_unavailable: missing modules " + ", ".join(missing)
        ),
    }


def openl3_reference_embeddings(
    clips, *, sample_rate=44100, hop_seconds=0.5
) -> list[list[float]]:
    """Produce the pinned OBS embeddings; refuses until openl3+torch exist.

    Each clip is one one-dimensional sequence of finite samples. OpenL3 emits
    frame-level embeddings; they are averaged over time (declared
    ``mean-over-frames-v1``). This is the pinned configuration's producer; it
    is never silently skipped when the dependency is absent.
    """

    probe = openl3_availability()
    _require(probe["available"], probe["reason"])
    import numpy as np

    openl3 = importlib.import_module("openl3")
    rows = []
    for i, clip in enumerate(clips):
        arr = np.asarray(_embedding_matrix([clip], f"clips[{i}]")[0], dtype=np.float64)
        embeddings, _timestamps = openl3.get_output(
            arr,
            sample_rate,
            content_type=REFERENCE_EMBEDDING_PIN["content_type"],
            input_repr=REFERENCE_EMBEDDING_PIN["input_representation"],
            embedding_size=REFERENCE_EMBEDDING_PIN["embedding_size"],
            hop_size=hop_seconds,
        )
        frame = np.asarray(embeddings)
        if frame.ndim == 1:
            frame = frame[np.newaxis, :]
        rows.append([float(v) for v in frame.mean(axis=0)])
    return rows


def run_population_conditions(populations, *, conditions, seed) -> dict:
    """Run declared drift conditions over named populations; receipts only.

    ``populations`` maps names to embedding populations. Each condition is a
    dict: ``kind`` in {``permutation_control``, ``split_half``,
    ``cross_population``, ``fadinfty``, ``parameter_shift_biased_resample``}
    plus its arguments (``population``/``a``/``b``/``label``/fadinfty options).
    Every result is demonstration evidence; no condition yields an acceptance
    verdict, a threshold, or an optimization target.
    """

    import random

    _require(isinstance(populations, Mapping), "populations must be a mapping")
    matrices = {
        name: _embedding_matrix(rows, name) for name, rows in populations.items()
    }

    def named_rows(name):
        _require(name in matrices, f"unknown population {name!r}")
        return matrices[name]

    rng = random.Random(seed)
    results = []
    for condition in conditions:
        _require(isinstance(condition, Mapping), "each condition must be a mapping")
        kind = condition.get("kind")
        entry = {"kind": kind}
        if kind == "permutation_control":
            name = condition["population"]
            rows = named_rows(name)
            perm = list(range(len(rows)))
            shuffled = perm[:]
            rng.shuffle(shuffled)
            permuted = [rows[i] for i in shuffled]
            outcome = mmd_obs_eq2(
                rows, permuted, distance=condition.get("distance", "l1")
            )
            entry.update(
                population=name,
                **outcome,
                control_holds=abs(outcome["value"]) <= 1e-12,
                note="identical partitions; population metrics must not move",
            )
        elif kind == "split_half":
            name = condition["population"]
            rows = named_rows(name)
            half = len(rows) // 2
            _require(half >= 2, f"{name} split half needs >= 2 embeddings")
            outcome = mmd_obs_eq2(
                rows[:half],
                rows[half : 2 * half],
                distance=condition.get("distance", "l1"),
            )
            entry.update(
                population=name, **outcome, note="within-corpus 50/50 baseline"
            )
        elif kind == "cross_population":
            name_a, name_b = condition["a"], condition["b"]
            outcome = mmd_obs_eq2(
                named_rows(name_a),
                named_rows(name_b),
                distance=condition.get("distance", "l1"),
            )
            outcome_fad = frechet_from_embeddings(
                named_rows(name_a), named_rows(name_b)
            )
            entry.update(
                a=name_a,
                b=name_b,
                label=condition.get("label", f"{name_a}-vs-{name_b}"),
                mmd=outcome,
                fad=outcome_fad,
                note="population-drift demonstration; no acceptance meaning",
            )
        elif kind == "fadinfty":
            name = condition["population"]
            outcome = fad_infinity_estimate(
                named_rows(name),
                named_rows(name),
                sample_sizes=condition.get("sample_sizes", [8, 16, 32, 48]),
                trials=condition.get("trials", 10),
                seed=condition.get("seed", seed),
            )
            entry.update(population=name, **outcome)
        elif kind == "parameter_shift_biased_resample":
            name = condition["population"]
            rows = named_rows(name)
            half = len(rows) // 2
            shifted = [
                rows[rng.randrange(half)]
                if i < half
                else rows[rng.randrange(len(rows))]
                for i in range(len(rows))
            ]
            outcome_mmd = mmd_obs_eq2(
                rows, shifted, distance=condition.get("distance", "l1")
            )
            outcome_fad = frechet_from_embeddings(rows, shifted)
            entry.update(
                population=name,
                label=condition.get("label", "parameter-distribution-shift"),
                mmd=outcome_mmd,
                fad=outcome_fad,
                note=(
                    "stratum-biased resample as a wrong-nebula/parameter-distribution "
                    "stand-in; the #44-gated ladder remains the ratified comparator"
                ),
            )
        else:
            raise PopulationMetricsError(f"unknown condition kind {kind!r}")
        results.append(entry)
    return {
        "conditions": results,
        "provenance": {
            "estimators": {
                "mmd": MMD_ESTIMATOR,
                "frechet": FRECHET_ESTIMATOR,
                "fadinfty": FAD_INFINITY_ESTIMATOR,
            },
            "accumulation": ACCUMULATION,
            "seed": seed,
            "doctrine": (
                "population diagnostics only: never per-sound identity or "
                "correctness, never an acceptance oracle, never an optimization "
                "target"
            ),
        },
    }
