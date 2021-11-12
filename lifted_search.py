
#%%
from pddlstream.language.conversion import fact_from_evaluation, objects_from_evaluations
import sys

from pddlstream.language.stream import Stream

sys.path.insert(0, '/home/mohammed/drake-tamp/pddlstream/FastDownward/builds/release64/bin/translate/')
import pddl
from pddl.conditions import Atom, Conjunction, NegatedAtom
from pddl.actions import PropositionalAction, Action
from pddl.effects import Effect
import pddl.conditions as conditions

from pddl.pddl_types import TypedObject
import pddl.conditions as conditions

class Identifiers:
    idx = 0
    @classmethod
    def next(cls):
        cls.idx += 1
        return f'?x{cls.idx}'

def combinations(candidates):
    """Given a dictionary from key (k^j) to a set of possible values D_k^j, yield all the
    tuples [(k^j, v_i^j)] where v_i^j is an element of D_k^j"""
    keys, domains = zip(*candidates.items())
    for combo in itertools.product(*domains):
        yield dict(zip(keys, combo))
# @profile
def bind_action(action, assignment):
    for par in action.parameters:
        if par.name not in assignment:
            assignment[par.name] = Identifiers.next()

    assert isinstance(action.precondition, Conjunction)
    precondition_parts = []
    for atom in action.precondition.parts:
        args = [assignment.get(arg, arg) for arg in atom.args]
        atom = Atom(atom.predicate, args) if not atom.negated else NegatedAtom(atom.predicate, args)
        precondition_parts.append(atom)

    effects = []
    for effect in action.effects:
        atom = effect.literal
        args = [assignment.get(arg, arg) for arg in atom.args]
        if atom.negated:
            atom = NegatedAtom(atom.predicate, args)
        else:
            atom = Atom(atom.predicate, args)
        condition = effect.condition # TODO: assign the parameters in this
        if effect.parameters or not isinstance(effect.condition, conditions.Truth):
            raise NotImplementedError
        effects.append((condition, atom, effect, assignment.copy()))

    arg_list = [assignment.get(par.name, par.name)
                for par in action.parameters[:action.num_external_parameters]]
    name = "(%s %s)" % (action.name, " ".join(arg_list))
    cost = 1

    if not all(param.name in assignment for param in action.parameters):
        params = [TypedObject(name=assignment.get(par.name), type_name=par.type_name) for par in action.parameters]

        arg_list = [assignment.get(par.name, par.name)
                    for par in action.parameters[:action.num_external_parameters]]
        name = "(%s %s)" % (action.name, " ".join(arg_list))
        pre = Conjunction(precondition_parts)
        eff = [Effect([], cond, atom) for (cond, atom, _, _) in effects]
        return Action(name, params, len(params), pre, eff, cost)
    return PropositionalAction(name, Conjunction(precondition_parts), effects, cost, action, assignment)
