import numpy as np
import os, random, string, re, cctk, ctypes, copy, shutil, tempfile, logging
import time as timelib
import subprocess as sp
import multiprocessing as mp
import presto

logger = logging.getLogger(__name__)

############################
### Directory Parameters ###
############################

# A-Za-z0-9
LETTERS_AND_DIGITS = string.ascii_letters + string.digits

# where the xtb GFNn config files are stored
XTB_PATH = presto.config.XTB_PATH

###########################

class Calculator():
    """
    Generic calculator class.
    """

    def __init__(self):
        pass

    def evaluate(self, atomic_numbers, positions, high_atoms, pipe, time=None):
        """
        Return energy, forces
        """
        pass

class NullCalculator(Calculator):
    """
    Does nothing except evaluate constraints. Mostly for testing purposes, but I guess you could also model something insanely boring this way.
    """
    def __init__(self, constraints=list()):
        for c in constraints:
            assert isinstance(c, presto.constraints.Constraint), "{c} is not a valid constraint!"
        self.constraints = constraints

    def evaluate(self, atomic_numbers, positions, high_atoms=None, pipe=None, time=None):
        energy = 0
        forces = np.zeros_like(positions).view(cctk.OneIndexedArray)

        for c in self.constraints:
            f, e = c.evaluate(positions, time=time)
            energy += e
            forces += f

        if pipe is not None:
            assert isinstance(pipe, mp.connection.Connection), "not a valid Connection instance!"
            pipe.send([energy, forces])
            pipe.close()
        else:
            return energy, forces


