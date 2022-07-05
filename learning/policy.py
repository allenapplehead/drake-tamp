
# %%
from glob import glob
import itertools
import json
import pickle
import os
import yaml
from copy import deepcopy
import numpy as np
from torch_geometric.data import Data
import re
import torch
from torch_geometric.nn import GATv2Conv
from torch_geometric.data import Batch
from torch_geometric.loader import DataLoader
import multiprocessing as mp

import getpass
USER = getpass.getuser()

import sys
sys.path.insert(
    0,
    f"/home/{USER}/drake-tamp/pddlstream/FastDownward/builds/release64/bin/translate/",
)

def parse_action(action, pddl_to_name):
    def to_name(op, arg1, arg2):
        return f"{op}(panda,{pddl_to_name[arg1]},{pddl_to_name[arg2]})"

    name, args = action
    if name == 'move':
        return None
    elif name == 'pick':
        block, region = args[1:3]
        return to_name(name, block, region)
    elif name == 'place':
        block, region = args[1:3]
        return to_name(name, block, region)
    elif name == 'stack':
        block, lowerblock = args[1], args[4]
        return to_name(name, block, lowerblock)
    elif name == 'unstack':
        block, lowerblock = args[1], args[4]
        return to_name(name, block, lowerblock)
    raise ValueError(f"Action {name} not recognized")

class State:
    blocks = None
    regions = None

    def copy(self):
        state = State()
        state.blocks = deepcopy(self.blocks)
        state.regions = deepcopy(self.regions)
        return state

    @classmethod
    def from_scene(cls, problem_file):
        SURFACES = {
            'blue_table': {
                'x': [-0.6,  0. ,  0. ],
                'size': [0.4 , 0.75, 0.  ]
            },
            'green_table': {
                'x': [0. , 0.6, 0. ],
                'size': [0.4 , 0.75, 0.  ]
            },
            'purple_table': {
                'x': [ 0. , -0.6,  0. ],
                'size': [0.4 , 0.75, 0.  ]
            },
            'red_table': {
                'x': [0.6, 0. , 0. ],
                'size': [0.4 , 0.75, 0.  ]
            }
        }

        state = State()            
        state.blocks = {}
        state.regions = {}

        for surface in problem_file['surfaces']:
            state.regions[surface] = {
                "x": np.array(SURFACES[surface]['x']),
                "size": np.array(SURFACES[surface]['size']),
            }

        assert all(predicate in ['on-block', 'on-table'] for predicate, _, _ in problem_file['goal'][1:])
        region_goals = {block: region[0] for predicate, block, region in problem_file['goal'][1:] if predicate == 'on-table'}
        block_goals = {block: lowerblock for predicate, block, lowerblock in problem_file['goal'][1:] if predicate == 'on-block'}
        reverse_block_goal = {lb:b for b,lb in block_goals.items()}
    
        for block in problem_file['objects']:
            
            state.blocks[block] = {
                "x": np.array(problem_file['objects'][block]['X_WO'][:3]),
                "x_uncertainty": np.array([0, 0, 0]),
                "held": False,
                "region": problem_file['objects'][block].get('on-table', (None, None))[0],
                "is_below": None,
                "is_above": None,
                "size": np.array([.5, .5, 1. if 'blocker' in block else .5]),
                "goal_region": region_goals.get(block),
                "goal_above": block_goals.get(block),
                "goal_below": reverse_block_goal.get(block)
            }
        for block in problem_file['objects']:
            lowerblock = problem_file['objects'][block].get('on-block')
            if lowerblock is not None:
                state.blocks[lowerblock]['is_below'] = block
                state.blocks[block]['is_above'] = lowerblock
        return state

    def transition(self, action):
        action = parse_action_name(action)
        operator = action[0]
        blocks = self.blocks.copy()
        regions = self.regions
        if operator == 'pick':
            operator, block, region = action
            blocks[block] = blocks[block].copy()
            blocks[block]['held'] = True
            blocks[block]['region'] = None
    
        elif operator == 'place':
            operator, block, region = action
            blocks[block] = blocks[block].copy()
            blocks[block]['held'] = False
            blocks[block]['region'] = region

            blocks[block]['x'] = regions[region]['x']
            blocks[block]['x_uncertainty'] = regions[region]['size'] / 2

        elif operator == 'unstack':
            operator, block, lowerblock = action
            blocks[block] = blocks[block].copy()
            blocks[lowerblock] = blocks[lowerblock].copy()

            blocks[block]['held'] = True
            blocks[lowerblock]['is_below'] = None
            blocks[block]['is_above'] = None
    
        elif operator == 'stack':
            operator, block, lowerblock = action
            blocks[block] = blocks[block].copy()
            blocks[lowerblock] = blocks[lowerblock].copy()

            blocks[block]['is_above'] = lowerblock
            blocks[lowerblock]['is_below'] = block
            blocks[block]['held'] = False

            blocks[block]['x'] = blocks[lowerblock]['x'] + blocks[lowerblock]['size'] * np.array([0,0,1])
            blocks[block]['x_uncertainty'] = blocks[lowerblock]['x_uncertainty']
        else:
            raise ValueError(operator)
        new_state = State()
        new_state.blocks = blocks
        new_state.regions = regions
        return new_state

    @property
    def held_block(self):
        for block in self.blocks:
            if self.blocks[block].get("held"):
                return block
        return None

    def operators(self):
        gripper = 'panda'
        held_block = self.held_block
        if held_block is None:
            for block in self.blocks:
                if self.blocks[block]['is_below'] is not None:
                    continue
                if self.blocks[block]['region'] in self.regions:
                    region = self.blocks[block]['region']
                    yield f"pick({gripper},{block},{region})"
                else:
                    lowerblock = self.blocks[block]['is_above']
                    yield f"unstack({gripper},{block},{lowerblock})"


        else:
            for region in self.regions:
                yield f"place({gripper},{held_block},{region})"
            for block in self.blocks:
                if block == held_block:
                    continue
                if self.blocks[block]["is_below"] is None:
                    yield f"stack({gripper},{held_block},{block})"
            
    def __hash__(self):
        return hash(
            tuple((
                b,
                s["held"],
                tuple(s['x']),
                tuple(s.get('x_uncertainty', 0)),
                s['region'],
                s['is_above'],
                s['is_below']
            ) for b,s in sorted(list(self.blocks.items()))))

