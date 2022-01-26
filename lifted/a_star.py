import math

from pddlstream.language.conversion import fact_from_evaluation

import sys
sys.path.insert(
    0,
    "/home/mohammed/drake-tamp/pddlstream/FastDownward/builds/release64/bin/translate/",
)
sys.path.insert(
    0, "/home/atharv/drake-tamp/pddlstream/FastDownward/builds/release64/bin/translate/"
)
from pddl.conditions import Atom
import time
from lifted.utils import PriorityQueue
from lifted.search import ActionStreamSearch
from lifted.sampling import (
    extract_stream_plan_from_path,
    sample_depth_first_with_costs,
    extract_stream_ordering,
)


def try_a_star(search, cost, heuristic, max_step=10000):
    start_time = time.time()
    q = PriorityQueue([search.init])
    closed = []
    expand_count = 0
    evaluate_count = 0
    found = False

    current_length = 0

    while q and expand_count < max_step:
        state = q.pop()
        expand_count += 1

        if search.test_goal(state):
            found = True
            break

        for op, child in search.successors(state):
            # state.children.append((op, child))
            if (
                child.unsatisfiable
            ):  # or any([search.test_equal(child, node) for node in closed]):
                continue

            is_unique = True
            for node in closed:
                if search.test_equal(child, node):
                    is_unique = False

                    dup1 = False
                    for _, parent in node.parents:
                        if search.test_equal(parent, state):
                            dup1 = True
                            break
                    if not dup1:
                        node.parents.add((op, state))

                    dup1 = False
                    for _, _c in state.children:
                        if search.test_equal(node, _c):
                            dup1 = True
                            break
                    if not dup1:
                        state.children.add((op, node))

            if not is_unique:
                continue

            state.children.add((op, child))
            evaluate_count += 1
            child.start_distance = state.start_distance + cost(state, op, child)
            q.push(child, child.start_distance + heuristic(child))

        closed.append(state)

    av_branching_f = evaluate_count / expand_count
    approx_depth = math.log(1e-6 + evaluate_count) / math.log(1e-6 + av_branching_f)
    print(f"Explored {expand_count}. Evaluated {evaluate_count}")
    print(f"Av. Branching Factor {av_branching_f:.2f}. Approx Depth {approx_depth:.2f}")
    print(f"Time taken: {(time.time() - start_time)} seconds")
    print(f"Solution cost: {state.start_distance}")

    if found:
        return state
    else:
        return None


def repeated_a_star(search, max_steps=1000, stats={}):

    # cost = lambda state, op, child: 1 / (child.num_successes / child.num_attempts)
    def cost(state, op, child, verbose=False):
        included = set()
        c = 0
        for obj in child.object_stream_map:
            stream_action = child.object_stream_map[obj]
            if stream_action is not None:
                if stream_action in included:
                    continue
                cg_key = child.get_object_computation_graph_key(obj)
                if cg_key in stats:
                    s = stats[cg_key]
                    comp_cost = (
                        (s["num_successes"] + 1) / (s["num_attempts"] + 1)
                    ) ** -1
                    c += comp_cost

                else:
                    comp_cost = 1
                    c += comp_cost
                if verbose:
                    print(stream_action, comp_cost)
                included.add(stream_action)
        return max(1, c)

    def heuristic(state):
        # actions = [a for _, a, _ in state.get_shortest_path_to_start()]
        # if len(actions) >= 2:
        #     if actions[-1] is not None and "move" in actions[-1].name:
        #         if actions[-2] is not None and "move" in actions[-2].name:
        #             return np.inf
        # return len(goal - state.state) * 4
        return 0

    stats = {}
    for _ in range(max_steps):
        goal_state = try_a_star(search, cost, heuristic)
        if goal_state is None:
            print("Could not find feasable action plan!")
            break

        print("Getting path ...")
        path = goal_state.get_shortest_path_to_start()
        c = 0
        for idx, i in enumerate(path):
            print(idx, i[1])
            a = cost(*i, verbose=True)
            print("action cost:", a)
            print("cum cost:", a + c)
            c += a

        action_skeleton = [a for _, a, _ in goal_state.get_shortest_path_to_start()]
        actions_str = "\n".join([str(a) for a in action_skeleton])
        print(f"Action Skeleton:\n{actions_str}")

        stream_plan = extract_stream_plan_from_path(path)
        stream_ordering = extract_stream_ordering(stream_plan)
        if not stream_ordering:
            break
        object_mapping = sample_depth_first_with_costs(
            stream_ordering, goal_state, stats
        )
        if object_mapping is not None:
            break
        print("Could not find object_mapping, retrying with updated costs")

    if goal_state is not None:
        action_skeleton = [a for _, a, _ in goal_state.get_shortest_path_to_start()]
        return action_skeleton, object_mapping, goal_state


