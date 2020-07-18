import sys, re, glob, cctk, argparse, yaml, os
import numpy as np
import subprocess as sp

parser = argparse.ArgumentParser(prog="wham.py")
parser.add_argument("-c", "--config", type=str, default="wham.chk")
parser.add_argument("type", type=str)
parser.add_argument("atom1", type=int)
parser.add_argument("atom2", type=int)
parser.add_argument("min_x", type=float)
parser.add_argument("max_x", type=float)
parser.add_argument("num", type=int)
parser.add_argument("chks")
args = vars(parser.parse_args(sys.argv[1:]))

print("wham - weighted histogram analysis method")

if args["type"] == "run":
    delta = (args["max_x"] - args["min_x"]) / args["num"]
    k = 3 / delta
    print(f"∆x: {delta:.3f}\tk = {k:.2f} kcal/(mol • Å)")

    settings = None
    assert os.path.exists(args["config"]), f"can't find file {args['config']}"
    print(f"reading {args['config']} as input file")
    with open(args["config"]) as config:
        settings = yaml.full_load(config)

    print("generating input files")
    files = glob.glob(args["chks"], recursive=True)
    count = 0
    for i, x in np.ndenumerate(np.linspace(args["min_x"], args["max_x"], args["num"])):
        for file in files:
            name = file.rsplit('/',1)[-1]
            name = re.sub(".chk", f"_{i:04d}")

            if "constraints" in settings:
                settings["constraints"]["wham"] = f"{args['atom1']} {args['atom2']} {x} {k:.3f}"
            else:
                settings["constraints"] = {"wham": f"{args['atom1']} {args['atom2']} {x} {k:.3f}"}

            with open(f"{name}.yaml") as config:
                yaml.dump(settings, config)

            traj = presto.config.build(f"{name}.yaml", f"{name}.chk", oldchk=file)

            count += 1

    print(f"wrote {count} files for submission")

elif args["type"] == "analyze":
    ...

else:
    raise ValueError(f"invalid type {args['type']} - need either ``run`` or ``analyze``!")