class XTBCalculator(Calculator):
    """
    Attributes:
        charge (int):
        multiplicity (int):
        gfn (int or str):
        parallel (int):
        xcontrol_path (str):
        topology (str): path to gfn-ff topology file, if gfn is ``ff``.
    """

    def __init__(self, charge=0, multiplicity=1, gfn=2, parallel=1, constraints=list(), xcontrol_path=None, topology=None):
        assert isinstance(charge, int)
        assert isinstance(multiplicity, int)
        assert isinstance(gfn, (int, str))
        assert isinstance(parallel, int)

        self.charge = charge
        self.multiplicity = multiplicity
        self.gfn = gfn
        self.parallel = parallel

        for c in constraints:
            assert isinstance(c, presto.constraints.Constraint), "{c} is not a valid constraint!"
        self.constraints = constraints

        if xcontrol_path is not None:
            assert isinstance(xcontrol_path, str)
        self.xcontrol_path = xcontrol_path

        if gfn == "ff":
            assert isinstance(topology, str), "need path for topology file!"
        self.topology = topology

    def evaluate(self, atomic_numbers, positions, high_atoms=None, pipe=None, time=None):
        """
        Gets the electronic energy and cartesian forces for the specified geometry.

        Args:
            atomic_numbers (cctk.OneIndexedArray): the atomic numbers (int)
            positions (cctk.OneIndexedArray): the atomic positions in angstroms
            high_atoms (np.ndarray): do nothing with this
            pipe (): for multiprocessing, the connection through which objects should be returned to the parent process

        Returns:
            energy (float): in Hartree
            forces (cctk.OneIndexedArray): in amu Å per fs**2
            time (float): in seconds
        """
        assert shutil.which(presto.config.XTB_EXEC), f"bad xtb executable {presto.config.XTB_EXEC}"

        old_working_directory = os.getcwd()

        # build xtb command
        command = presto.config.XTB_EXEC
        if self.gfn == "ff":
            command += " --gfnff"
        else:
            command += f" --gfn {self.gfn}"
        command += f" --chrg {self.charge} --uhf {self.multiplicity - 1}"
        if self.parallel > 1:
            command += f" --parallel {self.parallel}"
        if self.xcontrol_path:
            command += f" --input {self.xcontrol_path}"
        command += " --grad xtb-in.xyz &> xtb-out.out"

        # set env variables
        os.environ["OMP_NUM_THREADS"] = str(self.parallel)
        os.environ["MKL_NUM_THREADS"] = str(self.parallel)
        os.environ["OMP_STACKSIZE"] = "4G"
        os.environ["OMP_MAX_ACTIVE_LEVELS"] = "1"
        os.environ["XTBPATH"] = XTB_PATH
        os.environ["XTBHOME"] = XTB_PATH

        energy, forces = None, None
        elapsed = 0
        with tempfile.TemporaryDirectory() as tmpdir:
            if self.topology and os.path.exists(self.topology):
                shutil.copyfile(self.topology, f"{tmpdir}/gfnff_topo")

            molecule = cctk.Molecule(atomic_numbers, positions, charge=self.charge, multiplicity=self.multiplicity)
            cctk.XYZFile.write_molecule_to_file(f"{tmpdir}/xtb-in.xyz", molecule)

            # run xtb
            try:
                start = timelib.time()
                result = sp.run(command, cwd=tmpdir, shell=True, capture_output=True)
                end = timelib.time()
                elapsed = end - start

                result.check_returncode()
                assert os.path.isfile(f"{tmpdir}/energy"), "no energy file!"
                assert os.path.isfile(f"{tmpdir}/gradient"), "no gradient file!"
            except Exception as e:
                shutil.copyfile(f"{tmpdir}/xtb-in.xyz", f"{old_working_directory}/xtb-failed-input.xyz")
                shutil.copyfile(f"{tmpdir}/xtb-out.out", f"{old_working_directory}/xtb-failed-output.out")
                raise ValueError(f"xtb failed:\n{e}\nfiles:{os.listdir(tmpdir)}")

            # parse energy
            with open(f"{tmpdir}/energy", "r") as f:
                energy_lines = f.read().splitlines()
            energy = float(energy_lines[1].split()[1])

            # parse forces
            with open(f"{tmpdir}/gradient", "r") as f:
                gradient_lines = f.read().splitlines()
            forces = []
            for line in gradient_lines:
                fields = line.split()
                if len(fields) == 3:
                    x,y,z = float(fields[0]), float(fields[1]), float(fields[2])
                    forces.append([-x,-y,-z])
            forces = cctk.OneIndexedArray(forces)
            forces = forces * presto.constants.AMU_A2_FS2_PER_HARTREE_BOHR
            assert len(forces) == molecule.get_n_atoms(), "unexpected number of atoms"

            if self.gfn == "ff" and not os.path.exists(self.topology):
                assert os.path.exists(f"{tmpdir}/gfnff_topo"), "xtb didn't generate topology file!"
                shutil.copyfile(f"{tmpdir}/gfnff_topo", self.topology)

            # restore working directory
            os.chdir(old_working_directory)

        # apply constraints
        for c in self.constraints:
            f, e = c.evaluate(positions, time=time)
            energy += e
            forces += f

        # return result
        if pipe is not None:
            assert isinstance(pipe, mp.connection.Connection), "not a valid Connection instance!"
            pipe.send([energy, forces])
            pipe.close()
        else:
            return energy, forces