@profile
def find_applicable_brute_force(action, state, allow_missing, object_stream_map={}, filter_precond=True):
    """Given an action schema and a state, return the list of partially grounded
    operators possible in the given state"""

    # Find all possible assignments of the state.objects to action.parameters
    # such that [action.preconditions - {atom | atom in certified}] <= state
    candidates = {}
    for atom in action.precondition.parts:
        if atom.predicate in allow_missing:
            continue
        for ground_atom in state:
            if ground_atom.predicate != atom.predicate:
                continue
            for arg, candidate in zip(atom.args, ground_atom.args):
                candidates.setdefault(arg, set()).add(candidate)
    if not candidates:
        assert False, "why am i here"
        yield bind_action(action, {})
        return
    # find all the possible versions
    for assignment in combinations(candidates):
        feasible = True
        for par in action.parameters:
            if par.name not in assignment:
                assignment[par.name] = Identifiers.next()

        assert isinstance(action.precondition, Conjunction)
        precondition_parts = []
        for atom in action.precondition.parts:
            args = [assignment.get(arg, arg) for arg in atom.args]
            atom = Atom(atom.predicate, args) if not atom.negated else NegatedAtom(atom.predicate, args)
            precondition_parts.append(atom)
            # grounded positive precondition not in state
            if (not atom.negated and atom not in state):
                if(atom.predicate not in allow_missing):
                    feasible = False
                    break
                if all(arg in object_stream_map for arg in atom.args):
                    feasible = False
                    break


    

            if atom.negated and atom.negate() in state:
                feasible = False
                break
        if not feasible:
            continue
        effects = []
        for effect in action.effects:
            atom = effect.literal
            args = [assignment.get(arg, arg) for arg in atom.args]
            if atom.negated:
                atom = NegatedAtom(atom.predicate, args)
            else:
                atom = Atom(atom.predicate, args)
            condition = effect.condition # TODO: assign the parameters in this
            if effect.parameters or not isinstance(effect.condition, conditions.Truth):
                raise NotImplementedError
            effects.append((condition, atom, effect, assignment.copy()))

        arg_list = [assignment.get(par.name, par.name)
                    for par in action.parameters[:action.num_external_parameters]]
        name = "(%s %s)" % (action.name, " ".join(arg_list))
        cost = 1


        partial_op = PropositionalAction(name, Conjunction(precondition_parts), effects, cost, action, assignment)
    
        
        # are any of the preconditions missing?
        # if any of the preconditions correspond to non-certifiable AAAND they include missing args
        # then there's no way this will work

        if filter_precond:
            missing_positive = {atom for atom in partial_op.precondition.parts if not atom.negated} - set(state)

            # grounded positive precondition not in state
            if any(atom.predicate not in allow_missing or all(arg in object_stream_map for arg in atom.args) for atom in missing_positive):
                assert False
                continue
            
            # negative precondition is positive in state
            negative = {atom for atom in partial_op.precondition.parts if atom.negated} 
            if any(atom.negate() in state for atom in negative):
                assert False
                continue
        yield partial_op



def partially_ground(action, state, fluents):
    """Given an action schema and a state, return the list of partially grounded
    operators keeping any objects in fluent predicates lifted"""

    # Find all possible assignments of the state.objects to action.parameters
    # such that [action.preconditions - {atom | atom in certified}] <= state
    candidates = {}
    for atom in action.precondition.parts:

        for ground_atom in state:
            if ground_atom.predicate != atom.predicate or ground_atom.predicate in fluents:
                continue
            for arg, candidate in zip(atom.args, ground_atom.args):
                candidates.setdefault(arg, set()).add(candidate)
    if not candidates:
        yield bind_action(action, {})
        return
    # find all the possible versions
    for assignment in combinations(candidates):
        partial_op = bind_action(action, assignment)
        yield partial_op

def apply(action, state):
    """Given an action, and a state, compute the successor state"""
    return (state | {eff[1] for eff in action.add_effects}) - {eff[1] for eff in action.del_effects}
    state = set(state)
    for effect in action.effects:
        if effect.parameters or not isinstance(effect.condition, conditions.Truth):
            raise NotImplementedError
        if effect.literal.negated:
            pos_literal = effect.literal.negate()
            if pos_literal in state:
                state.remove(pos_literal)
        else:
            state.add(effect.literal)
    return state

def objects_from_state(state):
    return { arg for atom in state for arg in atom.args }


class SearchState:
    def __init__(self, state, object_stream_map, unsatisfied):
        self.state = state.copy()
        self.object_stream_map = object_stream_map.copy()
        self.unsatisfied = unsatisfied
        self.unsatisfiable = False
        self.children = []
        self.parent = None
        self.action = None

    def __eq__(self, other):
        # compare based on fluent predicates and computation graph in corresponding objects
        return False

    def __repr__(self):
        return str((self.state, self.object_stream_map, self.unsatisfied))

    def get_path(self):
        actions = []
        node = self
        while node is not None:
            actions.insert(0, node.action)
            node = node.parent
        return actions

