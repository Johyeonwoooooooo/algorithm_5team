"""배정과 순서를 따로 푸는 3 x 3 실험 (배정 알고리즘 x 순서 알고리즘).

1단계(배정): 이미 저장된 결과에서 배정만 가져온다. 탐욕법은 C, SA·ALNS 는 C2 와 C 의 해를 쓴다.
2단계(순서): 그 배정을 고정하고(fixed=True) 순서를 처음부터 다시 짠다. 탐욕 삽입으로 출발해 SA·ALNS 를 돌린다.

배정을 "기록된 배정" 자리에 넣은 MultiProblem 을 새로 만들기 때문에 솔버는 고치지 않는다.

python run_split.py --source results/extra/limits/sa2000000_alns20000 --sa-iterations 2000000 --alns-iterations 20000
"""
import argparse
from concurrent.futures import ProcessPoolExecutor
import csv
import json
import os
from pathlib import Path
from statistics import mean
from time import perf_counter

from dataset import StationDay
from solvers_multi import MultiProblem, alns, greedy, sa

SEARCHES = {"sa": sa, "alns": alns}
# 배정 이름 -> (결과 폴더, 시나리오). C 는 탐욕 배정에서, C2 는 기록된 배정에서 출발한 해다.
ASSIGNMENTS = {"greedy": ("greedy", "C"), "sa": ("sa", "C2"), "alns": ("alns", "C2"),
               "sa_C": ("sa", "C"), "alns_C": ("alns", "C")}


def _work(task):
    assigner, order_method, seed, iterations, source = task     # source: 차량별 배송지 ID 목록
    start = perf_counter()
    day = StationDay()
    base = MultiProblem.from_station_day(day)
    vehicles = [{**vehicle, "recorded_order": order} for vehicle, order in zip(day.vehicles, source)]
    problem = MultiProblem(day.depot["id"], vehicles, day.stops, day.travel, max_duration=base.max_duration)
    routes, report = greedy(problem, fixed=True)
    if order_method != "greedy":
        routes, _ = SEARCHES[order_method](problem, routes, iterations, seed, fixed=True)
    assert all(set(order) == set(given) for order, given in zip(routes, problem.recorded)), "배정이 바뀌었다"
    result = base.evaluate(routes)      # 원래 문제로 채점: 용량·근무시간 상한·누락 검사 포함
    return {"assignment": assigner, "order": order_method, "seed": seed, "iterations": iterations,
            "cost": result["cost"], "travel_minutes": result["travel_seconds"] / 60,
            "late_stops": result["late_stops"], "late_minutes": result["late_seconds"] / 60,
            "longest_duration_hours": result["longest_duration_seconds"] / 3600,
            "greedy_reset_to_source_order": len(report["vehicles_reset_to_recorded_order"]),
            "runtime_seconds": perf_counter() - start}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, default=Path("results"), help="배정을 가져올 결과 폴더")
    parser.add_argument("--output", type=Path, default=Path("try_split"))
    parser.add_argument("--sa-iterations", type=int, default=8_000_000)
    parser.add_argument("--alns-iterations", type=int, default=80_000)
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    args = parser.parse_args()
    iterations = {"greedy": None, "sa": args.sa_iterations, "alns": args.alns_iterations}

    sources, joint = [], {}       # (배정 알고리즘, 시드, 배정), 함께 푼 원래 비용
    for assigner, (folder, scenario) in ASSIGNMENTS.items():
        for line in (args.source / folder / "solutions.jsonl").read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            if item["scenario"] == scenario:
                sources.append((assigner, item["seed"], item["routes"]))
        with (args.source / folder / "runs.csv").open(encoding="utf-8-sig") as file:
            joint[assigner] = mean(float(row["cost"]) for row in csv.DictReader(file) if row["scenario"] == scenario)
    seeds = sorted({seed for _, seed, _ in sources if seed is not None})
    tasks = []
    for assigner, seed, routes in sources:
        tasks.append((assigner, "greedy", seed, None, routes))
        # 탐욕 배정은 하나뿐이라 순서 단계에서 모든 시드를 돌린다. 나머지는 배정을 만든 시드를 그대로 쓴다.
        for order_seed in (seeds if seed is None else [seed]):
            tasks += [(assigner, method, order_seed, iterations[method], routes) for method in SEARCHES]

    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        rows = []
        for row in pool.map(_work, tasks):
            rows.append(row)
            print(f"{row['assignment']:>6} -> {row['order']:<6} seed {row['seed']}  cost {row['cost']:.1f}  "
                  f"{row['runtime_seconds']:.0f}s", flush=True)

    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / "runs.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    print("\n평균 비용 (행: 배정, 열: 순서). 맨 오른쪽은 배정 알고리즘이 순서까지 함께 푼 원래 비용")
    print(f"{'':>8}" + "".join(f"{name:>10}" for name in ("greedy", "sa", "alns", "joint")))
    for assigner in ASSIGNMENTS:
        cells = [mean(row["cost"] for row in rows if row["assignment"] == assigner and row["order"] == method)
                 for method in ("greedy", "sa", "alns")]
        print(f"{assigner:>8}" + "".join(f"{value:>10,.0f}" for value in cells + [joint[assigner]]))
    print("Results:", args.output / "runs.csv")


if __name__ == "__main__":
    main()
