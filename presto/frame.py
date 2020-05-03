import numpy as np
import math, copy, cctk

import presto

class Frame():
    """
    Represents one frame in a trajectory.

    Attributes:
        trajectory (presto.Trajectory):
        positions (cctk.OneIndexedArray):
        velocities (cctk.OneIndexedArray):
        accelerations (cctk.OneIndexedArray):

        energy (float):
        bath_temperature (float):
    """

    def __init__(self, trajectory, x, v, a, bath_temperature=298, energy=0.0):
        assert isinstance(trajectory, presto.trajectory.Trajectory), "need trajectory"

        assert len(x) == len(v), "length of positions not same as length of velocities!"
        assert len(x) == len(a), "length of positions not same as length of accelerations!"

        assert isinstance(x, cctk.OneIndexedArray), "positions is not a one-indexed array!"
        assert isinstance(v, cctk.OneIndexedArray), "velocities is not a one-indexed array!"
        assert isinstance(a, cctk.OneIndexedArray), "accelerations is not a one-indexed array!"

        assert (x.ndim == 2) and (x.shape[1] ==  3), "positions must be an n x 3 ndarray"
        assert (v.ndim == 2) and (v.shape[1] ==  3), "velocities must be an n x 3 ndarray"
        assert (a.ndim == 2) and (a.shape[1] ==  3), "accelerations must be an n x 3 ndarray"

        assert (isinstance(bath_temperature, float)) or (isinstance(bath_temperature, int)), "bath temperature needs to be numeric!"
        assert bath_temperature >= 0, "bath temperature must be positive or 0"

        self.trajectory = trajectory
        self.positions = x
        self.velocities = v
        self.accelerations = a
        self.bath_temperature = bath_temperature
        self.energy = energy

    def __str__(self):
        temp = f"E={self.energy}, temp={self.bath_temperature}\n"
        n_atoms = len(self.positions)
        for atom in range(1,n_atoms+1):
            x,v,a = self.positions[atom],self.velocities[atom],self.accelerations[atom]
            temp += f"{atom:3d} [ {x[1]:8.3f} {x[2]:8.3f} {x[3]:8.3f} ] [ {v[1]:8.3f} {v[2]:8.3f} {v[3]:8.3f} ] [ {a[1]:10.2E} {a[2]:10.2E} {a[3]:10.2E} ]\n"
        return temp[:-1]

    def next(self, temp=None, forwards=True):
        """
        Computes next frame using ``self.trajectory.integrator``.

        The desired bath temperature is not used in the current force calculations, but is passed to the output frame.
        """
        if temp is None:
            temp = self.bath_temperature
        assert isinstance(temp, float) or isinstance(temp, int), "temp must be numeric!"

        integrator = self.trajectory.integrator
        energy, new_x, new_v, new_a = integrator.next(self, forwards=forwards)
        self.energy = energy
        return Frame(self.trajectory, new_x, new_v, new_a, temp)

    def prev(self, temp=None):
        """
        Computes previous frame using ``self.trajectory.integrator``.

        The desired bath temperature is not used in the current force calculations, but is passed to the output frame.
        """
        return self.next(temp, forwards=False)

    def temperature(self):
        """
        Computes the temperature based on the equipartition theorem, counting only the active atoms.

        T = sum{ m_i * v_i ** 2 / (kB * Nf) }
        """
        v = [np.linalg.norm(x) for x in self.velocities[self.trajectory.active_atoms]]
        m = self.trajectory.masses[self.trajectory.active_atoms].reshape(-1,1)
        K = m * np.power(v, 2)
        return float(np.mean(K)) / (3 * presto.constants.BOLTZMANN_CONSTANT)

    def pressure(self):
        """
        Computes the pressure based on the following formula:

        P = 1/(3*V) * (\sum{m_i * v_i * v_i + r_i * f_i}
        """
        m = self.trajectory.masses[self.trajectory.active_atoms]
        tot = 0
        for i in range(1, len(self.positions) + 1):
            tot += np.dot(m[i] * self.velocities[i], self.velocities[i]) + np.dot(self.positions[i], self.accelerations[i] *  m[i])

        return tot/3 * self.volume()

    def volume(self):
        return self.molecule().volume()

    def inactive_mask(self):
        """
        Returns an ``np.ndarray`` of the same length as ``positions`` where every active atom is ``False`` and every inactive atom is ``True``.
        """
        inactive_mask = np.ones(shape=len(self.positions)).view(cctk.OneIndexedArray)
        inactive_mask[self.trajectory.active_atoms] = 0
        inactive_mask  = inactive_mask.astype(bool)
        return inactive_mask

    def molecule(self):
        return cctk.Molecule(self.trajectory.atomic_numbers, self.positions)

    def remove_com_motion(self):
        # move centroid to origin
        centroid = np.mean(self.positions, axis=0)
        self.positions = self.positions - centroid

        #### subtract out center-of-mass translational motion
        com_translation = np.sum(self.trajectory.masses.reshape(-1,1) * self.velocities, axis=0)
        correction_tran = np.tile(com_translation / np.sum(self.trajectory.masses), (len(self.velocities),1))
        self.velocities = self.velocities - correction_tran
        assert np.linalg.norm(np.sum(self.trajectory.masses.reshape(-1,1) * self.velocities, axis=0)) < 0.0001, "didn't remove COM translation well enough!"

        ### subtract out center-of-mass rotational motion - this was really difficult for me to figure out :'(
        com_rotation = np.sum(np.cross(self.velocities, self.trajectory.masses.reshape(-1,1) * self.positions), axis=0)
        correction_r = np.cross(self.positions, np.tile(com_rotation / np.sum(self.trajectory.masses), (len(self.velocities),1))) / np.linalg.norm(self.positions, axis=1).reshape(-1,1) ** 2
        self.velocities = self.velocities - correction_r
        assert np.linalg.norm(np.sum(self.trajectory.masses.reshape(-1,1) * self.velocities, axis=0)) < 0.0001, "didn't remove COM translation well enough!"
        assert np.linalg.norm(np.sum(self.trajectory.masses.reshape(-1,1) * np.cross(self.velocities, self.positions), axis=0)) < 0.0001, "didn't remove COM rotation well enough!"

        return self


