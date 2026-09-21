"""Stdlib-only qualification for population-drift machinery; spec/POPULATION-METRICS.md.

No Torch, no NumPy, no network, no corpus store: every test runs on tiny
synthetic corpora with synthetic embeddings. The pinned OpenL3 producer is
asserted to REFUSE while its dependency is absent — absence never silently
skips, and an unavailable check never looks like a pass.
"""

from __future__ import annotations

import math
import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from torchsynth_voice.population_metrics import (  # noqa: E402
    ACCUMULATION,
    FAD_INFINITY_ESTIMATOR,
    FRECHET_ESTIMATOR,
    MMD_ESTIMATOR,
    PROVISIONAL_ENVELOPE_V0,
    PopulationMetricsError,
    REFERENCE_EMBEDDING_PIN,
    fad_infinity_estimate,
    frechet_from_embeddings,
    frechet_gaussian,
    inject_outliers,
    jacobi_eigenpairs,
    mean_and_covariance,
    mmd_obs_eq2,
    openl3_availability,
    openl3_reference_embeddings,
    provisional_envelope_embedding,
    run_population_conditions,
)


def gaussian_population(seed, count, dim=2, mu=0.0, sigma=1.0):
    rng = random.Random(seed)
    return [[rng.gauss(mu, sigma) for _ in range(dim)] for _ in range(count)]


class MmdObsEq2Tests(unittest.TestCase):
    def test_hand_computed_one_dimensional_value(self):
        # x = {0, 0}, y = {3, 3}: every ordered pair contributes 2*3 - 0 - 0.
        result = mmd_obs_eq2([[0], [0]], [[3], [3]])
        self.assertEqual(result["value"], 6.0)
        self.assertEqual(result["estimator"], MMD_ESTIMATOR)
        self.assertEqual(result["distance"], "l1")
        self.assertEqual(result["accumulation"], ACCUMULATION)
        self.assertEqual(result["n"], 2)

    def test_identity_permutation_control_is_exactly_zero(self):
        population = gaussian_population(7, 24)
        permuted = population[:]
        random.Random(3).shuffle(permuted)
        result = mmd_obs_eq2(population, permuted)
        self.assertEqual(result["value"], 0.0)

    def test_split_of_same_population_sits_between_control_and_shift(self):
        population = gaussian_population(11, 40)
        half = len(population) // 2
        within = mmd_obs_eq2(population[:half], population[half:])["value"]
        control = mmd_obs_eq2(population, population[:])["value"]
        shifted = [[x + 25.0 for x in row] for row in population]
        cross = mmd_obs_eq2(population, shifted)["value"]
        self.assertEqual(control, 0.0)
        self.assertLess(within, cross)
        self.assertGreaterEqual(within, 0.0)

    def test_refuses_unequal_partition_sizes(self):
        with self.assertRaises(PopulationMetricsError):
            mmd_obs_eq2([[0.0], [1.0]], [[2.0]])

    def test_refuses_dimension_mismatch_and_nonfinite(self):
        with self.assertRaises(PopulationMetricsError):
            mmd_obs_eq2([[0.0, 1.0]], [[2.0]])
        with self.assertRaises(PopulationMetricsError):
            mmd_obs_eq2([[0.0], [float("nan")]], [[1.0], [1.0]])

    def test_callable_distance_requires_identity(self):
        def nameless(a, b):
            return 0.0

        with self.assertRaises(PopulationMetricsError):
            mmd_obs_eq2([[0.0]], [[1.0]], distance=nameless)

        def named(a, b):
            return abs(a[0] - b[0])

        named.identity = "identity-l1"
        # n = 1: the single ordered pair contributes 2*5 - 0 - 0, over n^2 = 1.
        self.assertEqual(mmd_obs_eq2([[0.0]], [[5.0]], distance=named)["value"], 10.0)


