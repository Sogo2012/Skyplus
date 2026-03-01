# geometry_utils.py

"""
Utilities for generating 3D geometry models.
"""

import numpy as np

class Geometry:
    def __init__(self):
        pass

    def create_cube(self, size):
        """
        Create a cube with a given size.
        """
        return np.array([[0, 0, 0], [size, 0, 0], [size, size, 0], [0, size, 0],
                         [0, 0, size], [size, 0, size], [size, size, size], [0, size, size]])

    def create_sphere(self, radius, num_points):
        """
        Create a sphere with a given radius and number of points.
        """
        phi = np.linspace(0, np.pi, num_points)
        theta = np.linspace(0, 2 * np.pi, num_points)
        phi, theta = np.meshgrid(phi, theta)
        x = radius * np.sin(phi) * np.cos(theta)
        y = radius * np.sin(phi) * np.sin(theta)
        z = radius * np.cos(phi)
        return np.array([x.flatten(), y.flatten(), z.flatten()]).T
