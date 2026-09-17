"""Independent numerical oracle for the live-agent exercise; never supplies model replies."""
import argparse
import importlib.util
import itertools
import json
from pathlib import Path
import sys


def verify(project):
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location('live_staffing', Path(project) / 'staffing.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    checks = 0
    for counts in itertools.product(range(3), repeat=6):
        requirements = {1: counts[0], 2: counts[1]}
        available = {1: counts[2], 2: counts[3]}
        training = {1: counts[4], 2: counts[5]}
        originals = [dict(x) for x in (requirements, available, training)]
        if any(training[k] > available[k] for k in training):
            try:
                module.allocate_staff(requirements, available, training)
            except ValueError:
                pass
            else:
                raise AssertionError('Invalid training capacity accepted: ' + repr(counts))
        else:
            shortages = {k: count - (available.get(k, 0) - training.get(k, 0))
                         for k, count in requirements.items() if count > available.get(k, 0) - training.get(k, 0)}
            expected = {'can_run': not shortages, 'assigned': {} if shortages else {k: v for k, v in requirements.items() if v}, 'shortages': shortages}
            assert module.allocate_staff(requirements, available, training) == expected, repr(counts)
        assert [requirements, available, training] == originals, 'Inputs mutated'
        checks += 1
    invalid = [None, [], {True: 1}, {0: 1}, {-1: 1}, {1.0: 1}, {'1': 1}, {1: True}, {1: -1}, {1: 1.5}, {1: '2'}]
    for value in invalid:
        for position in range(3):
            args = [{}, {}, {}]; args[position] = value
            try:
                module.allocate_staff(*args)
            except ValueError:
                pass
            else:
                raise AssertionError('Invalid input accepted: ' + repr(args))
            checks += 1
    assert module.allocate_staff({4: 2}, {5: 99}, {}) == {'can_run': False, 'assigned': {}, 'shortages': {4: 2}}
    checks += 1
    return {'oracle_checks': checks, 'passed': True, 'scope': 'Exact-level staffing, training capacity, stopped jobs, invalid input, and nonmutation'}


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('project')
    print(json.dumps(verify(p.parse_args().project), indent=2))