class FrechetTests(unittest.TestCase):
    def test_identical_populations_are_zero(self):
        population = gaussian_population(5, 30)
        self.assertAlmostEqual(
            frechet_from_embeddings(population, population)["value"], 0.0, places=12
        )

    def test_one_dimensional_closed_form(self):
        a = [[0.0], [1.0], [-1.0], [0.5]]
        b = [[3.0], [2.0], [4.0], [2.5]]
        mu_a, cov_a = mean_and_covariance(a)
        mu_b, cov_b = mean_and_covariance(b)
        var_a = math.fsum((row[0] - mu_a[0]) ** 2 for row in a) / (len(a) - 1)
        var_b = math.fsum((row[0] - mu_b[0]) ** 2 for row in b) / (len(b) - 1)
        expected = (mu_a[0] - mu_b[0]) ** 2 + (math.sqrt(var_a) - math.sqrt(var_b)) ** 2
        got = frechet_gaussian(mu_a, cov_a, mu_b, cov_b)
        self.assertAlmostEqual(got, expected, places=12)
        self.assertAlmostEqual(got, 7.5625, places=12)

    def test_shared_rotation_leaves_eigenvalue_cross_term_invariant(self):
        theta = 0.7
        rotation = [
            [math.cos(theta), -math.sin(theta)],
            [math.sin(theta), math.cos(theta)],
        ]

        def rotate(diagonal):
            return [
                [
                    sum(
                        rotation[i][k] * diagonal[k][m] * rotation[j][m]
                        for k in range(2)
                        for m in range(2)
                    )
                    for j in range(2)
                ]
                for i in range(2)
            ]

        zero = [0.0, 0.0]
        got = frechet_gaussian(
            zero,
            rotate([[4.0, 0.0], [0.0, 1.0]]),
            zero,
            rotate([[9.0, 0.0], [0.0, 0.25]]),
        )
        expected = 14.25 - 2.0 * (math.sqrt(36.0) + math.sqrt(0.25))
        self.assertAlmostEqual(got, expected, places=9)

    def test_shifted_distribution_is_positive(self):
        population = gaussian_population(21, 32)
        shifted = [[x + 3.0 for x in row] for row in population]
        result = frechet_from_embeddings(population, shifted)
        self.assertGreater(result["value"], 0.0)
        self.assertEqual(result["estimator"], FRECHET_ESTIMATOR)
        self.assertEqual(result["dim"], 2)

    def test_jacobi_finds_known_eigenvalues(self):
        eigenvalues, _ = jacobi_eigenpairs([[2.0, 1.0], [1.0, 2.0]])
        self.assertAlmostEqual(eigenvalues[0], 1.0, places=10)
        self.assertAlmostEqual(eigenvalues[1], 3.0, places=10)

    def test_refuses_singleton_covariance(self):
        with self.assertRaises(PopulationMetricsError):
            frechet_from_embeddings([[0.0, 1.0]], [[2.0, 3.0]])