class GaussianCalculator(Calculator):
    def __init__(
            self,
            charge=0,
            multiplicity=1,
            link0={"mem":"1GB", "nprocshared":"4"},
            route_card="#p hf/3-21g force",
            footer=None,
            constraints=list(),
            gaussian_chk=None,
        ):
        if not re.search("force", route_card):
            raise ValueError("need a force job to calculate forces")

        self.charge = charge
        self.multiplicity = multiplicity
        self.link0 = link0
        self.route_card = route_card
        self.footer = footer

        for c in constraints:
            assert isinstance(c, presto.constraints.Constraint), f"{c} is not a valid constraint!"
        self.constraints = constraints

        if gaussian_chk is not None:
            assert isinstance(gaussian_chk, str), "gaussian_chk must be string"
        self.gaussian_chk = gaussian_chk

    def evaluate(self, atomic_numbers, positions, high_atoms=None, pipe=None, qc=False, time=None):
        """
        Gets the electronic energy and Cartesian forces for the specified geometry.

        Args:
            atomic_numbers (cctk.OneIndexedArray): the atomic numbers (int)
            positions (cctk.OneIndexedArray): the atomic positions in angstroms
            high_atoms (np.ndarray): do nothing with this
            pipe (): for multiprocessing, the connection through which objects should be returned to the parent process
            qc (bool): try quadratic convergence for tricky cases, only after default DIIS fails

        Returns:
            energy (float): in Hartree
            forces (cctk.OneIndexedArray): in amu Å per fs**2
        """
        assert shutil.which(presto.config.G16_EXEC), f"bad Gaussian executable {presto.config.G16_EXEC}"

        old_working_directory = os.getcwd()

        molecule = cctk.Molecule(atomic_numbers, positions, charge=self.charge, multiplicity=self.multiplicity)
        route_card = self.route_card
        if qc:
            logger.warning(f"warning: initial DIIS convergence failed at {time} fs, trying quadratic convergence")
            route_card = f"{self.route_card} scf=qc"

        energy, forces = None, None
        elapsed = 0
        with tempfile.TemporaryDirectory() as tmpdir:
            link0 = copy.deepcopy(self.link0)

            # we will try and use the previous checkpoint file to accelerate the current run.
            if self.gaussian_chk:
                link0["chk"] = "chk.chk"

                if os.path.exists(self.gaussian_chk):
                    shutil.copyfile(self.gaussian_chk, f"{tmpdir}/oldchk.chk")
                    link0["oldchk"] = "oldchk.chk"
                    route_card = f"{route_card} guess=read"

            cctk.GaussianFile.write_molecule_to_file(f"{tmpdir}/g16-in.gjf", molecule, route_card, link0, self.footer)
            command = f"{presto.config.G16_EXEC} g16-in.gjf g16-out.out"

            # run g16
            gaussian_file = None
            try:
                start = timelib.time()
                result = sp.run(command, cwd=tmpdir, shell=True, capture_output=True)
                end = timelib.time()
                elapsed = end - start

                result.check_returncode()
                assert os.path.isfile(f"{tmpdir}/g16-out.out"), "no energy file!"

                # moved this inside the try/except because sometimes gaussian dies "silently" e.g. without a non-zero returncode
                gaussian_file = cctk.GaussianFile.read_file(f"{tmpdir}/g16-out.out")
            except Exception as e:
                try:
                    assert qc is False # retry with quadratic convergence
                    self.evaluate(atomic_numbers, positions, high_atoms, pipe, qc=True)
                except Exception as e:
                    shutil.copyfile(f"{tmpdir}/g16-in.gjf", f"{old_working_directory}/g16-failed-input.gjf")
                    shutil.copyfile(f"{tmpdir}/g16-out.out", f"{old_working_directory}/g16-failed-output.out")
                    raise ValueError(f"g16 failed:\n{e}\nfiles:{os.listdir(tmpdir)}")

            # check we read ok
            assert gaussian_file is not None, "g16 must not have worked right"

            # extract output
            ensemble = gaussian_file.ensemble
            molecule = ensemble.molecules[-1]

            properties_dict = ensemble.get_properties_dict(molecule)
            energy = properties_dict["energy"]
            forces = properties_dict["forces"]
            forces = forces * presto.constants.AMU_A2_FS2_PER_HARTREE_BOHR

            # save new checkpoint file
            if self.gaussian_chk and os.path.exists(f"{tmpdir}/chk.chk"):
                shutil.copyfile(f"{tmpdir}/chk.chk", self.gaussian_chk)

        # restore working directory
        os.chdir(old_working_directory)

        # apply constraints
        for c in self.constraints:
            f, e = c.evaluate(positions, time=time)
            energy += e
            forces += f

        # return result
        if pipe is not None:
            assert isinstance(pipe, mp.connection.Connection), "not a valid Connection instance!"
            pipe.send([energy, forces])
            pipe.close()
        else:
            return energy, forces

