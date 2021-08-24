from glob import glob
import yaml
import json
import os
import re
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import tikzplotlib

def dict_from_string(s:str):
    obj = eval(s, type('js', (dict,), dict(__getitem__=lambda s, n: n))())
    return obj

def parse_logs(exp_dir, exp_name):
    data = []
    for i, log_filename in enumerate(glob(os.path.join(exp_dir, '*.log'))):
        with open(log_filename, 'r') as f:
            d = {
                'solved': False,
                'exp_name': exp_name,
                'run':os.path.basename(log_filename).strip('.log'),
                'planning_iters': 0,
                'planning_time': 0,
                'expanded': float('nan')
            }

            data.append(d)
            for line in f.readlines():
                if line.startswith('Planning'):
                    i_star, i_ground, q = re.search('Planning. # optim: (\d+). # grounded: (\d+). Queue length: (\d+)', line).groups()
                    d['results'] = int(i_star)
                    d['evaluations'] = int(i_ground)
                    d['queue'] = int(q)
                    d['planning_iters'] += 1

                    match = re.search('. Expanded: (\d+). duration: ([0-9.]+)$', line)
                    if match:
                        expanded, duration = match.groups()
                        d['expanded'] = int(expanded)
                        d['planning_time'] += float(duration)
                if 'Results' in line:
                    (results, ) = re.search('Results: (\d+)', line).groups()
                    d['results'] = max(int(results), d.get('results', 0))
                if 'Attempt' in line:
                    d['planning_iters'] += 1
                if line.startswith('Summary:'):
                    d.update(dict_from_string(line[8:].replace('inf', '"inf"')))
    return data

def load_results_from_stats(exp_dir, exp_name):
    data = []
    for i, stats_path in enumerate(glob(os.path.join(exp_dir, '*_logs/stats.json'))):
        with open(stats_path, 'r') as f:
            run_stats = json.load(f)

        d = run_stats['summary']
        problem_file_path = run_stats["problem_file_path"]
        if problem_file_path is not None:
            with open(problem_file_path, "r") as f:
                problem_info = yaml.safe_load(f)
                assert "run_attr" in problem_info, f"No run_attr recorded in this problem file {problem_file_path}"
                for k,v in problem_info["run_attr"].items():
                    d[k] = v
        else:
            print(f"Warning: no problem_file_path provided from {stats_path}")
        d['results'] = max(run_stats['results'][1])
        d['evaluations'] = max(run_stats['evaluations'][1])
        # fd stats
        d['planning_iters'] = len(run_stats['fd_stats'])
        d['total_expanded'] = sum([p.get('expanded', 0) for p in run_stats['fd_stats']])
        d['median_expanded'] = np.median([p.get('expanded', 0) for p in run_stats['fd_stats']])
        d['mean_expanded'] = np.mean([p.get('expanded', 0) for p in run_stats['fd_stats']])
        d['max_expanded'] = max([p.get('expanded', 0) for p in run_stats['fd_stats']])
        d['total_evaluated'] = sum([p.get('evaluated', 0) for p in run_stats['fd_stats']])
        d['max_evaluated'] = max([p.get('evaluated', 0) for p in run_stats['fd_stats']])
        d['total_fd_search_time'] = sum([p.get('total_time', 0) for p in run_stats['fd_stats']])
        d['total_translation_time'] = sum([p.get('translation_time', 0) for p in run_stats['fd_stats']])
        d['total_fd_timeouts'] = sum([p.get('timeout', 0) for p in run_stats['fd_stats']])
        d['scoring_time'] = run_stats.get('scoring_time', float('nan'))
        d['exp_name'] = exp_name
        d['run'] = stats_path.split(os.path.sep)[-2].strip(".yaml_logs")
        data.append(d)
    return data