if __name__ == "__main__":
    from experiments.blocks_world_noaxioms.run import *
    from pddlstream.algorithms.algorithm import parse_problem
    import argparse

    url = None
    # url = 'tcp://127.0.0.1:6000'

    # naming scheme: <num_blocks>_<num_blockers>_<maximum_goal_stack_height>_<index>
    # problem_file = 'experiments/blocks_world/data_generation/random/train/1_0_1_40.yaml'
    # problem_file = 'experiments/blocks_world/data_generation/random/train/1_1_1_52.yaml'
    # problem_file = "experiments/blocks_world/data_generation/non_monotonic/train/1_1_1_0.yaml"
    # problem_file = 'experiments/blocks_world/data_generation/non_monotonic/train/1_1_1_0_easy.yaml'
    # problem_file = 'experiments/blocks_world/data_generation/non_monotonic/train/2_2_1_55.yaml'

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-t", "--task", help="Task description file", required=True, type=str
    )
    parser.add_argument(
        "-s",
        "--save-cgstats-path",
        help="Path to save a json of CG stats.",
        default=None,
        type=str,
    )
    parser.add_argument(
        "-p", "--profile", help="Path to save a profile.", default=None, type=str
    )
    args = parser.parse_args()
    problem_file = args.task

    (
        sim,
        station_dict,
        traj_directors,
        meshcat_vis,
        prob_info,
    ) = make_and_init_simulation(url, problem_file)
    problem, model_poses = construct_problem_from_sim(sim, station_dict, prob_info)
    evaluations, goal_exp, domain, externals = parse_problem(problem)


    init = set()
    for evaluation in evaluations:
        x = fact_from_evaluation(evaluation)
        init.add(Atom(x[0], [o.pddl for o in x[1:]]))

    goal = set()
    assert goal_exp[0] == "and"
    for x in goal_exp[1:]:
        goal.add(Atom(x[0], [o.pddl for o in x[1:]]))

    print("Initial:", init)
    print("\n\nGoal:", goal)
    [pick, move, place, stack, unstack] = domain.actions

    search = ActionStreamSearch(init, goal, externals, domain.actions)

    from ompl.util import setLogLevel, LogLevel
    setLogLevel(LogLevel.LOG_WARN)

    if args.profile is not None:
        import lifted
        from line_profiler import LineProfiler
        profile = LineProfiler()
        profile.add_function(repeated_a_star)
        profile.add_function(try_a_star)
        profile.add_function(lifted.find_applicable_brute_force)
        profile.add_function(ActionStreamSearch.test_equal)
        profile.enable()

    start_time = time.time()
    try:
        stats = {}
        result = repeated_a_star(search, stats=stats)
        if result is not None:
            action_skeleton, object_mapping, goal_state = result
            actions_str = "\n".join([str(a) for a in action_skeleton])
            print(f"Action Skeleton:\n{actions_str}")
            print(f"\nObject mapping: {object_mapping}\n")
        if args.save_cgstats_path:
            with open(args.save_cgstats_path, "w") as f:
                json.dump(list((tuple(cg), v) for (cg, v) in stats.items()), f)

    except KeyboardInterrupt:
        print("KeyboardInterrupt while searching.")

    print(f"Total time: {(time.time() - start_time):.4f}")

    if args.profile is not None:
        profile.print_stats()
        profile.print_stats(open(args.profile, "w"))