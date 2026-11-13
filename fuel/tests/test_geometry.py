from django.test import SimpleTestCase

from fuel.services.geometry import (
    cumulative_miles,
    decode_polyline,
    haversine_mi,
    resample,
    simplify,
)


class HaversineTests(SimpleTestCase):
    def test_known_distance(self):
        # New York to Philadelphia, about 81 miles as the crow flies.
        miles = haversine_mi(40.7484, -73.9857, 39.9526, -75.1652)
        self.assertAlmostEqual(miles, 82.9, delta=1.0)

    def test_zero_for_same_point(self):
        self.assertEqual(haversine_mi(35.0, -90.0, 35.0, -90.0), 0.0)


class PolylineTests(SimpleTestCase):
    def test_decodes_the_reference_example(self):
        # The sample string from Google's encoded-polyline specification.
        points = decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@", precision=5)
        self.assertEqual(
            [(round(a, 4), round(b, 4)) for a, b in points],
            [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)],
        )

    def test_rejects_a_truncated_string(self):
        with self.assertRaises(ValueError):
            decode_polyline("_p~iF~ps|U_ulLnnq")


class CumulativeTests(SimpleTestCase):
    def test_runs_from_zero_and_adds_up(self):
        points = [(35.0, -90.0), (35.0, -89.0), (35.0, -88.0)]
        miles = cumulative_miles(points)
        self.assertEqual(miles[0], 0.0)
        self.assertEqual(len(miles), 3)
        self.assertAlmostEqual(miles[2], 2 * miles[1], places=4)

    def test_single_point(self):
        self.assertEqual(cumulative_miles([(35.0, -90.0)]), [0.0])


class ResampleTests(SimpleTestCase):
    def test_thins_to_roughly_the_requested_spacing(self):
        points = [(35.0, -90.0 + i * 0.01) for i in range(400)]
        cumulative = cumulative_miles(points)
        kept = resample(points, cumulative, spacing_mi=5.0)
        self.assertLess(len(kept), len(points) / 5)
        self.assertEqual(kept[0], 0)
        self.assertEqual(kept[-1], len(points) - 1)


class SimplifyTests(SimpleTestCase):
    def test_collapses_a_straight_line(self):
        points = [(i * 0.001, 0.0) for i in range(100)]
        self.assertEqual(len(simplify(points, 0.0001)), 2)

    def test_keeps_a_real_corner(self):
        points = [(0.0, 0.0), (0.0, 0.5), (0.5, 0.5)]
        self.assertEqual(simplify(points, 0.001), points)

    def test_always_keeps_both_ends(self):
        points = [(0.0, 0.0), (0.0001, 0.3), (0.0, 0.6)]
        out = simplify(points, 0.01)
        self.assertEqual(out[0], points[0])
        self.assertEqual(out[-1], points[-1])