class ActionStreamSearch:
    def __init__(self, init, goal, externals, actions):
        self.init_objects = {o for o in objects_from_state(init)}
        self.init = SearchState(init, {o:None for o in self.init_objects}, set())
        self.goal = goal
        self.externals = externals
        self.actions = actions
        self.streams_by_predicate = {}
        for stream in externals:
            for fact in stream.certified:
                self.streams_by_predicate.setdefault(fact[0], set()).add(stream)

        self.fluent_predicates = set()
        for action in actions:
            for effect in action.effects:
                self.fluent_predicates.add(effect.literal.predicate)

    def test_equal(self, s1, s2):
        return False
        f1 = sorted(f for f in s1.state if f.predicate in self.fluent_predicates)
        f2 = sorted(f for f in s2.state if f.predicate in self.fluent_predicates)

        # are f1 and f2 the same, down to a substitiution?
        sub = {o:o for o in self.init_objects}
        for a,b in zip(f1, f2):
            if a.predicate != b.predicate:
                return False
            if a == b:
                continue
            for o1, o2 in zip(a.args, b.args):
                if o1 != o2 and not (o1.startswith('?') and o2.startswith('?')):
                    return False
                if sub.setdefault(o1, o2) != o2:
                    return False
        # TODO: are the stream created objects the same down to a substitution of their computation graph
        return True
    
    def test_goal(self, state):
        return self.goal <= state.state 

    def action_successor(self, state, action):
        new_state = apply(action, state.state)
        missing_positive = {atom for atom in action.precondition.parts if not atom.negated} - set(state.state)
        return SearchState(new_state, state.object_stream_map, missing_positive | state.unsatisfied)
    
    def successors(self, state):
        for action in self.actions:
            ops = find_applicable_brute_force(action, state.state | state.unsatisfied, self.streams_by_predicate, state.object_stream_map)
            for op in ops:
                new_world_state = apply(op, state.state)
                missing_positive = {atom for atom in op.precondition.parts if not atom.negated} - set(state.state)
                # this does the partial order plan
                try:
                    partial_plan = certify(state.state, state.object_stream_map, missing_positive | state.unsatisfied, streams_by_predicate)
                    # this extracts the important bits from it (i.e. description of the state)
                    new_world_state, object_stream_map, missing = extract_from_partial_plan(new_world_state, partial_plan)
                    op_state = SearchState(new_world_state, object_stream_map, missing)
                    yield (op, op_state)
                except Unsatisfiable:
                    continue

                
    
class Unsatisfiable(Exception):
    pass
def identify_groups(facts, stream):

    # given a set of facts, and a stream capable of producing facts
    # return a grouping over the facts where each group of facts might
    # can be certified together

    # question: is the solution unique?
    # question: is the "maximal" part very important?

    # question: does my graph consist only of disjoint cliques?
    # if so, then i can use a flood fill algorithm 
    # lets assume thats the case and see if we run into trouble
    preds = set(cert[0] for cert in stream.certified)
    facts = {f for f in facts if f.predicate in preds}
    if not facts:
        return []

    adjacency = {f:set() for f in facts}
    for (a, b) in itertools.combinations(facts, 2):
        assignment = {}
        for certified in stream.certified:
            if certified[0] == b.predicate:
                assignment.update({var:val for (var,val) in zip(certified[1:], b.args)})
            if certified[0] == a.predicate:
                partial = {var:val for (var,val) in zip(certified[1:], a.args)}
                if any(assignment.get(var) != partial.get(var) for var in partial):
                    break
        else:
            if assignment:
                adjacency.setdefault(a, set()).add(b)
                adjacency.setdefault(b, set()).add(a)

    unlabeled = facts.copy()
    labeled = set()
    groups = []
    while unlabeled:
        fact = unlabeled.pop()
        group = adjacency[fact]
        for el in group:
            unlabeled.remove(el)
            # check that it's a disconnected clique
            assert (adjacency[el] | set([el])) == (group | set([fact]))
        group.add(fact)
        
        # double check my assumption that its a disconnected clique
        assert not (labeled & group)
        labeled = labeled | group
        groups.append(group)
    return groups


def get_assignment(group, stream):
    assignment = {}
    for certified in stream.certified:
        for fact in group:
            if certified[0] == fact.predicate:
                partial = {var:val for (var,val) in zip(certified[1:], fact.args)}
                if any(assignment.get(var, partial[var]) != partial[var] for var in partial):
                    return None
                assignment.update(partial)
    return assignment

def instantiate_stream_from_assignment(stream, assignment, new_vars=False):
    assignment = assignment.copy()
    if new_vars:
        for param in stream.inputs + stream.outputs:
            assignment.setdefault(param, Identifiers.next())

    inputs = tuple(assignment.get(arg) for arg in stream.inputs)
    outputs = tuple(assignment.get(arg) for arg in stream.outputs)
    domain = {Atom(dom[0], [assignment.get(arg) for arg in dom[1:]]) for dom in stream.domain}
    certified = {Atom(cert[0], [assignment.get(arg) for arg in cert[1:]]) for cert in stream.certified}

    return (inputs, outputs, domain, certified)