def compare_same_set(data, only_solved=False):
    runs = {}
    for d in data:
        if not only_solved or d.get('solved'):
            runs.setdefault(d['exp_name'], set()).add(d['run'])

    include = set()
    for exp in runs:
        include = include & runs[exp] if include else runs[exp]

    return [d for d in data if d['run'] in include]

def table_compare(data):
    data = compare_same_set(data)
    df = pd.DataFrame(data)

    df.loc[~df.solved, 'run_time'] = float('nan')
    df.loc[~df.solved, 'planning_time'] = float('nan')
    df.loc[~df.solved, 'sample_time'] = float('nan')
    df.loc[~df.solved, 'search_time'] = float('nan')
    df.loc[~df.solved, 'results'] = float('nan')
    df.loc[~df.solved, 'evaluations'] = float('nan')

    aggregates = df.groupby('exp_name').agg(['sum','mean', 'std'])[[
            ['solved', 'sum'],
            ['solved', 'mean'],
            ['run_time', 'mean'],
            ['run_time', 'std'],
            ['search_time', 'mean'],
            ['search_time', 'std'],
            ['sample_time', 'mean'],
            ['sample_time', 'std'],
            ['results', 'mean'],
            ['results', 'std'],
            ['evaluations', 'mean'],
            ['evaluations', 'std'],
        ]
    ]
    print(aggregates.to_string(float_format="%.2f"))
    # print(aggregates.to_latex(float_format="%.2f", bold_rows=True))

def num_blocks_vs_max_stack(data):
    for d in data:
        d['num_blocks'], d['num_blockers'], d['max_stack'], _ = map(int, d['run'].strip('.yaml').split('_'))

    df = pd.DataFrame(data)

    d = df.pivot_table(columns='max_stack', values='solved', index='num_blocks', aggfunc='mean')
    print(d.to_string(float_format="%.2f"))

def num_discs_vs_exp(data):
    for d in data:
        d['num_discs'] = int(d['run'].strip('.yaml').split('_')[-1])
    data = compare_same_set(data)
    df = pd.DataFrame(data)
    print_header('Num Discs vs Solved')
    d = df.groupby(['num_discs', 'exp_name']).agg(['mean', 'sum']).pivot_table(columns='exp_name', values=[('solved', 'mean'), ('solved', 'sum')], index='num_discs')
    print(d.to_string(float_format="%.2f"))

    print_header('Num Discs vs FD time')
    df.loc[~df.solved, 'run_time'] = 60
    d = df.groupby(['num_discs', 'exp_name']).agg(['mean', 'median']).pivot_table(columns='exp_name', values=[('total_fd_search_time', 'mean')], index='num_discs')
    print(d.to_string(float_format="%.2f"))

    print_header('Num Discs vs FD time')
    df.loc[~df.solved, 'run_time'] = 60
    d = df.groupby(['num_discs', 'exp_name']).agg(['mean', 'median']).pivot_table(columns='exp_name', values=[('run_time', 'mean')], index='num_discs')
    print(d.to_string(float_format="%.2f"))

    print_header('Num Discs vs FD time')
    df.loc[~df.solved, 'run_time'] = 60
    d = df.groupby(['num_discs', 'exp_name']).agg(['mean', 'median']).pivot_table(columns='exp_name', values=[('results', 'mean')], index='num_discs')
    print(d.to_string(float_format="%.2f"))
    #d = df.groupby(['num_discs', 'exp_name']).agg(['mean', 'sum']).pivot_table(columns='exp_name', values=[('solved', 'mean'), ('run_time', 'mean')], index='num_discs')
    #print(d.to_string(float_format="%.2f"))

