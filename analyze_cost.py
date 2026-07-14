#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Case-level cost analysis for v6opt vs baseline (and v6old) on swebench-verified."""
import json, glob, os

BASE = '/data00/home/fanmeihao/projects/OptiHarnessForCost/results/swebench-verified'
DIRS = {
    'v6opt':    f'{BASE}/evolve-v6cycle-swebench-0711-211200',
    'baseline': f'{BASE}/noevolve-swebench-0703-031441',
    'v6old':    f'{BASE}/evolve-v6cycle-swebench-0711-104138',
}

# Pricing (per token)
P_UNCACHED = 0.27 / 1e6
P_CACHE    = 0.07 / 1e6
P_OUTPUT   = 1.10 / 1e6


def count_steps(case_dir):
    """Count agent LLM turns from trajectory.json (source == 'agent')."""
    traj = os.path.join(case_dir, 'agent', 'trajectory.json')
    if not os.path.exists(traj):
        return 0
    try:
        t = json.load(open(traj))
        steps = t.get('steps') or []
        return sum(1 for s in steps if isinstance(s, dict) and s.get('source') == 'agent')
    except Exception:
        return 0


def load_results(result_dir):
    cases = {}
    for f in glob.glob(f'{result_dir}/*/result.json'):
        case_dir = os.path.dirname(f)
        case_id = os.path.basename(case_dir)
        if case_id == 'config.json':
            continue
        # real case id = repo__issue-number (drop random suffix after 2nd __)
        parts = case_id.split('__')
        real_id = '__'.join(parts[:2]) if len(parts) >= 2 else case_id
        try:
            d = json.load(open(f))
            ar = d.get('agent_result') or {}
            inp = ar.get('n_input_tokens', 0) or 0
            cache = ar.get('n_cache_tokens', 0) or 0
            out = ar.get('n_output_tokens', 0) or 0
            # reward from verifier_result.rewards.reward
            vr = d.get('verifier_result') or {}
            reward = 0
            if isinstance(vr, dict):
                reward = (vr.get('rewards') or {}).get('reward', 0) or 0
            steps = count_steps(case_dir)
            uncached = inp - cache
            cost = uncached * P_UNCACHED + cache * P_CACHE + out * P_OUTPUT
            cases[real_id] = {
                'inp': inp, 'cache': cache, 'out': out, 'uncached': uncached,
                'steps': steps, 'reward': reward, 'cost': cost,
                'cache_ratio': cache / inp if inp > 0 else 0.0,
                'dir': case_id,
            }
        except Exception as e:
            print(f'  [WARN] failed {f}: {e}')
    return cases


def fmt(x):
    return f'{x:,}'