def _get_model_input_nodes(state):
    node_feature_to_index = {
        "x": 0,
        "x_uncertainty": 3,
        "size": 6,
        "held": 9,
        "region": 10,
        "goal_region": 14
    }
    edge_feature_to_index = {
        "is_above": 0,
        "is_below": 1,
        "goal_above": 2,
        "goal_below": 3,
        "transform": 4
    }
    regions = state.regions
    blocks = state.blocks
    nodes = sorted(list(blocks))
    node_to_block = dict(enumerate(nodes))
    node_features = torch.zeros((len(blocks), 18))
    region_to_index = {r:i for i, r in enumerate(regions)}

    for node in node_to_block:
        block = node_to_block[node]
        block_info = blocks[block]
        node_features[node, node_feature_to_index["x"]: node_feature_to_index["x"] + 3] = torch.tensor(block_info["x"], dtype=torch.float)
        node_features[node, node_feature_to_index["x_uncertainty"]:node_feature_to_index["x_uncertainty"] + 3] = torch.tensor(block_info["x_uncertainty"], dtype=torch.float)
        node_features[node, node_feature_to_index["held"]:node_feature_to_index["held"] + 1] = torch.tensor(block_info["held"], dtype=torch.float)
        node_features[node, node_feature_to_index["size"]:node_feature_to_index["size"] + 3] = torch.tensor(block_info["size"], dtype=torch.float)
        if block_info["region"]:
            node_features[node, node_feature_to_index["region"] + region_to_index[block_info["region"]]] = 1
        if block_info["goal_region"]:
            node_features[node, node_feature_to_index["goal_region"] + region_to_index[block_info["goal_region"]]] = 1

    edges = []
    edge_attributes = torch.zeros((len(blocks)**2 - len(blocks), 7))
    for node in node_to_block:
        for othernode in node_to_block:
            if node == othernode:
                continue
            block = node_to_block[node]
            otherblock = node_to_block[othernode]
            block_info = blocks[block]
            edge_attributes[len(edges), edge_feature_to_index["is_above"]] = 1 if otherblock == block_info["is_above"] else 0
            edge_attributes[len(edges), edge_feature_to_index["is_below"]] = 1 if otherblock == block_info["is_below"] else 0
            edge_attributes[len(edges), edge_feature_to_index["goal_above"]] = 1 if otherblock == block_info["goal_above"] else 0
            edge_attributes[len(edges), edge_feature_to_index["goal_below"]] = 1 if otherblock == block_info["goal_below"] else 0
            edge_attributes[len(edges), edge_feature_to_index["transform"]:edge_feature_to_index["transform"] + 3] = torch.tensor(blocks[otherblock]["x"] - block_info["x"])
            edges.append((node, othernode))

    return nodes, node_features, edges, edge_attributes

def parse_action_name(action_name):
    result = re.search(r'(pick|place|stack|unstack)\(([^,]+),([^,]+),(.+)\)($| )', action_name)
    action, _, block, region = result.group(1), result.group(2), result.group(3), result.group(4)
    try:
        region = eval(region)
    except Exception:
        pass
    if isinstance(region, tuple):
        region = region[0]
    return (action, block, region)

def _get_model_input_actions(nodes, action_name):
    operator, block, region = parse_action_name(action_name)
    action_vocab = {
        "pick.green_table": 0,
        "pick.red_table": 1,
        "pick.blue_table": 2,
        "pick.purple_table": 3,
        "place.green_table": 4,
        "place.red_table": 5,
        "place.blue_table": 6,
        "place.purple_table": 7,
        "stack": 8,
        "unstack": 9
    }
    operator_rep = torch.zeros((1, len(action_vocab)))
    operator_rep[0, action_vocab[f"{operator}.{region}" if operator in ["pick", "place"] else operator]] = 1
    target_block_1 = nodes.index(block)
    target_block_2 = -1 if operator in ["pick", "place"] else nodes.index(region)

    return operator_rep, target_block_1, target_block_2