class FadInfinityTests(unittest.TestCase):
    def test_sample_size_bias_is_visible_and_extrapolation_lands(self):
        ref = gaussian_population(101, 48)
        test = gaussian_population(202, 64)
        outcome = fad_infinity_estimate(
            ref, test, sample_sizes=[8, 16, 32, 64], trials=6, seed=11
        )
        self.assertEqual(outcome["fadinfty_estimator"], FAD_INFINITY_ESTIMATOR)
        means = [row["mean_fad"] for row in outcome["curve"]]
        self.assertGreater(min(means), 0.0)
        # Bias direction: small-N estimates sit above the extrapolated limit.
        self.assertGreater(means[0], outcome["fadinfty"])
        self.assertGreaterEqual(outcome["fadinfty"], 0.0)
        for row in outcome["curve"]:
            self.assertLessEqual(row["min_fad"], row["mean_fad"])
            self.assertLessEqual(row["mean_fad"], row["max_fad"])
            self.assertEqual(len(row["values"]), 6)

    def test_identical_reference_and_test_extrapolate_near_zero(self):
        population = gaussian_population(31, 40)
        outcome = fad_infinity_estimate(
            population, population, sample_sizes=[8, 16, 32], trials=5, seed=4
        )
        self.assertLess(outcome["fadinfty"], 0.5)

    def test_requires_at_least_two_sizes(self):
        population = gaussian_population(9, 16)
        with self.assertRaises(PopulationMetricsError):
            fad_infinity_estimate(
                population, population, sample_sizes=[8], trials=2, seed=1
            )

    def test_condition_accepts_distinct_reference_population(self):
        corpus = gaussian_population(31, 40)
        shifted = [[x + 25.0 for x in row] for row in corpus]
        receipt = run_population_conditions(
            {"corpus": corpus, "shifted": shifted},
            conditions=[
                {
                    "kind": "fadinfty",
                    "reference": "corpus",
                    "population": "shifted",
                    "sample_sizes": [8, 16, 32],
                    "trials": 4,
                    "seed": 3,
                }
            ],
            seed=1,
        )
        entry = receipt["conditions"][0]
        self.assertEqual(entry["reference"], "corpus")
        self.assertEqual(entry["population"], "shifted")
        # A genuinely shifted test population extrapolates above zero, unlike
        # the degenerate self-comparison.
        self.assertGreater(entry["fadinfty"], 0.0)

    def test_condition_reference_defaults_to_self_comparison(self):
        population = gaussian_population(31, 40)
        entry = run_population_conditions(
            {"corpus": population},
            conditions=[
                {
                    "kind": "fadinfty",
                    "population": "corpus",
                    "sample_sizes": [8, 16],
                    "trials": 2,
                    "seed": 3,
                }
            ],
            seed=1,
        )["conditions"][0]
        self.assertEqual(entry["reference"], "corpus")
        self.assertEqual(entry["population"], "corpus")


class OutlierInjectionTests(unittest.TestCase):
    """Deterministic trailing-k injection and its monotone contamination response."""

    def _corpus_and_pool(self):
        corpus = gaussian_population(23, 24, dim=3)
        pool = [
            [x + 30.0 for x in row] for row in gaussian_population(77, 8, dim=3)
        ]
        return corpus, pool

    def test_trailing_replacement_is_exact_and_repeatable(self):
        corpus, pool = self._corpus_and_pool()
        injected = inject_outliers(corpus, pool, 4)
        self.assertEqual(len(injected), len(corpus))
        self.assertEqual(injected[:-4], corpus[:-4])
        self.assertEqual(injected[-4:], pool[:4])
        self.assertEqual(injected, inject_outliers(corpus, pool, 4))

    def test_refusals(self):
        corpus, pool = self._corpus_and_pool()
        with self.assertRaises(PopulationMetricsError):
            inject_outliers(corpus, pool, 0)
        with self.assertRaises(PopulationMetricsError):
            inject_outliers(corpus, pool, len(corpus))
        with self.assertRaises(PopulationMetricsError):
            inject_outliers(corpus, pool, len(pool) + 1)
        with self.assertRaises(PopulationMetricsError):
            inject_outliers(gaussian_population(5, 4, dim=2), pool, 1)
        with self.assertRaises(PopulationMetricsError):
            inject_outliers(corpus, pool, True)

    def test_condition_rows_are_monotone_and_seed_free(self):
        corpus, pool = self._corpus_and_pool()
        sizes = [1, 4, 8]
        conditions = [
            {
                "kind": "outlier_injection",
                "population": "corpus",
                "outlier_pool": "pool",
                "injection_sizes": sizes,
            }
        ]
        first = run_population_conditions(
            {"corpus": corpus, "pool": pool}, conditions=conditions, seed=1
        )
        second = run_population_conditions(
            {"corpus": corpus, "pool": pool}, conditions=conditions, seed=999
        )
        entry = first["conditions"][0]
        self.assertEqual(entry["kind"], "outlier_injection")
        self.assertEqual(entry["population"], "corpus")
        self.assertEqual(entry["outlier_pool"], "pool")
        self.assertEqual(entry["injection_sizes"], sizes)
        self.assertIn("seed-free", entry["injection_rule"])
        self.assertEqual([row["k"] for row in entry["injections"]], sizes)
        self.assertEqual(
            [row["contamination_rate"] for row in entry["injections"]],
            [1 / 24, 4 / 24, 8 / 24],
        )
        fads = [row["fad"]["value"] for row in entry["injections"]]
        mmds = [row["mmd"]["value"] for row in entry["injections"]]
        # Strongly separated pool: the contamination response must not decrease
        # as the severity grid climbs.
        self.assertEqual(fads, sorted(fads))
        self.assertEqual(mmds, sorted(mmds))
        self.assertTrue(all(value > 0.0 for value in mmds))
        # The injection rule is seed-free: identical rows under any harness seed.
        self.assertEqual(first["conditions"], second["conditions"])

    def test_condition_refuses_unknown_pool_and_bad_sizes(self):
        corpus, pool = self._corpus_and_pool()
        with self.assertRaises(PopulationMetricsError):
            run_population_conditions(
                {"corpus": corpus, "pool": pool},
                conditions=[
                    {
                        "kind": "outlier_injection",
                        "population": "corpus",
                        "outlier_pool": "missing",
                    }
                ],
                seed=1,
            )
        with self.assertRaises(PopulationMetricsError):
            run_population_conditions(
                {"corpus": corpus, "pool": pool},
                conditions=[
                    {
                        "kind": "outlier_injection",
                        "population": "corpus",
                        "outlier_pool": "pool",
                        "injection_sizes": [0],
                    }
                ],
                seed=1,
            )
        with self.assertRaises(PopulationMetricsError):
            run_population_conditions(
                {"corpus": corpus, "pool": pool},
                conditions=[
                    {
                        "kind": "outlier_injection",
                        "population": "corpus",
                        "outlier_pool": "pool",
                        "injection_sizes": "1,4",
                    }
                ],
                seed=1,
            )


