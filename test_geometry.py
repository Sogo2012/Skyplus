# Test for Geometry Utilities

# This file contains test cases for the geometry utility functions.

import unittest

class TestGeometryUtilities(unittest.TestCase):

    def test_area_of_circle(self):
        self.assertAlmostEqual(area_of_circle(1), 3.14159)

    def test_perimeter_of_rectangle(self):
        self.assertEqual(perimeter_of_rectangle(2, 3), 10)

if __name__ == '__main__':
    unittest.main()