def get_model_input(state, action_name):
    nodes, node_features, edges, edge_attributes = _get_model_input_nodes(state)
    operator_rep, target_block_1, target_block_2 = _get_model_input_actions(nodes, action_name)

    return nodes, node_features, edges, edge_attributes, operator_rep, target_block_1, target_block_2


def MLP(layers, input_dim):
    mlp_layers = [torch.nn.Linear(input_dim, layers[0])]


    for layer_num in range(0, len(layers) - 1):
        mlp_layers.append(torch.nn.ReLU())
        mlp_layers.append(torch.nn.Linear(layers[layer_num],
                                      layers[layer_num + 1]))
    # if len(layers) > 1:
    #     mlp_layers.append(torch.nn.LayerNorm(
    #         mlp_layers[-1].weight.size()[:-1]))  # type: ignore

    return torch.nn.Sequential(*mlp_layers)


class AttentionPolicy(torch.nn.Module):
    def __init__(self, node_dim, edge_dim, action_dim, N=30):
        super().__init__()
        node_encoder_dim = 32
        self.node_encoder = MLP([16, node_encoder_dim], node_dim)

        self.encoder = GATv2Conv(in_channels=node_encoder_dim,
                 out_channels=int(node_encoder_dim / 2), heads = 2, edge_dim=edge_dim)

        num_heads = 4
        action_embed_dim = 32
        self.action_encoder = MLP([16, action_embed_dim], action_dim + node_encoder_dim * 2 + node_dim*2)
        self.att = GATv2Conv(in_channels=node_encoder_dim,
                 out_channels=int(node_encoder_dim / 2), heads = 2)
        self.output = MLP([16, 1], action_embed_dim)

    def forward(self, B):
        node_enc = self.node_encoder(B.x)
        node_enc = self.encoder(node_enc, edge_index=B.edge_index, edge_attr=B.edge_attr)

        target_1_enc = node_enc[B.t1_index]
        target_2_enc = node_enc[B.t2_index]
        target_2_enc[B.t2_index == -1] = 0

        target_1_enc_res = B.x[B.t1_index]
        target_2_enc_res = B.x[B.t2_index]
        target_2_enc_res[B.t2_index == -1] = 0

        action_enc = self.action_encoder(torch.cat([B.ops, target_1_enc, target_1_enc_res, target_2_enc, target_2_enc_res], dim=1))
        starting_points = []
        end_points = []
        num_copies_sum = 0
        for index, num_copies in enumerate(B.num_ops):
            num_nodes = B.node_count[index]
            starting_points.append((torch.arange(num_nodes) + B.ptr[index]).repeat(num_copies))
            end_points.append(torch.arange(num_copies).repeat_interleave(num_nodes) + num_copies_sum + B.num_nodes)
            num_copies_sum += num_copies

        attention_edges = torch.cat(
            [
                torch.cat(starting_points, 0).unsqueeze(1),
                torch.cat(end_points, 0).unsqueeze(1)
            ],
            dim=1
        ).t().contiguous()

        attended_actions = self.att(torch.vstack([node_enc, action_enc]), attention_edges)[B.num_nodes:]
        logits = self.output(attended_actions)
        
        return logits

def group(logits, num_ops):
    groups = []
    s = 0
    for i, n in enumerate(num_ops):
        groups.append(logits[s: s + n].squeeze(1).unsqueeze(0))
        s += n
    return groups

def get_data(state):
    nodes, node_features, edges, edge_attributes = _get_model_input_nodes(state)
    edge_index = torch.tensor(edges, dtype=torch.long).t().contiguous()
    ops = []
    t1, t2 = [], []
    action_names = []
    for action_name in state.operators():
        operator_rep, target_block_1, target_block_2 = _get_model_input_actions(nodes, action_name)
        ops.append(operator_rep)
        t1.append(target_block_1)
        t2.append(target_block_2)
        action_names.append(action_name)
    ops = torch.vstack(ops)
    t1 = torch.tensor(t1, dtype=torch.long)
    t2 = torch.tensor(t2, dtype=torch.long)
    return Data(
        nodes=nodes,
        x=node_features,
        edge_index=edge_index,
        edge_attr=edge_attributes,
        ops=ops,
        t1_index=t1,
        t2_index=t2,
        num_ops=len(ops),
        node_count=len(nodes),
        action_names=action_names
    )
def make_dataset(data_files):
    states = []
    all_data = []
    for f in data_files:
        f = f.replace('/jobs/', f"/home/{USER}/drake-tamp/data/jobs/")
        with open(f, 'rb') as fb:
            f_data = pickle.load(fb)
        with open(os.path.join(os.path.dirname(f), 'stats.json'), 'r') as fb:
            stats_data = json.load(fb)
        with open(f"/home/{USER}/drake-tamp/" + stats_data['problem_file_path'].split('drake-tamp/')[1], 'r') as fb:
            problem_file = yaml.full_load(fb)

        if not stats_data['solution']:
            continue
        if 'non_monotonic' in stats_data['problem_file_path']:
            continue
        # for plan in stats_data['action_plans'][-1:]:
        for plan in [stats_data['solution']]:
            # plan = stats_data['solution']
            problem_info = f_data['problem_info']
            state = State.from_scene(problem_file)
            pddl_to_name = problem_info.object_mapping
            demonstration = [parse_action(a, pddl_to_name) for a in plan]

            for action in filter(bool, demonstration):
                states.append(state)
                data = get_data(state)
                operator, arg1, arg2 = parse_action_name(action)
                action = f'{operator}(panda,{arg1},{arg2})'
                y = [action in action_name for action_name in data.action_names]
                assert any(y)
                data.y = torch.tensor(y, dtype=torch.long)
                all_data.append(data)
                state = state.transition(action)
    return all_data, states