class ONIOMCalculator(Calculator):
    """
    Composes two other calculators, and computes forces according to the ONIOM embedding scheme.
    """
    def __init__(self, high_calculator, low_calculator, constraints=list()):
        assert isinstance(high_calculator, Calculator), "high calculator isn't a proper Calculator!"
        assert isinstance(low_calculator, Calculator), "low calculator isn't a proper Calculator!"
        self.high_calculator = high_calculator
        self.low_calculator = low_calculator

        # prevent namespace collisions
        self.full_calculator = copy.deepcopy(self.low_calculator)
        if isinstance(self.full_calculator, XTBCalculator):
            if self.full_calculator.gfn == "ff":
                self.full_calculator.topology = self.full_calculator.topology.replace(".low", ".full")

        # atom numbering gets messed up if you don't give full_calculator the constraints
        for c in constraints:
            assert isinstance(c, presto.constraints.Constraint), f"{c} is not a valid constraint!"
        self.full_calculator.constraints = constraints
        self.constraints = constraints


    def evaluate(self, atomic_numbers, positions, high_atoms, pipe=None, time=None):
        """
        Evaluates the forces according to the ONIOM embedding scheme.
        """
        assert len(high_atoms) > 0, "no point in doing ONIOM without a high layer!"
        assert isinstance(atomic_numbers, cctk.OneIndexedArray), "need to pass one-indexed array for indexing to work properly"
        assert isinstance(positions, cctk.OneIndexedArray), "need to pass one-indexed array for indexing to work properly"

        high_atomic_numbers = atomic_numbers[high_atoms]
        high_positions = positions[high_atoms]

        parent_hh, child_hh = mp.Pipe()
        process_hh = mp.Process(target=self.high_calculator.evaluate, kwargs={
            "atomic_numbers": high_atomic_numbers,
            "positions": high_positions,
            "pipe": child_hh,
            "time": time,
        })
        process_hh.start()

        parent_hl, child_hl = mp.Pipe()
        process_hl = mp.Process(target=self.low_calculator.evaluate, kwargs={
            "atomic_numbers": high_atomic_numbers,
            "positions": high_positions,
            "pipe": child_hl,
            "time": time,
        })
        process_hl.start()

        parent_ll, child_ll = mp.Pipe()
        process_ll = mp.Process(target=self.full_calculator.evaluate, kwargs={
            "atomic_numbers": atomic_numbers,
            "positions": positions,
            "pipe": child_ll,
            "time": time,
        })
        process_ll.start()

        e_hh, f_hh = parent_hh.recv()
        e_hl, f_hl = parent_hl.recv()
        e_ll, f_ll = parent_ll.recv()

        # 1 hour is the limit, we're not waiting any longer than that!
        process_hh.join(3600)
        process_hl.join(3600)
        process_ll.join(3600)

        # check things actually finished okay
        assert process_hh.exitcode == 0, f"process_hh exited not-ok with exit code {process_hh.exitcode}"
        assert process_hl.exitcode == 0, f"process_hl exited not-ok with exit code {process_hl.exitcode}"
        assert process_ll.exitcode == 0, f"process_ll exited not-ok with exit code {process_ll.exitcode}"

        # do the ONIOM combination
        energy = e_hh + e_ll - e_hl
        forces = f_ll
        forces[high_atoms] = f_hh + f_ll[high_atoms] - f_hl

        return energy, forces

