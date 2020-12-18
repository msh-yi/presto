import presto, logging, time
import numpy as np

logger = logging.getLogger(__name__)

class Controller():
    """
    Controls actual running of frames. Dispatches to other objects.
    """
    def __init__(self, trajectory):
        assert isinstance(trajectory.calculator, presto.calculators.Calculator)
        assert isinstance(trajectory.integrator, presto.integrators.Integrator)
        assert all([isinstance(c, presto.checks.Check) for c in trajectory.checks])
        assert all([isinstance(r, presto.reporters.Reporter) for r in trajectory.reporters])

        self.trajectory = trajectory

    def run(self, checkpoint_interval=25, end_time=None, runtime=None, keep_all=False):
        current_time = self.trajectory.frames[-1].time

        if end_time is None:
            if runtime is not None:
                assert isinstance(runtime, (int, float)), "runtime must be numeric"
                end_time = current_time + runtime
            else:
                end_time = self.trajectory.stop_time
        else:
            assert isinstance(end_time, (int, float)), "end_time must be numeric"

        assert end_time > current_time, f"error: end_time {end_time} must be greater than current_time {current_time}"

        count = 0
        finished_early = False
        while current_time < end_time:
            current_time += self.trajectory.timestep
            current_frame = self.trajectory.frames[-1]

            bath_temperature = current_frame.bath_temperature
            if isinstance(self.trajectory, presto.trajectory.EquilibrationTrajectory):
                bath_temperature = self.trajectory.bath_scheduler(current_time)

            new_frame = current_frame.next(forwards=self.trajectory.forwards, temp=current_frame.bath_temperature)
            assert new_frame.time == current_time, f"frame time {new_frame.time} does not match loop time {current_time}"
            self.trajectory.frames.append(new_frame)

            for check in self.trajectory.checks:
                if int(current_time % check.interval) == 0:
                    check.check_frame(new_frame)

            for reporter in self.trajectory.reporters:
                if int(current_time % reporter.interval) == 0:
                    reporter.report(self.trajectory)

            # do we initiate early stopping?
            if not finished_early:
                if isinstance(self.trajectory, presto.trajectory.ReactionTrajectory):
                    if self.trajectory.termination_function(self.frames[-1]):
                        end_time = current_time + self.trajectory.time_after_finished
                        finished_early = True
                        logger.info(f"Reaction trajectory finished! {self.trajectory.time_after_finished} additional fs will be run.")

            if int(current_time % checkpoint_interval) == 0:
                self.trajectory.save(keep_all=keep_all)

            count += 1
            if count < 10:
                logger.info(f"Run initiated ok - frame {count:05d} completed in {new_frame.elapsed:.2f} s.")

        self.trajectory.save(keep_all=keep_all)
        if current_time == self.trajectory.stop_time:
            self.trajectory.finished = True
        elif finished_early:
            self.trajectory.finished = self.trajectory.termination_function(self.frames[-1])

        logger.info(f"Trajectory finished with {self.trajectory.num_frames()} frames.")
        return