#%%
def is_goal(state):
    return all((
        (state.blocks[block]['goal_region'] is None or state.blocks[block]['region'] == state.blocks[block]['goal_region']) and \
        (state.blocks[block]['goal_above'] is None or state.blocks[block]['is_above'] == state.blocks[block]['goal_above']) and \
        (state.blocks[block]['goal_below'] is None or state.blocks[block]['is_below'] == state.blocks[block]['goal_below'])) for block in state.blocks)

import heapq
def levinTs(state, model):
    counter = 0
    queue = [(1, 0, state, 1, [])]
    expansions = 0
    closed = set()
    while queue:
        expansions += 1
        _, _, state, p, history = heapq.heappop(queue)
        if expansions % 100 == 0:
            print(expansions, len(closed), len(history), p)
        
        if is_goal(state):
            print(history)
            print(f'Found goal in {expansions} expansions')
            yield history
            continue
        if hash(state) in closed:
            continue

        for p_a, action in invoke(state, model):
            child = state.transition(action)
            heapq.heappush(queue, ((1*len(history) + 1)/(p_a*p), counter, child, p_a*p, history + [action]))
            counter += 1

        closed.add(hash(state))
def invoke(state, model, temperature=1):
    with torch.no_grad():
        data = get_data(state)
        p_A = torch.nn.functional.softmax(model(Batch.from_data_list([data])).squeeze(1)/temperature).detach().numpy()
    return list(zip(p_A, data.action_names))

def load_model(path='policy.pt'):
    model = AttentionPolicy(18,7,10)
    model.load_state_dict(torch.load(path))
    return model

from experiments.blocks_world.run_lifted import create_problem
from lifted.a_star import ActionStreamSearch, repeated_a_star
from lifted.utils import PriorityQueue

ENABLE_EQUALITY_CHECK = True
import time
def try_a_star(search, cost, heuristic, result, max_steps=10001, max_time=None, policy_ts=None, **kwargs):
    start_time = time.time()
    q = PriorityQueue([search.init])
    # assert hasattr(search)
    closed = set()
    expand_count = 0
    evaluate_count = 0
    found = False


    while q and expand_count < max_steps and (time.time() - start_time) < max_time:
        state = q.pop()
        expand_count += 1
        if state in closed:
            continue

        if search.test_goal(state):
            found = True
            break

        for op, child in search.successors(state):
            # state.children.append((op, child))
            # if (
            #     child.unsatisfiable
            # ):  # or any([search.test_equal(child, node) for node in closed]):
            #     continue

            is_unique = True
            if ENABLE_EQUALITY_CHECK:
                # child_hash = search.get_id(child)
                # child.id = child_hash
                if child in closed:
                    is_unique = False
            # else:
            #     child.id = expand_count

            if not is_unique:
                continue

            child.parents = {(op, state)}
            child.ancestors = state.ancestors | {state}
            state.children.add((op, child))
            evaluate_count += 1
        for op, child in sorted(state.children, key=lambda x: x[0].name):
            if policy_ts is not None:
                q.push(child, policy_ts(state, op, child))
            else:
                child.start_distance = state.start_distance + cost(state, op, child)
                child.cost_to_go = heuristic(child, search.goal)
                q.push(child, child.start_distance + heuristic(child, search.goal))
        # if hasattr(state, 'particles'):
        closed.add(state)

    # av_branching_f = evaluate_count / expand_count
    # approx_depth = math.log(1e-6 + evaluate_count) / math.log(1e-6 + av_branching_f)
    print(f"Explored {expand_count}. Evaluated {evaluate_count}")
    # print(f"Av. Branching Factor {av_branching_f:.2f}. Approx Depth {approx_depth:.2f}")
    # print(f"Time taken: {(time.time() - start_time)} seconds")
    # print(f"Solution cost: {state.start_distance}")
    result.expand_count += expand_count
    result.evaluate_count += evaluate_count
    if found:
        return state
    else:
        return None