def build_calculator(settings, checkpoint_filename, constraints=list(), ):
    """
    Build calculator from settings dict.
    """
    assert isinstance(settings, dict), "Need to pass a dictionary!!"
    assert "type" in settings, "Need `type` for calculator"
    assert isinstance(settings["type"], str), "Calculator `type` must be a string"

    if settings["type"].lower() == "oniom":
        assert "high_calculator" in settings, "Need `high_calculator` settings dictionary for ONIOM!"
        assert "low_calculator" in settings, "Need `low_calculator` settings dictionary for ONIOM!"
        return ONIOMCalculator(
            high_calculator=build_calculator(settings["high_calculator"], checkpoint_filename + ".high"),
            low_calculator=build_calculator(settings["low_calculator"], checkpoint_filename + ".low"),
            constraints=constraints,
        )
    elif settings["type"].lower() == "gaussian":
        charge = 0
        if "charge" in settings:
            assert isinstance(settings["charge"], int), "Calculator `charge` must be an integer."
            charge = settings["charge"]

        multiplicity = 1
        if "multiplicity" in settings:
            assert isinstance(settings["multiplicity"], int), "Calculator `multiplicity` must be an integer."
            assert settings["multiplicity"] > 0, "Calculator `multiplicity` must be positive."
            multiplicity = settings["multiplicity"]

        link0 = None
        if "link0" in settings:
            assert isinstance(settings["link0"], dict), "Calculator `link0` must be a dictionary."
            link0 = settings["link0"]

        assert "route_card" in settings, "Need a route card for `GaussianCalculator`!"
        assert isinstance(settings["route_card"], str), "Calculator `route_card` must be a string."
        assert re.search("#p", settings["route_card"]), "Need `#p` in route card!"
        assert re.search("force", settings["route_card"]), "Need `force` in route card!"

        footer = None
        if "footer" in settings:
            assert isinstance(settings["footer"], str), "Calculator `footer` must be string or `None`."
            footer = settings["footer"]

        gaussian_chk = None
        if "gaussian_chk" in settings:
            if isinstance(settings["gaussian_chk"], str):
                gaussian_chk = settings["gaussian_chk"]
            elif settings["gaussian_chk"] is True:
                gaussian_chk = f"{checkpoint_filename}.gchk"
            else:
                raise ValueError("`gaussian_chk` must be string or True!")

        if link0 is not None:
            return GaussianCalculator(charge=charge, multiplicity=multiplicity, link0=link0, route_card=settings["route_card"], footer=footer, constraints=constraints, gaussian_chk=gaussian_chk)
        else:
            return GaussianCalculator(charge=charge, multiplicity=multiplicity, route_card=settings["route_card"], footer=footer, constraints=constraints, gaussian_chk=gaussian_chk)

    elif settings["type"].lower() == "xtb":
        charge = 0
        if "charge" in settings:
            assert isinstance(settings["charge"], int), "Calculator `charge` must be an integer."
            charge = settings["charge"]

        multiplicity = 1
        if "multiplicity" in settings:
            assert isinstance(settings["multiplicity"], int), "Calculator `multiplicity` must be an integer."
            assert settings["multiplicity"] > 0, "Calculator `multiplicity` must be positive."
            multiplicity = settings["multiplicity"]

        gfn = 2
        if "gfn" in settings:
            assert settings["gfn"] in [0, 1, 2, "ff"], "Calculator `gfn` must be 0, 1, 2, or ``ff``."
            gfn = settings["gfn"]

        parallel = 1
        if "parallel" in settings:
            assert isinstance(settings["parallel"], int), "Calculator `parallel` must be an integer."
            assert settings["parallel"] > 0, "Calculator `parallel` must be positive."
            parallel = settings["parallel"]

        xcontrol = None
        if "xcontrol" in settings:
            assert isinstance(settings["xcontrol"], str), "Calculator `xcontrol` must be a string."
            xcontrol = settings["xcontrol"]

        topology = None
        if "topology" in settings:
            assert isinstance(settings["topology"], str), "Calculator `topology` must be a string (path where topology will be stored)."
            topology = settings["topology"]
        elif gfn == "ff":
            topology = f"{checkpoint_filename}.top"

        return XTBCalculator(charge=charge, multiplicity=multiplicity, gfn=gfn, parallel=parallel, constraints=constraints, xcontrol_path=xcontrol, topology=topology)

    else:
        raise ValueError(f"Unknown integrator type {settings['type']}! Allowed options are `oniom`, `xtb`, or `gaussian`.")