def instantiate(state, group, stream):
    assignment = get_assignment(group, stream)
    if not assignment:
        return None
    inputs = tuple(assignment.get(arg) for arg in stream.inputs)
    outputs = tuple(assignment.get(arg) for arg in stream.outputs)
    if any(x is None for x in inputs + outputs):
        return None
    domain = {Atom(dom[0], [assignment.get(arg) for arg in dom[1:]]) for dom in stream.domain}
    certified = {Atom(cert[0], [assignment.get(arg) for arg in cert[1:]]) for cert in stream.certified}
    missing = domain - state.state
    if any(atom.predicate not in streams_by_predicate for atom in missing):
        # domain facts not satisfiable
        return None

    if any(out in state.object_stream_map for out in outputs):
        # the same object cant be output by different stream instances
        # TODO: need to prune this node completely from the search...
        state.unsatisfiable = True
        return None
    instance = (stream, inputs, outputs)

    new_literals = state.state | certified
    new_unsat = state.unsatisfied - certified | missing
    new_object_stream_map = dict(state.object_stream_map, **{out:instance for out in outputs})
    state = SearchState(new_literals, new_object_stream_map, new_unsat)
    return state

def instantiation_successors(state, streams):
    new_states = []
    for stream in streams:
        for group in identify_groups(state.unsatisfied, stream):
            new_state = instantiate(state, group, stream)
            if new_state:
                new_states.append(new_state)
    return new_states

def identify_stream_groups(facts, streams):
    facts_grouped = set()
    for stream in streams:
        for group in identify_groups(facts, stream):
            assert not (facts_grouped & group)
            yield (stream, group)
            facts_grouped |= group

def instantiate_depth_first(state, streams):
    changed = False
    for (stream, group) in identify_stream_groups(state.unsatisfied, streams):
        new_state = instantiate(state, group, stream)
        if new_state is not None:
            state = new_state
            changed = True
    if changed:
        return instantiate_depth_first(state, streams)
    else:
        return state

def try_bfs(search):
    q = [search.init]
    closed = []
    expand_count = 0
    evaluate_count = 0
    while q:
        state = q.pop(0)
        expand_count += 1
        if search.test_goal(state):
            print(f'Explored {expand_count}. Evaluated {evaluate_count}')
            # print(state.get_path(), end='\n\n')
            # continue
            return state.get_path()
        state.children = []
        for (op, child) in search.successors(state):
            child.action = op
            child.parent = state
            state.children.append((op, child))
            if child.unsatisfiable or any(search.test_equal(child, node) for node in closed):
                continue 
            evaluate_count += 1
            q.append(child)
        closed.append(state)

import itertools

from dataclasses import dataclass, field
# I want to think about partial order planning
@dataclass
class PartialPlan:
    agenda: set
    actions: set
    bindings: dict
    order: list
    links: list
    def copy(self):
        return PartialPlan(self.agenda.copy(), self.actions.copy(), self.bindings.copy(), self.order.copy(), self.links.copy())

id_provider = itertools.count()
@dataclass
class StreamAction:
    stream: Stream = None
    inputs: tuple = field(default_factory=tuple)
    outputs: tuple = field(default_factory=tuple)
    id: int = field(default_factory=lambda: next(id_provider))
    pre: set = field(default_factory=set)
    eff: set = field(default_factory=set)

    def __hash__(self):
        return self.id

@dataclass
class Resolver:
    action: StreamAction = None
    links: list = field(default_factory=[])
    binding: dict = None

def equal_barring_substitution(atom1, atom2, bindings):
    if atom1.predicate != atom2.predicate:
        return None
    sub = bindings.copy()
    for a1, a2 in zip(atom1.args, atom2.args):
        if sub.setdefault(a1, a2) != a2:
            return None
    return sub