def try_a_star_tree(search, cost, heuristic, result, max_steps=10001, max_time=None, policy_ts=None, closed_exclusion=None, **kwargs):
    start_time = time.time()
    q = PriorityQueue([search.init])
    closed = {search.init}
    closed_exclusion = closed_exclusion if closed_exclusion is not None else set()

    expand_count = 0
    evaluate_count = 0
    found = False

    while q and expand_count < max_steps and (time.time() - start_time) < max_time:
        state = q.pop()
        expand_count += 1

        if search.test_goal(state):
            found = True
            break

        for op, child in search.successors(state):
            evaluate_count += 1
            child.parents = {(op, state)}
            child.ancestors = state.ancestors | {state}
            child.start_distance = state.start_distance + cost(state, op, child)
            state.children.add((op, child))

            if child in closed_exclusion or child not in closed or child in state.ancestors:
                q.push(child, child.start_distance + heuristic(child, search.goal))
                if policy_ts is not None:
                    q.push(child, policy_ts(state, op, child))
                else:
                    child.start_distance = state.start_distance + cost(state, op, child)
                    child.cost_to_go = heuristic(child, search.goal)
                    q.push(child, child.start_distance + heuristic(child, search.goal))
                
                if child in closed_exclusion:
                    closed_exclusion.remove(child)
                else:
                    closed.add(child)

    time_taken = time.time() - start_time

    # av_branching_f = evaluate_count / expand_count
    # approx_depth = math.log(1e-6 + evaluate_count) / math.log(1e-6 + av_branching_f)
    print(f"Explored {expand_count}. Evaluated {evaluate_count}")
    # print(f"Av. Branching Factor {av_branching_f:.2f}. Approx Depth {approx_depth:.2f}")
    # print(f"Time taken: {time_taken} seconds")
    # print(f"Solution cost: {state.start_distance}")

    result.expand_count += expand_count
    result.evaluate_count += evaluate_count
    if found:
        return state
    else:
        return None

def try_mqs(search, heuristic, result, max_steps=10000, max_time=None, policy_ts=None,cost=None, **kwargs):
    start_time = time.time()
    q_p = PriorityQueue([search.init])
    q_h = PriorityQueue([search.init])
    # assert hasattr(search)
    closed = set()
    expand_count = 0
    evaluate_count = 0
    found = False


    while (q_h or q_p) and expand_count < max_steps and (time.time() - start_time) < max_time:
        if expand_count % 2 == 0:
            q = q_h
        else:
            q = q_p
        state = q.pop()
        expand_count += 1
        if state in closed:
            continue

        if search.test_goal(state):
            found = True
            break

        for op, child in search.successors(state):
            # state.children.append((op, child))

            # AS: Why is this required?
            # if (
            #     child.unsatisfiable
            # ):  # or any([search.test_equal(child, node) for node in closed]):
            #     continue

            is_unique = True
            if ENABLE_EQUALITY_CHECK:
                # child_hash = search.get_id(child)
                # child.id = child_hash
                if child in closed:
                    is_unique = False
            # else:
            #     child.id = expand_count

            if not is_unique:
                continue

            child.parents = {(op, state)}
            child.ancestors = state.ancestors | {state}
            child.start_distance = state.start_distance + cost(state, op, child)
            state.children.add((op, child))
            evaluate_count += 1
        for op, child in sorted(state.children, key=lambda x: x[0].name):
            q_p.push(child, policy_ts(state, op, child))
            q_h.push(child, cost(state, op, child) + heuristic(child, search.goal))
        closed.add(state)

    # av_branching_f = evaluate_count / expand_count
    # approx_depth = math.log(1e-6 + evaluate_count) / math.log(1e-6 + av_branching_f)
    print(f"Explored {expand_count}. Evaluated {evaluate_count}")
    # print(f"Av. Branching Factor {av_branching_f:.2f}. Approx Depth {approx_depth:.2f}")
    # print(f"Time taken: {(time.time() - start_time)} seconds")
    # print(f"Solution cost: {state.start_distance}")
    result.expand_count += expand_count
    result.evaluate_count += evaluate_count
    if found:
        return state
    else:
        return None
#%%
class Policy:
    def __init__(self, problem_file, search, model):
        self.initial_state = State.from_scene(problem_file)
        self.planning_states = {
            search.init: self.initial_state
        }
        self.cache = {}
        self.model = model

    def __call__(self, state, op, child):
        model_state = self.planning_states[state]
        opname = op.name.split(' ')[0]#[1:]
        key = (model_state, opname)
        if key not in self.cache:
            for p, op_name in invoke(model_state, self.model):
                op_key = (model_state, op_name)
                self.cache[op_key] = p
        self.planning_states[child] = model_state.transition(opname)
        return self.cache[key]

# %%
from functools import partial
import math
class Result:
    solution = None
    stats = None
    skeletons = None
    action_skeleton = None
    object_mapping = None
    def __init__(self, stats):
        self.stats = stats
        self.skeletons = []
        self.start_time = time.time()
        self.expand_count = 0
        self.evaluate_count = 0
    def end(self, goal_state, object_mapping):
        if object_mapping is not None:
            action_skeleton = [a for _, a, _ in goal_state.get_shortest_path_to_start()]
            self.action_skeleton = action_skeleton
            self.object_mapping = object_mapping
            self.solution = goal_state
        self.planning_time = time.time() - self.start_time

def default_action_cost(state, op, child, stats={}, verbose=False):
    s = stats.get(op, None)
    if s is None:
        return 1
    return (
        (s["num_successes"] + 1) / (s["num_attempts"] + 1)
    ) ** -1

