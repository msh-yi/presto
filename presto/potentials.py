import numpy as np
import cctk
import presto

class Potential():
    """
    Constraining potential to keep things confined.

    Attributes:
        max_radius (float): maximum allowed distance for particles. for a sphere, this is just the radius. for a cube, it might be half the space diagonal.
            for an ellipsoid, it might be the major radius. used for internal automatic checks.
    """
    def force(self, positions):
        """
        Returns forces from a given set of atomic coordinates.
        """
        pass

class SphericalHarmonicPotential(Potential):
    """
    Default force constant is 10 kcal/mol per Å**2, akin to that employed by Singleton (JACS, 2016, 138, 15167).
    In *presto* units, this is 0.004184 amu Å**2 fs**-2.

    Attributes:
        radius (float): area outside which this takes effect
        force_constant (float):
        convert_from_kcal (bool):
    """
    def __init__(self, radius, force_constant=10, convert_from_kcal=True):
        assert isinstance(radius, (int, float)), "radius must be numeric"
        assert radius > 0, "radius must be positive"

        assert isinstance(force_constant, (int, float)), "force_constant must be numeric"
        assert force_constant > 0, "force_constant must be positive"
        if convert_from_kcal:
            force_constant *= 0.0004184

        self.radius = radius
        self.force_constant = force_constant

        self.max_radius = radius

    def force(self, positions):
        radii = np.linalg.norm(positions, axis=1)
        forces = -0.5 * self.force_constant * (positions - positions/radii.reshape(-1,1) * self.radius)
        forces = forces * np.linalg.norm(positions - positions/radii.reshape(-1,1) * self.radius, axis=1).reshape(-1,1)
        inside = radii < self.radius
        forces.view(np.ndarray)[inside,:] = 0
        return forces.view(cctk.OneIndexedArray)

def build_potential(settings):
    """
    Build potential from settings dict.
    """
    assert isinstance(settings, dict), "Need to pass a dictionary!!"
    assert "type" in settings, "Need `type` for potential"
    assert isinstance(settings["type"], str), "Potential `type` must be a string"

    if settings["type"].lower() == "spherical_harmonic":
        assert "radius" in settings, "Need `radius` for spherical harmonic potential."
        assert isinstance(settings["radius"], (int, float)), "`radius` must be numeric!"
        assert settings["radius"] > 0, "`radius` must be positive!"

        if "force_constant" in settings:
            assert isinstance(settings["force_constant"], (int, float)), "`force_constant` must be numeric!"
            assert settings["force_constant"] > 0, "`force_constant` must be positive!"
            return SphericalHarmonicPotential(radius=settings["radius"], force_constant=settings["force_constant"])
        else:
            return SphericalHarmonicPotential(radius=settings["radius"])

    else:
        raise ValueError(f"Unknown potential type {settings['type']}! Allowed options are `spherical_harmonic` (free will is an illusion).")