class ReferencePinTests(unittest.TestCase):
    def test_declared_pin_identity(self):
        self.assertEqual(
            REFERENCE_EMBEDDING_PIN["identity"], "openl3-music-mel256-512-l1"
        )
        self.assertEqual(REFERENCE_EMBEDDING_PIN["family"], "openl3")
        self.assertEqual(REFERENCE_EMBEDDING_PIN["content_type"], "music")
        self.assertEqual(REFERENCE_EMBEDDING_PIN["input_representation"], "mel256")
        self.assertEqual(REFERENCE_EMBEDDING_PIN["embedding_size"], 512)
        self.assertEqual(REFERENCE_EMBEDDING_PIN["audio_distance"], "l1")

    def test_openl3_producer_refuses_while_dependency_absent(self):
        probe = openl3_availability()
        self.assertFalse(probe["available"])
        self.assertIn("openl3", probe["reason"])
        with self.assertRaises(PopulationMetricsError) as caught:
            openl3_reference_embeddings([[0.0, 0.0], [0.1, -0.1]])
        self.assertIn("openl3", str(caught.exception))


class ProvisionalEmbeddingTests(unittest.TestCase):
    def test_deterministic_dimensions_and_zero_signal(self):
        signal = [math.sin(i * 0.01) for i in range(1000)]
        first = provisional_envelope_embedding(signal, windows=8)
        self.assertEqual(first, provisional_envelope_embedding(signal, windows=8))
        self.assertEqual(len(first), 9)
        self.assertTrue(all(math.isfinite(v) for v in first))
        silent = provisional_envelope_embedding([0.0] * 400, windows=4)
        self.assertEqual(silent[:-1], [0.0] * 4)
        self.assertEqual(silent[-1], 0.0)

    def test_declared_not_the_obs_pin(self):
        self.assertNotEqual(
            PROVISIONAL_ENVELOPE_V0["identity"], REFERENCE_EMBEDDING_PIN["identity"]
        )
        self.assertIn("NOT the OBS reference pin", PROVISIONAL_ENVELOPE_V0["note"])

    def test_refuses_nonfinite_and_bad_windows(self):
        with self.assertRaises(PopulationMetricsError):
            provisional_envelope_embedding([0.0, float("inf")])
        with self.assertRaises(PopulationMetricsError):
            provisional_envelope_embedding([0.0, 1.0], windows=0)


