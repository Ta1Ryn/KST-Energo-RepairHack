import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from response_contract import make_response


class ContractTests(unittest.TestCase):
    def test_box_uses_top_left_not_yolo_center(self):
        result = make_response([(2, .8, .5, .6, .4, .2)], "test")
        self.assertEqual(result["visualStatus"], "possibleDefect")
        box = result["detections"][0]["box"]
        self.assertAlmostEqual(box["x"], .3)
        self.assertAlmostEqual(box["y"], .5)
        self.assertAlmostEqual(box["width"], .4)
        self.assertEqual(result["detections"][0]["label"], "corrosion")

    def test_no_boxes_does_not_confirm_equipment_health(self):
        result = make_response([], "test")
        self.assertEqual(result["visualStatus"], "checkRecommended")
        self.assertIsNone(result["confidence"])
        self.assertEqual(result["detections"], [])

    def test_invalid_class_and_non_finite_box_are_rejected(self):
        for row in [(4, .8, .5, .5, .2, .2), (.5, .8, .5, .5, .2, .2), (0, .8, float("nan"), .5, .2, .2)]:
            with self.assertRaises(ValueError):
                make_response([row], "test")

    def test_boundary_is_clipped_to_original_photo(self):
        result = make_response([(0, .9, .99, .99, .1, .1)], "test")
        box = result["detections"][0]["box"]
        self.assertLessEqual(box["x"] + box["width"], 1)
        self.assertLessEqual(box["y"] + box["height"], 1)


if __name__ == "__main__":
    unittest.main()