# %%
from lifted.sampling import ancestral_sampling_by_edge_seq, extract_stream_plan_from_path
def repeated_a_star(search, max_steps=1000, stats={}, heuristic=None, cost=None, debug=False, edge_eval_steps=30, max_time=None, policy_ts=None, search_func=try_a_star):
    def lprint(*args):
        if debug:
            print(*args)
    result = Result(stats)

    if heuristic is None:
        heuristic = lambda s,g: len(g - s.state)
    
    if cost is None:
        cost = default_action_cost
    cost = partial(cost, stats=stats)

    closed_exclusion = set()

    max_time = max_time if max_time is not None else math.inf
    for _ in range(max_steps):
        goal_state = search_func(search, cost=cost, policy_ts=policy_ts, max_time=max_time, heuristic=heuristic, result=result, closed_exclusion=closed_exclusion)
        if goal_state is None:
            lprint("Could not find feasable action plan!")
            object_mapping = None
            break

        closed_exclusion |= goal_state.ancestors | {goal_state}

        lprint("Getting path ...")
        path = goal_state.get_shortest_path_to_start()
        c = 0
        for idx, i in enumerate(path):
            lprint(idx, i[1])
            a = cost(*i, verbose=True)
            lprint("action cost:", a)
            lprint("cum cost:", a + c)
            c += a

        action_skeleton = [a for _, a, _ in goal_state.get_shortest_path_to_start()]
        actions_str = "\n".join([str(a) for a in action_skeleton])
        lprint(f"Action Skeleton:\n{actions_str}")

        stream_plan = extract_stream_plan_from_path(path)
        stream_plan = [(edge, list({action for action in object_map.values()})) for (edge, object_map) in stream_plan]
        object_mapping = ancestral_sampling_by_edge_seq(stream_plan, goal_state, stats, max_steps=edge_eval_steps)

        path = goal_state.get_shortest_path_to_start()
        result.skeletons.append(path)
        c = 0
        for idx, i in enumerate(path):
            lprint(idx, i[1])
            a = cost(*i, verbose=True)
            lprint("action cost:", a)
            lprint("cum cost:", a + c)
            c += a


        if object_mapping is not None:
            break
        lprint("Could not find object_mapping, retrying with updated costs")

    result.end(goal_state, object_mapping)
    return result


# %%
def stream_cost(state, op, child, verbose=False,given_stats=dict()):
    if op in given_stats:
        return given_stats[op]
    c = 1
    res = {"num_successes": 0,"num_attempts": 0}
    included = set()
    for stream_action in {v for v in child.object_stream_map.values() if v is not None}:
        obj = stream_action.outputs[0]
        cg_key = child.id_anon_cg_map[obj]
        if cg_key in given_stats:
            included.add(cg_key)
            s = given_stats[cg_key]
            comp_cost = (
                (s["num_successes"] + 1) / (s["num_attempts"] + 1)
            )
            if comp_cost < c:
                c = comp_cost
                res = s
    for i, j in itertools.combinations(included, 2):
        cg_key = frozenset([i, j])
        if cg_key in given_stats.get("pairs", {}):
            s = given_stats["pairs"][cg_key]
            comp_cost = (
                (s["num_successes"] + 1) / (s["num_attempts"] + 1)
            )
            if comp_cost < c:
                c = comp_cost
                res = s
    return res