def num_blocks_vs_exp(data):
    for d in data:
        d['num_blocks'], d['num_blockers'], d['max_stack'], _ = map(int, d['run'].strip('.yaml').split('_'))

    data = compare_same_set(data)
    df = pd.DataFrame(data)

    print_header('Num Blocks vs Solved')
    d = df.groupby(['num_blocks', 'exp_name']).agg(['mean', 'sum']).pivot_table(columns='exp_name', values=[('solved', 'mean'), ('solved', 'sum')], index='num_blocks')
    print(d.to_string(float_format="%.2f"))

    print_header('Num Blocks vs Runtime')
    df.loc[~df.solved, 'run_time'] = 60
    d = df.groupby(['num_blocks', 'exp_name']).agg(['mean', 'median']).pivot_table(columns='exp_name', values=[('run_time', 'mean')], index='num_blocks')
    print(d.to_string(float_format="%.2f"))

    print_header('Num Blocks vs Results')
    d = df.groupby(['num_blocks', 'exp_name']).agg(['mean', 'median']).pivot_table(columns='exp_name', values=[('results', 'mean')], index='num_blocks')
    print(d.to_string(float_format="%.2f"))

def bar_plot_compare(img_save_path, data, x_axis_key, y_axis_key, agg = "mean", verbose = True, bar_width = 0.35, tex_save_path = None):
    # ie. x_axis_key: "num_discs"
    # agg in ["mean", "sum", "median"]

    #data = compare_same_set(data)
    df = pd.DataFrame(data)

    d = df.groupby([x_axis_key, "exp_name"]).agg([agg]).pivot_table(columns="exp_name", values = [(y_axis_key, agg)], index = x_axis_key)
    if verbose:
        print(d.to_string(float_format = "%.2f"))
    x = np.array(d.axes[0])
    fig, ax = plt.subplots()
    rects_list = []
    num_exp = len(d.columns)
    middle_loc = num_exp*bar_width/2
    for i, key in enumerate(d.columns):
        y = np.array(d[key])
        exp_name = key[-1]
        rects  = ax.bar(x + bar_width*i + bar_width/2 - middle_loc, y, bar_width, label = exp_name)
        rects_list.append(rects)
    ax.set_xlabel(x_axis_key.replace("_", " ").title())
    ax.set_ylabel(y_axis_key.replace("_", " ").title())
    ax.set_xticks(x)
    ax.legend()

    fig.tight_layout()
    plt.savefig(img_save_path, dpi = 400)
    if tex_save_path is not None:
        tikzplotlib.save(tex_save_path)
    
    print()

def print_header(st):
    print(('\n' * 2) + ('#' * 10), st, ('#' * 10) + ('\n' * 1))



if __name__ == '__main__':
    import pandas as pd

    def plot_and_print_blocks(dir):
        print_header(dir)
        data_adaptive = load_results_from_stats(f'/home/agrobenj/drake-tamp/experiments/{dir}/save/', 'adaptive')
        data_oracle = load_results_from_stats(f'/home/agrobenj/drake-tamp/experiments/{dir}/oracle/', 'oracle')
        table_compare(data_adaptive + data_oracle)
        #num_discs_vs_exp(data_oracle + data_adaptive)
        #num_blocks_vs_exp(data_adaptive + data_oracle)
        bar_plot_compare("num_blocks_run_time.png", data_adaptive + data_oracle, "num_blocks", "run_time", tex_save_path= "num_blocks_run_time.tex", bar_width = 0.25)
        bar_plot_compare("num_blocks_solved.png", data_adaptive + data_oracle, "num_blocks", "solved", tex_save_path= "num_blocks_solved.tex", bar_width = 0.25)

    data_adaptive = load_results_from_stats(f'/home/agrobenj/drake-tamp/experiments/hanoi_logs/save/', 'adaptive')
    bar_plot_compare("test_plot.png", data_adaptive, "num_discs", "solved", tex_save_path= "test_plot.tex", bar_width = 0.25)
    bar_plot_compare("test_plot.png", data_adaptive, "num_discs", "run_time", tex_save_path= "test_plot.tex", bar_width = 0.25)

    #plot_and_print("non_monotonic_logs")
    #plot_and_print("clutter_logs")
    #plot_and_print("sorting_logs")
    #plot_and_print("stacking_logs")