def get_resolvers(partial_plan, agenda_item, streams_by_predicate):
    (incomplete_action, missing_precond) = agenda_item
    for action in partial_plan.actions:
        if missing_precond in action.eff:
            assert False, "Didnt expect to get here, considering im doing the same work below"
            yield Resolver(links=[(action, missing_precond, incomplete_action)])
            continue

        # # check bindings
        # for eff in action.eff:
        #     # if equal barring substitution:
        #     sub = equal_barring_substitution(missing_precond, eff, partial_plan.bindings)
        #     if sub:
        #         if missing_precond.predicate in streams_by_predicate:
        #             # print(missing_precond, eff, action.stream)
        #             continue
        #         else:
        #             print(missing_precond, eff, action.stream)

        #         yield Resolver(binding=sub, link=(action, missing_precond, incomplete_action))

    

    for stream in streams_by_predicate.get(missing_precond.predicate, []):
        assignment = get_assignment((missing_precond, ), stream)

        (inputs, outputs, pre, eff) = instantiate_stream_from_assignment(stream, assignment, new_vars=True)

        binding = {o: o for o in outputs}

        action = StreamAction(stream, inputs, outputs, pre=pre, eff=eff)
        # TODO: continue if any of the atoms in eff are already produced by an action in the plan
        # In fact, it may be easier... that we continue if any of outputs are in any achieved facts?
        if any(o in partial_plan.bindings for o in outputs):
            continue
        if any(assignment.get(p) is None for p in stream.inputs):
            action.new_input_variables = True
            links = [(action, missing_precond, incomplete_action)]
        else:
            action.new_input_variables = False

            # could figure out all the links from this action to existing agenda items.
            # assumption: no other future action could certify the facts that this action certifies.

            # is it possible that:
            #  this action resolves a1 and a2
            #  a1 has many resolvers, so we dont do anything
            #  a2 has only one resolver, so we apply it, and resolve a1 along the way
            #  but now, because we resolved a1, we have made an irrevocable choice even though there might have been another way
            
            # I dont think this is a problem because the fact that one action will resolve a1 and a2 means that any other choice
            # for resolving a1 would have failed to resolve a2. Because there's only one resolver of a2. So had we resolved a1 by
            # some other means, then the only resolver of a2 would have to reproduce a1, but that's not allowed.
            links = []
            for (incomplete_action, missing_precond) in partial_plan.agenda:
                if missing_precond in eff:
                    links.append((action, missing_precond, incomplete_action))


            # could figure out all the links from existing actions to the preconditions of this action.
            # assumption: no facts are every provided by more than one existing action.
            for missing_precond in pre:
                for existing_action in partial_plan.actions:
                    if missing_precond in existing_action.eff:
                        links.append((existing_action, missing_precond, action))

        yield Resolver(action, links=links, binding=binding)

def successor(plan, resolver):
    plan = plan.copy()
    if resolver.action:
        plan.actions.add(resolver.action)
        plan.agenda |= {(resolver.action, f) for f in resolver.action.pre} 
    if resolver.links:
        for link in resolver.links:
            plan.links.append(link)
            plan.agenda = plan.agenda - {(link[2], link[1])}

    if resolver.binding:
        plan.bindings.update(resolver.binding)
    
    return plan

def certify(state, object_stream_map, missing, streams_by_predicate):

    init_action = StreamAction(eff=state)
    goal_action = StreamAction(pre=missing)
    p0 = PartialPlan(agenda={(goal_action, sub) for sub in missing}, actions={init_action, goal_action}, bindings={o:o for o in object_stream_map}, order=[], links=[])


    while p0.agenda:
        for agenda_item in p0.agenda:
            resolvers = list(get_resolvers(p0, agenda_item, streams_by_predicate))
            if not resolvers:
                raise Unsatisfiable('Deadend')
            if len(resolvers) > 1:
                # assumes that there is at least one fact that will uniquely identify the stream, doesnt it?
                # e.g if stream A certifies (p ?x ?y) and (q ?y ?z)
                # but stream B certifies (p ?x ?y) and (r ?y ?z)
                # and stream C certifies (q ?x ?y) and (r ?y ?z)
                # if we have two agenda items { (p ?x1 ?y1) and (q ?y1 ?z1)} each of the agenda items
                # will have 2 resolvers, so we wont identify stream A as being the right move
                continue
            [resolver] = resolvers
            if resolver.action and resolver.action.new_input_variables:
                continue
            p0 = successor(p0, resolver)
            break
        else:
            break
    return p0