#%%
class FeedbackPolicy:
    def __init__(self, base_policy, cost_fn, stats, initial_state):
        self._stats = stats
        self._policy = base_policy
        self._cost_fn = cost_fn
        self.store = {initial_state: 1}
        self.counts = {initial_state: 0}
    
    def __call__(self, state, op, child):
        s = 0
        m_attempts = lambda a: 10**(1 + a // 10)
        for a, c in state.children:
            if self._cost_fn is not None:
                fa = self._cost_fn(state, a, c, given_stats=self._stats)
            else:
                fa = self._stats.get(a, {"num_successes": 0,"num_attempts": 0})
                
            
            fa = (1 + fa["num_successes"] * m_attempts(fa["num_attempts"])) / (1 +  fa["num_attempts"]*m_attempts(fa["num_attempts"]))

            pa = self._policy(state, a, c)
            s += fa * pa
        
        
        if self._cost_fn is not None:
            fa = self._cost_fn(state, op, child, given_stats=self._stats)
        else:
            fa = self._stats.get(op, {"num_successes": 0,"num_attempts": 0})
        fa = (1 + fa["num_successes"]*m_attempts(fa["num_attempts"])) / (1 +  fa["num_attempts"]*m_attempts(fa["num_attempts"]))
        pa = self._policy(state, op, child) 
        p_adj = (fa * pa) / s

        self.store[child] = p_adj * self.store[state]
        self.counts[child] = 1 + self.counts[state]
        return (1) / self.store[child]

#%%
if __name__ == '__main__':
    model_path = f"/home/{USER}/drake-tamp/policy.pt"

    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--output_path", "-o", type=str, default=None)
    parser.add_argument("--problem_type", "-p", type=str, default="random", choices=["random", "distractor", "clutter", "sorting", "stacking"])
    parser.add_argument("--search_type", "-s", type=str, default="a_star", choices=["a_star", "tree", "mqs"])
    parser.add_argument("--disable_policy", action="store_true")
    args = parser.parse_args()

    output_path = args.output_path if args.output_path is not None else f"policy_{args.problem_type}_{args.search_type}_{time.time()}_output.pkl"
    search_funcs = {"a_star": try_a_star, "tree": try_a_star_tree, "mqs": try_mqs}
    search_func = search_funcs[args.search_type]

    if (not args.disable_policy) and (not os.path.exists(model_path)):
        print('Trainin model')
        with open(f"/home/{USER}/drake-tamp/data/jobs/blocksworld-dset.json", 'r') as f:
            data_files = json.load(f)["train"]
        np.random.shuffle(data_files)
        all_data, _ = make_dataset(data_files[:])
        valid_data, _ = make_dataset(data_files[-30:])

        valid_loader = DataLoader(valid_data, shuffle=False, batch_size=128)
        train_loader = DataLoader(all_data, shuffle=True, batch_size=32)
        model = AttentionPolicy(18,7,10)
        optimizer = torch.optim.Adam(model.parameters(), lr=0.0001)

        criterion = torch.nn.CrossEntropyLoss(reduction='none')
        def loss(logits, targets, num_ops):
            count = 0
            total = 0
            s = 0
            for i, n in enumerate(num_ops):
                total += criterion(logits[s: s + n].squeeze(1).unsqueeze(0), targets[s:s + n].nonzero().squeeze(0))
                s += n
                count += 1
            return total/count

        for i in range(350):
            model.train()
            train_loss = 0
            for batch in train_loader:
                pred = model(batch)
                l = loss(pred, batch.y, batch.num_ops)
                train_loss += l.item()
                l.backward()
                optimizer.step()
                optimizer.zero_grad()
            train_loss = train_loss / len(train_loader)
            val_loss = 0
            model.eval()
            for batch in valid_loader:
                pred = model(batch)
                l = loss(pred, batch.y, batch.num_ops)
                val_loss += l.item()
            val_loss = val_loss / len(valid_loader)
            print(i, train_loss, val_loss)

        torch.save(model.state_dict(), model_path)
    elif (not args.disable_policy) and os.path.exists(model_path):
        model = load_model(model_path)
        model.eval()
    else:
        model = None

#%%
    
#%%
    # import sys 
    # # # problem_file_path = f"/home/{USER}/drake-tamp/experiments/blocks_world/data_generation/distractors/test/2_10_1_57.yaml"
    # problem_file_path = f"/home/{USER}/drake-tamp/experiments/blocks_world/data_generation/clutter/test/10_0_1_100.yaml"
    # with open(problem_file_path, 'r') as f:
    #     problem_file = yaml.full_load(f)
    # init, goal, externals, actions = create_problem(problem_file_path)

    # search = ActionStreamSearch(init, goal, externals, actions)

    # stats = {}
    # base_policy = Policy(problem_file, search, model)
    # policy = FeedbackPolicy(base_policy=base_policy, cost_fn=stream_cost, stats=stats, initial_state=search.init)

    # r = repeated_a_star(search, stats=stats, policy_ts=policy, max_steps=5, edge_eval_steps=10, debug=False, max_time=90)
    # print(r.solution, r.planning_time, r.expand_count)
    # sys.exit(0)
#%%
    from glob import glob
    from pddlstream.language.object import Object
    def extract_labels(path, stats):
        stream_plan = extract_stream_plan_from_path(path)
        (_,_,initial_state), _ = stream_plan[0]


        path_data = []
        for (edge, object_map) in stream_plan:
            action = edge[1]
            action_feasibility = stats.get(action)
            if not action_feasibility:
                break

            action_streams = list({action for action in object_map.values()})
            stream_feasibility = [stats[edge[2].id_anon_cg_map[stream.outputs[0]]] for stream in action_streams]
            path_data.append(dict(
                action_name=action.name,
                action_feasibility=action_feasibility,
                streams=[stream.serialize() for stream in action_streams],
                stream_feasibility=stream_feasibility
            ))
        return path_data

    def run_problem(problem_file_path, data):
        with open(problem_file_path, 'r') as f:
            problem_file = yaml.full_load(f)

        if len(problem_file['objects']) == 1:
            return # policy expects edges yo!

        init, goal, externals, actions = create_problem(problem_file_path)

        search = ActionStreamSearch(init, goal, externals, actions)
        if search.test_goal(search.init):
            return

        stats = {}
        if not args.disable_policy:
            base_policy = Policy(problem_file, search, model)
            policy = FeedbackPolicy(base_policy=base_policy, cost_fn=stream_cost, stats=stats, initial_state=search.init)
        else:
            policy = None
        m_attempts = lambda a: 10**(1 + a // 10)
        def stream_cost_fn(s, o, c, stats=stats, verbose=False):
            fa = stream_cost(s, o, c, given_stats=stats)
            fa = (1 + fa["num_successes"] * m_attempts(fa["num_attempts"])) / (1 +  fa["num_attempts"]*m_attempts(fa["num_attempts"]))
            return 1/fa

        start = time.time()
        r = repeated_a_star(search, search_func=search_func, stats=stats, policy_ts=policy, cost=stream_cost_fn,max_steps=100, edge_eval_steps=50, max_time=90)
        duration = time.time() - start
        print(os.path.basename(problem_file_path), len(r.action_skeleton or []),  len(r.skeletons), len({s[-1][1] for s in r.skeletons}), duration)
        path_data = []
        for path in r.skeletons:
            path_data.append(extract_labels(path, r.stats))

        objects = ({o_pddl:dict(name=o_pddl,value=Object.from_name(o_pddl).value) for o_pddl in search.init_objects})
        
        data[problem_file_path] = dict(
            name=os.path.basename(problem_file_path),
            planning_time=r.planning_time,
            solved=r.solution is not None,
            goal=goal,
            objects=objects,
            path_data=path_data,
            expanded=r.expand_count,
            evaluated=r.evaluate_count,
        )

    problems = sorted(glob(f"/home/{USER}/drake-tamp/experiments/blocks_world/data_generation/{args.problem_type}/test/*.yaml"))
    manager = mp.Manager()
    data = manager.dict()
    pool = mp.Pool(processes=mp.cpu_count() - 1)
    results = []
    for problem_file_path in problems:
        results.append(pool.apply_async(run_problem, (problem_file_path, data,)))
    pool.close()
    num_jobs = len(results)
    done_count = 0
    while done_count < num_jobs:
        time.sleep(0.1)
        done_count = sum([r.ready() for r in results])
    pool.join()

    data_dict = {k: v for k, v in data.items()}
    with open(output_path, 'wb') as f:
        pickle.dump(data_dict, f)

# # %%
# with open('../policy_test_data.pkl', 'rb') as f:
#     data = pickle.load(f)
# print(np.sum([r['solved'] for r in data]))
# print(np.sum([r['planning_time'] for r in data]))
# print(np.median([r['planning_time'] for r in data]))
# # %%
# with open('../policy_test_data_nonlin.pkl', 'rb') as f:
#     data = pickle.load(f)
# print(np.sum([r['solved'] for r in data]))
# print(np.sum([r['planning_time'] for r in data]))
# print(np.median([r['planning_time'] for r in data]))
# # %%
# with open('../policy_test_data_nonlin_nolevin.pkl', 'rb') as f:
#     data = pickle.load(f)
# print(np.sum([r['solved'] for r in data]))
# print(np.sum([r['planning_time'] for r in data]))
# print(np.median([r['planning_time'] for r in data]))
# #%%


# # %%
# pfiles = ["4_5_2_27.yaml","7_5_6_7.yaml","7_5_3_58.yaml","5_2_3_52.yaml","7_2_3_91.yaml","7_5_1_25.yaml","6_5_5_32.yaml","7_3_6_55.yaml","7_5_4_70.yaml","2_6_2_41.yaml","5_5_2_22.yaml","4_2_1_2.yaml","5_4_5_46.yaml","7_4_5_37.yaml","7_5_3_31.yaml","7_5_6_73.yaml",]
# pfiles = [f"/home/{USER}/drake-tamp/experiments/blocks_world/data_generation/random/test/" + x for x in pfiles]model = load_model()
# #%%
# problem_file_path = f"/home/{USER}/drake-tamp/experiments/blocks_world/data_generation/stacking/test/7_0_6_15.yaml"
# with open(problem_file_path, 'r') as f:
#     problem_file = yaml.full_load(f)

# state = State.from_scene(problem_file)
# model = load_model(model_path)
# list(invoke(state, model, .01))
# # %%
# list(invoke(
#     state \
#         .transition('pick(panda,green_block0,blue_table)')
#         .transition('place(panda,green_block0,green_table)')
#         .transition('pick(panda,green_block1,blue_table)')
#         .transition('place(panda,green_block1,green_table)')
#         .transition('pick(panda,green_block3,purple_table)')
#         .transition('place(panda,green_block3,green_table)')
#         .transition('pick(panda,green_block2,blue_table)')
#         .transition('place(panda,green_block2,green_table)')
#         .transition('pick(panda,green_block4,purple_table)')
#         .transition('place(panda,green_block4,green_table)')
#         .transition('pick(panda,red_block0,blue_table)')
#         .transition('place(panda,red_block0,red_table)')
#         .transition('pick(panda,red_block1,blue_table)')
#         .transition('place(panda,red_block1,red_table)')
#         .transition('pick(panda,red_block3,blue_table)')
#         .transition('place(panda,red_block3,red_table)')
#         .transition('pick(panda,red_block2,blue_table)')
#         .transition('place(panda,red_block2,red_table)')
#         .transition('pick(panda,red_block4,blue_table)')
#         .transition('place(panda,red_block4,red_table)')
# , model))
# # %%
# is_goal(state \
#         .transition('pick(panda,green_block0,blue_table)')
#         .transition('place(panda,green_block0,green_table)')
#         .transition('pick(panda,green_block1,blue_table)')
#         .transition('place(panda,green_block1,green_table)')
#         .transition('pick(panda,green_block3,purple_table)')
#         .transition('place(panda,green_block3,green_table)')
#         .transition('pick(panda,green_block2,blue_table)')
#         .transition('place(panda,green_block2,green_table)')
#         .transition('pick(panda,green_block4,purple_table)')
#         .transition('place(panda,green_block4,green_table)')
#         .transition('pick(panda,red_block0,blue_table)')
#         .transition('place(panda,red_block0,red_table)')
#         .transition('pick(panda,red_block1,blue_table)')
#         .transition('place(panda,red_block1,red_table)')
#         .transition('pick(panda,red_block3,blue_table)')
#         .transition('place(panda,red_block3,red_table)')
#         .transition('pick(panda,red_block2,blue_table)')
#         .transition('place(panda,red_block2,red_table)')
#         .transition('pick(panda,red_block4,blue_table)')
#         .transition('place(panda,red_block4,red_table)'))
# %%