class ConditionsHarnessTests(unittest.TestCase):
    """The full conditions harness on a tiny synthetic corpus, stdlib-only."""

    def _corpus(self):
        base = gaussian_population(46, 24, dim=3)
        return {
            "corpus": base,
            "corpus_pitch_x2": [[x * 1.5 for x in row] for row in base],
            "corpus_noise_gain": [
                [x + random.Random(9 + i).gauss(0, 0.5) for i, x in enumerate(row)]
                for row in base
            ],
            "outlier_pool": [[x + 20.0 for x in row] for row in base],
        }

    def test_all_condition_kinds_run_and_control_holds(self):
        populations = self._corpus()
        receipt = run_population_conditions(
            populations,
            conditions=[
                {"kind": "permutation_control", "population": "corpus"},
                {"kind": "split_half", "population": "corpus"},
                {
                    "kind": "cross_population",
                    "a": "corpus",
                    "b": "corpus_pitch_x2",
                    "label": "extreme-pitch demonstration",
                },
                {
                    "kind": "cross_population",
                    "a": "corpus",
                    "b": "corpus_noise_gain",
                    "label": "noise-gain demonstration",
                },
                {
                    "kind": "parameter_shift_biased_resample",
                    "population": "corpus",
                    "label": "wrong-nebula stand-in",
                },
                {
                    "kind": "outlier_injection",
                    "population": "corpus",
                    "outlier_pool": "outlier_pool",
                    "injection_sizes": [1, 3],
                    "label": "outlier demonstration",
                },
                {
                    "kind": "fadinfty",
                    "population": "corpus",
                    "sample_sizes": [6, 12, 24],
                    "trials": 4,
                    "seed": 5,
                },
                {
                    "kind": "fadinfty",
                    "reference": "corpus",
                    "population": "corpus_noise_gain",
                    "sample_sizes": [6, 12, 24],
                    "trials": 4,
                    "seed": 5,
                },
            ],
            seed=20260920,
        )
        kinds = [entry["kind"] for entry in receipt["conditions"]]
        self.assertEqual(kinds[0], "permutation_control")
        self.assertTrue(receipt["conditions"][0]["control_holds"])
        self.assertEqual(receipt["conditions"][0]["value"], 0.0)
        self.assertIn("outlier_injection", kinds)
        by_label = {
            entry.get("label"): entry
            for entry in receipt["conditions"]
            if "label" in entry
        }
        self.assertGreater(by_label["extreme-pitch demonstration"]["mmd"]["value"], 0.0)
        self.assertGreater(by_label["noise-gain demonstration"]["mmd"]["value"], 0.0)
        self.assertGreater(by_label["wrong-nebula stand-in"]["mmd"]["value"], 0.0)
        outlier_entry = by_label["outlier demonstration"]
        self.assertEqual(
            [row["k"] for row in outlier_entry["injections"]], [1, 3]
        )
        self.assertTrue(
            all(row["mmd"]["value"] > 0.0 for row in outlier_entry["injections"])
        )
        fad_entry = receipt["conditions"][-2]
        self.assertEqual(fad_entry["kind"], "fadinfty")
        self.assertGreaterEqual(fad_entry["fadinfty"], 0.0)
        cross_entry = receipt["conditions"][-1]
        self.assertEqual(cross_entry["reference"], "corpus")
        self.assertIn("never an optimization target", receipt["provenance"]["doctrine"])

    def test_unknown_population_and_kind_refuse(self):
        with self.assertRaises(PopulationMetricsError):
            run_population_conditions(
                {"corpus": gaussian_population(1, 8)},
                conditions=[{"kind": "split_half", "population": "missing"}],
                seed=1,
            )
        with self.assertRaises(PopulationMetricsError):
            run_population_conditions(
                {"corpus": gaussian_population(1, 8)},
                conditions=[{"kind": "teleport", "population": "corpus"}],
                seed=1,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