def main():
    data = {name: load_results(d) for name, d in DIRS.items()}
    v6opt, baseline, v6old = data['v6opt'], data['baseline'], data['v6old']

    print('#' * 100)
    print('# 载入统计')
    for name, c in data.items():
        tot = sum(v['cost'] for v in c.values())
        print(f'  {name:10s}: {len(c)} cases, 总成本 ${tot:.4f}')

    common = set(v6opt) & set(baseline)
    print(f'\nv6opt ∩ baseline 共同 case 数: {len(common)}')
    only_v6 = set(v6opt) - set(baseline)
    only_b = set(baseline) - set(v6opt)
    if only_v6: print(f'  仅 v6opt: {sorted(only_v6)}')
    if only_b: print(f'  仅 baseline: {sorted(only_b)}')

    # ---- 全局成本对比（共同 case）----
    def agg(cases, keys, field):
        return sum(cases[k][field] for k in keys)

    v_cost = agg(v6opt, common, 'cost'); b_cost = agg(baseline, common, 'cost')
    print('\n' + '#' * 100)
    print('# 全局成本对比（共同 case）')
    print(f'  v6opt 总成本:    ${v_cost:.4f}')
    print(f'  baseline 总成本: ${b_cost:.4f}')
    print(f'  降幅: {(b_cost - v_cost) / b_cost * 100:.2f}%  (省 ${b_cost - v_cost:.4f})')

    # ---- 成本构成分解 ----
    print('\n' + '#' * 100)
    print('# 成本构成分解（共同 case 合计）')
    for name, cases in [('v6opt', v6opt), ('baseline', baseline)]:
        u = agg(cases, common, 'uncached'); ca = agg(cases, common, 'cache'); o = agg(cases, common, 'out')
        cu, cc, co = u * P_UNCACHED, ca * P_CACHE, o * P_OUTPUT
        tot = cu + cc + co
        print(f'  {name}:')
        print(f'    uncached input: {fmt(u):>15}  -> ${cu:.4f} ({cu/tot*100:.1f}%)')
        print(f'    cached input:   {fmt(ca):>15}  -> ${cc:.4f} ({cc/tot*100:.1f}%)')
        print(f'    output:         {fmt(o):>15}  -> ${co:.4f} ({co/tot*100:.1f}%)')
    vu, bu = agg(v6opt, common, 'uncached'), agg(baseline, common, 'uncached')
    vc, bc = agg(v6opt, common, 'cache'), agg(baseline, common, 'cache')
    vo, bo = agg(v6opt, common, 'out'), agg(baseline, common, 'out')
    vi, bi = agg(v6opt, common, 'inp'), agg(baseline, common, 'inp')
    print('\n  v6opt / baseline 比值:')
    print(f'    total input:    {vi/bi:.3f}')
    print(f'    uncached input: {vu/bu:.3f}')
    print(f'    cached input:   {vc/bc:.3f}')
    print(f'    output:         {vo/bo:.3f}')

    # ---- 步数与效率 ----
    print('\n' + '#' * 100)
    print('# 步数 / cache 命中率 / 每步 token（共同 case 平均）')
    for name, cases in [('v6opt', v6opt), ('baseline', baseline)]:
        steps = [cases[k]['steps'] for k in common]
        cr = [cases[k]['cache_ratio'] for k in common]
        per_step = [cases[k]['inp'] / cases[k]['steps'] for k in common if cases[k]['steps']]
        rewards = [cases[k]['reward'] for k in common]
        print(f'  {name}:')
        print(f'    平均步数:          {sum(steps)/len(steps):.1f}  (min {min(steps)}, max {max(steps)}, 合计 {sum(steps)})')
        print(f'    平均 cache 命中率:  {sum(cr)/len(cr):.3f}')
        print(f'    平均每步 input:     {sum(per_step)/len(per_step):,.0f}')
        print(f'    平均 reward:        {sum(rewards)/len(rewards):.3f}  (解决 {sum(1 for r in rewards if r>=1)}/{len(rewards)})')

    # ---- 逐 case 差异 ----
    diffs = []
    for cid in common:
        v = v6opt[cid]; b = baseline[cid]
        diffs.append({
            'cid': cid, 'diff': v['cost'] - b['cost'],
            'vc': v['cost'], 'bc': b['cost'],
            'vs': v['steps'], 'bs': b['steps'],
            'vr': v['reward'], 'br': b['reward'],
            'vi': v['inp'], 'bi': b['inp'], 'vo': v['out'], 'bo': b['out'],
            'v_cache_ratio': v['cache_ratio'], 'b_cache_ratio': b['cache_ratio'],
        })
    diffs.sort(key=lambda x: -x['diff'])

    higher = [x for x in diffs if x['diff'] > 1e-9]
    lower = [x for x in diffs if x['diff'] < -1e-9]
    same = [x for x in diffs if abs(x['diff']) <= 1e-9]
    print('\n' + '#' * 100)
    print('# 逐 case 成本变化汇总')
    print(f'  成本升高 case: {len(higher)} 个, 合计多花 ${sum(x["diff"] for x in higher):.4f}')
    print(f'  成本降低 case: {len(lower)} 个, 合计节省 ${abs(sum(x["diff"] for x in lower)):.4f}')
    print(f'  成本持平 case: {len(same)} 个')

    print('\n=== v6opt 成本高于 baseline 的 case（Top 20，多花从大到小）===')
    hd = f'{"case_id":42s} {"v6$":>9} {"base$":>9} {"diff$":>9} {"v6st":>5} {"bst":>5} {"v6r":>4} {"br":>4} {"v6crat":>7} {"bcrat":>7}'
    print(hd)
    for x in higher[:20]:
        print(f'{x["cid"]:42s} {x["vc"]:9.4f} {x["bc"]:9.4f} {x["diff"]:+9.4f} {x["vs"]:5} {x["bs"]:5} {x["vr"]:4.0f} {x["br"]:4.0f} {x["v_cache_ratio"]:7.3f} {x["b_cache_ratio"]:7.3f}')

    print('\n=== v6opt 成本低于 baseline 的 case（Top 20，节省从大到小）===')
    print(hd)
    for x in sorted(lower, key=lambda z: z['diff'])[:20]:
        print(f'{x["cid"]:42s} {x["vc"]:9.4f} {x["bc"]:9.4f} {x["diff"]:+9.4f} {x["vs"]:5} {x["bs"]:5} {x["vr"]:4.0f} {x["br"]:4.0f} {x["v_cache_ratio"]:7.3f} {x["b_cache_ratio"]:7.3f}')

    # ---- 步数最多 case ----
    print('\n' + '#' * 100)
    print('# v6opt 步数最多的 15 个 case')
    for cid in sorted(common, key=lambda c: v6opt[c]['steps'], reverse=True)[:15]:
        v = v6opt[cid]; b = baseline[cid]
        print(f'  {cid:42s} v6st={v["steps"]:3} bst={b["steps"]:3} v6$={v["cost"]:.4f} b$={b["cost"]:.4f} diff={v["cost"]-b["cost"]:+.4f} v6crat={v["cache_ratio"]:.3f}')

    # ---- v6opt vs v6old ----
    common_old = set(v6opt) & set(v6old)
    if common_old:
        print('\n' + '#' * 100)
        print(f'# v6opt vs v6old（共同 case {len(common_old)}）')
        vo_cost = agg(v6opt, common_old, 'cost'); vold_cost = agg(v6old, common_old, 'cost')
        print(f'  v6opt 总成本: ${vo_cost:.4f}  |  v6old 总成本: ${vold_cost:.4f}  |  变化 {(vo_cost-vold_cost)/vold_cost*100:+.2f}%')
        for name, cases in [('v6opt', v6opt), ('v6old', v6old)]:
            steps = [cases[k]['steps'] for k in common_old]
            rewards = [cases[k]['reward'] for k in common_old]
            print(f'  {name}: 平均步数 {sum(steps)/len(steps):.1f}, 解决 {sum(1 for r in rewards if r>=1)}/{len(rewards)}')

    # ---- 完整逐 case 对比表 ----
    print('\n' + '#' * 100)
    print('# 完整逐 case 对比表（按成本差 diff 从高到低）')
    print(f'{"case_id":42s} {"v6opt$":>9} {"base$":>9} {"diff$":>9} {"v6st":>5} {"bst":>5} {"v6r":>4} {"br":>4}')
    for x in diffs:
        print(f'{x["cid"]:42s} {x["vc"]:9.4f} {x["bc"]:9.4f} {x["diff"]:+9.4f} {x["vs"]:5} {x["bs"]:5} {x["vr"]:4.0f} {x["br"]:4.0f}')

    # ---- 保存 JSON 供报告使用 ----
    out = {
        'summary': {
            'common_cases': len(common),
            'v6opt_total_cost': v_cost, 'baseline_total_cost': b_cost,
            'reduction_pct': (b_cost - v_cost) / b_cost * 100,
            'saved': b_cost - v_cost,
            'higher_count': len(higher), 'higher_total': sum(x['diff'] for x in higher),
            'lower_count': len(lower), 'lower_total': abs(sum(x['diff'] for x in lower)),
            'same_count': len(same),
            'ratio_input': vi/bi, 'ratio_uncached': vu/bu, 'ratio_cache': vc/bc, 'ratio_output': vo/bo,
        },
        'composition': {
            'v6opt': {'uncached': vu, 'cache': vc, 'output': vo},
            'baseline': {'uncached': bu, 'cache': bc, 'output': bo},
        },
        'cases': diffs,
    }
    with open('/data00/home/fanmeihao/projects/OptiHarnessForCost/cost_analysis_result.json', 'w') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print('\n[SAVED] cost_analysis_result.json')


if __name__ == '__main__':
    main()