def extract_from_partial_plan(new_world_state, partial_plan):
    object_stream_map = {o:None for o in partial_plan.bindings}
    for act in partial_plan.actions:
        if act.stream is None:
            continue
        new_world_state |= act.eff
        for out in act.outputs:
            object_stream_map[out] = act
    missing = {m for (stream_action, m) in partial_plan.agenda}
    return new_world_state, object_stream_map, missing    
#%%
if __name__ == '__main__':
    from experiments.blocks_world.run import *
    from pddlstream.algorithms.algorithm import parse_problem
    url = 'tcp://127.0.0.1:6000'
    problem_file = 'experiments/blocks_world/data_generation/random/test/3_1_1_18.yaml'

    (
        sim,
        station_dict,
        traj_directors,
        meshcat_vis,
        prob_info,
    ) = make_and_init_simulation(url, problem_file)
    problem, model_poses = construct_problem_from_sim(sim, station_dict, prob_info)
    evaluations, goal_exp, domain, externals = parse_problem(problem)

    # print("Initial:", str_from_object(problem.init))
    # print("Goal:", str_from_object(problem.goal))

    init = set()
    for evaluation in evaluations:
        x = fact_from_evaluation(evaluation)
        init.add(Atom(x[0], [o.pddl for o in x[1:]]))

    goal = set()
    assert goal_exp[0] == 'and'
    for x in goal_exp[1:]:
        goal.add(Atom(x[0], [o.pddl for o in x[1:]]))

    print('Initial:', init)
    print('\n\nGoal:', goal)
    [pick, move, place, stack, unstack] = domain.actions

    # todo: move this somewhere like a class member
    streams_by_predicate = {}
    for stream in externals:
        for fact in stream.certified:
            streams_by_predicate.setdefault(fact[0], set()).add(stream)


    search = ActionStreamSearch(init, goal, externals, domain.actions)
    # import cProfile, pstats, io
    # pr = cProfile.Profile()
    # pr.enable() 
    actions = try_bfs(search)
    # pr.disable()
    # s = io.StringIO()
    # ps = pstats.Stats(pr, stream=s)
    # ps.print_stats()
    # ps.dump_stats('lifted-bfs.profile')   
    print(actions)
#     # for op, _ in search.successors(search.init):
#     #     print(op)

#     # state = search.init
#     # stream = externals[0]
#     # groups = identify_groups(state.unsatisfied, stream)
#     # print(groups)

#     # s1 = search.action_successor(search.init, moves[0])
#     # state = instantiate_depth_first(s1, externals)

#     # [action]  = list(find_applicable_brute_force(pick, state.state, streams_by_predicate))
#     # s2 = search.action_successor(state, action)
#     # state = instantiate_depth_first(s2, externals)
#     # print(state)

# # %%
#     # I want to think about regression planning.
#     subgoal = search.init.children[2][1].unsatisfied
#     # which "actions" are relevant??
#     for stream in externals:
#         if any(c[0] == fact.predicate for c in stream.certified for fact in subgoal):
#             substitution = get_assignment(subgoal, stream)
#             # if the inputs are not fully specified, we'd have to create
#             # new objects

#             # at least one of the objects is guaranteed to be specified
#             # it doesnt matter if all are not specified
            

# # %%

            
#     # init_action = PropositionalAction('init', conditions.Truth(),  [(conditions.Truth(), atom, None, None) for atom in search.init.state], 0, None, None)
#     # goal_action = PropositionalAction('goal', Conjunction(search.goal), [], 0, None, None)
#     init_action = StreamAction(eff=search.init.state)
#     goal_action = StreamAction(pre={sub for sub in subgoal})
#     p0 = PartialPlan(agenda={(goal_action, sub) for sub in subgoal}, actions={init_action, goal_action}, bindings={o:o for o in objects_from_state(search.init.state)}, order=[], links=[])


#     while p0.agenda:
#         for agenda_item in p0.agenda:
#             resolvers = list(get_resolvers(p0, agenda_item))
#             if not resolvers:
#                 raise RuntimeError('Deadend')
#             if len(resolvers) > 1:
#                 continue
#             [resolver] = resolvers
#             p0 = successor(p0, resolver)
#             break
#         else:
#             break

#     print(p0.bindings)
    
