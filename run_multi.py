"""data/station_day 에서 배정과 순서를 함께 푸는 실험 (Python 3.10+).

A   기록된 배정 + 기록된 순서
B   기록된 배정 + 알고리즘 순서 (배정 고정)
C   알고리즘 배정 + 알고리즘 순서 (탐욕법이 처음부터 배정하고, SA·ALNS 는 그 해에서 출발)
C2  C 와 같되 SA·ALNS 가 B 의 탐욕 해(기록된 배정)에서 출발. 탐욕법 결과는 없다.

python run_multi.py --algo sa          자기 알고리즘만 실행 (greedy / sa / alns / all)

결과는 --output(기본 results/multi) 아래 baseline/, greedy/, sa/, alns/ 폴더에 나뉘어 저장된다.
탐욕 해는 SA·ALNS 의 출발점이라 어떤 --algo 에서도 계산하지만, greedy/ 폴더는 greedy 나 all 일 때만 쓴다.
"""
import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
import csv
import hashlib
from itertools import permutations
import json
import os
import platform
import sys
from pathlib import Path
from time import perf_counter

from dataset import FOLDER, StationDay, haversine_km
from solvers_multi import (LATE_SECOND_COST, LATE_STOP_COST, NEIGHBOURS, POSITIONS, WAIT_WEIGHT,
                           MultiProblem, alns, greedy, sa)


ROOT = Path(__file__).resolve().parent
SEARCHES = {"sa": sa, "alns": alns}
# 본 실험의 한도. 한도를 두 배로 늘릴 때마다 평균 비용이 아직 0.1~1.5% 줄어들어 완전히 수렴한 값은 아니다
# (RESULTS_MULTI.md 의 반복 한도 비교).
DEFAULT_ITERATIONS = {"sa": 8_000_000, "alns": 80_000}
_problem = None


def _start(options):
    global _problem
    _problem = MultiProblem.from_station_day(StationDay(), **options)


def _work(task):
    method, fixed, seed, iterations, initial = task
    start = perf_counter()
    if method == "greedy":
        routes, extra = greedy(_problem, fixed)
    else:
        routes, extra = SEARCHES[method](_problem, initial, iterations, seed, fixed)
    return routes, extra, perf_counter() - start


def all_estimated_times(day, problem):
    """착시 확인용: 모든 구간을 manifest 의 멱함수 추정값으로 바꾼 이동시간 표.
    배송지 사이는 between_dropoffs, 거점 구간은 방향별 거점 계수를 쓴다."""
    model = day.manifest["travel_time_model"]
    place = [day.depot] + [day.stops[name] for name in problem.ids[1:]]
    size = len(place)
    times = [[0.0] * size for _ in range(size)]
    for i in range(size):
        for j in range(size):
            if i != j:
                fit = model["depot_to_dropoff" if i == 0 else "dropoff_to_depot" if j == 0 else "between_dropoffs"]
                distance = haversine_km(place[i]["lat"], place[i]["lng"], place[j]["lat"], place[j]["lng"])
                times[i][j] = round(fit["a"] * distance ** fit["b"], 1) if distance > 0 else 0.0
    return times


def boundary_stops(day, problem, radius_km=0.5):
    """원래 다른 경로의 배송지가 500 m 안에 있는 배송지(경로 경계)."""
    place = [None] + [day.stops[name] for name in problem.ids[1:]]
    result = set()
    for i in range(1, len(place)):
        for j in range(i + 1, len(place)):
            if problem.home[i] != problem.home[j] and haversine_km(
                    place[i]["lat"], place[i]["lng"], place[j]["lat"], place[j]["lng"]) < radius_km:
                result.update((i, j))
    return result


def write_csv(path, rows, empty=()):
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]) if rows else list(empty))
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--algo", choices=("greedy", "sa", "alns", "all"), default="all", help="돌릴 알고리즘")
    parser.add_argument("--output", type=Path, default=ROOT / "results" / "multi")
    parser.add_argument("--sa-iterations", type=int, default=DEFAULT_ITERATIONS["sa"], help="SA 반복 횟수(이동 시도 횟수)")
    parser.add_argument("--alns-iterations", type=int, default=DEFAULT_ITERATIONS["alns"], help="ALNS 반복 횟수(제거·복구 횟수)")
    parser.add_argument("--seeds", nargs="+", type=int, default=[11, 29, 47, 61, 83])
    parser.add_argument("--wait-weight", type=float, default=WAIT_WEIGHT, help="대기 1초당 비용")
    parser.add_argument("--late-second-cost", type=float, default=LATE_SECOND_COST, help="물품별 지각 1초당 비용")
    parser.add_argument("--late-stop-cost", type=float, default=LATE_STOP_COST, help="지각 배송지 1곳당 비용")
    parser.add_argument("--max-duration-seconds", type=float,
                        help="차량별 소요시간(출발~마지막 배송 완료) 상한. 기본값은 기록된 경로 중 가장 긴 소요시간")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 1, help="동시에 돌릴 프로세스 수(결과에는 영향 없음)")
    args = parser.parse_args()
    if min(args.sa_iterations, args.alns_iterations) < 0 or len(set(args.seeds)) != len(args.seeds):
        parser.error("반복 횟수는 0 이상, 시드는 서로 달라야 합니다.")
    weights = {"wait_weight": args.wait_weight, "late_second_cost": args.late_second_cost,
               "late_stop_cost": args.late_stop_cost}
    options = {**weights, "max_duration": args.max_duration_seconds}
    day = StationDay()
    problem = MultiProblem.from_station_day(day, **options)
    estimated_times = all_estimated_times(day, problem)
    boundary = boundary_stops(day, problem)
    labels = [f"V{number + 1}" for number in range(len(problem.vehicle_ids))]
    iterations = {"sa": args.sa_iterations, "alns": args.alns_iterations}
    chosen = [name for name in SEARCHES if args.algo in (name, "all")]

    results = {"baseline": [("A", "recorded", None, None, None, problem.recorded, [], None)],
               "greedy": [], "sa": [], "alns": []}
    greedy_report = {}
    with ProcessPoolExecutor(max_workers=args.jobs, initializer=_start, initargs=(options,)) as pool:
        first = {scenario: pool.submit(_work, ("greedy", scenario == "B", None, None, None)) for scenario in "BC"}
        pending = []
        for scenario in "BC":
            routes, greedy_report[scenario], seconds = first[scenario].result()
            results["greedy"].append((scenario, "greedy", None, None, None, routes, [], seconds))
            print(f"{scenario} greedy  {seconds:.1f}s", flush=True)
            initial_cost = problem.evaluate(routes)["cost"]
            # C2 는 B 의 탐욕 해에서 출발해 차량 간 이동을 허용한다.
            pending += [(name, method, seed, initial_cost, seconds,
                         pool.submit(_work, (method, name == "B", seed, iterations[method], routes)))
                        for name in ((scenario, "C2") if scenario == "B" else (scenario,))
                        for method in chosen for seed in args.seeds]
        for scenario, method, seed, initial_cost, initial_seconds, future in pending:
            routes, history, seconds = future.result()
            results[method].append((scenario, method, seed, iterations[method], initial_cost, routes, history,
                                    initial_seconds + seconds))
            print(f"{scenario} {method} seed {seed}  {seconds:.1f}s", flush=True)

    common = {"weights": weights, "max_duration_seconds": problem.max_duration,
              "max_duration_source": "argument" if args.max_duration_seconds is not None else "longest recorded route",
              "vehicles": dict(zip(labels, problem.vehicle_ids)), "boundary_stops": len(boundary),
              "data_sha256": {name: hashlib.sha256((FOLDER / name).read_bytes()).hexdigest()
                              for name in ("station_day.json", "travel.json", "manifest.json")}}
    for folder_name in ["baseline"] + (["greedy"] if args.algo in ("greedy", "all") else []) + chosen:
        folder = args.output / folder_name
        settings = dict(common)
        if folder_name == "baseline":
            # 공통 폴더라 한 번만 쓴다. PC 마다 달라지는 정보는 넣지 않는다.
            if (folder / "settings.json").exists():
                if json.loads((folder / "settings.json").read_text(encoding="utf-8")) != settings:
                    parser.error(f"{folder} 에 다른 설정의 기준선이 있습니다. --output 을 바꾸거나 폴더를 지우세요.")
                continue
        else:
            settings |= {"algo": folder_name, "greedy_report": greedy_report,
                         "solver_sha256": hashlib.sha256((ROOT / "solvers_multi.py").read_bytes()).hexdigest(),
                         "python": sys.version, "platform": platform.platform()}
            if folder_name in SEARCHES:
                settings |= {"iterations": iterations[folder_name], "seeds": args.seeds, "neighbours": NEIGHBOURS,
                             "alns_positions": POSITIONS}
        rows, vehicle_rows, move_rows, history_rows, solutions = [], [], [], [], []
        for scenario, method, seed, count, initial_cost, routes, history, seconds in results[folder_name]:
            real = problem.evaluate(routes)
            guess = problem.evaluate(routes, estimated_times)
            where = {stop: number for number, order in enumerate(routes) for stop in order}
            moved = [stop for stop, number in where.items() if problem.home[stop] != number]
            # 차량 이름만 바뀐 경우를 빼려고, 원래 경로와 가장 많이 겹치도록 차량을 짝지었을 때의 이동 수도 센다.
            overlap = Counter((problem.home[stop], number) for stop, number in where.items())
            kept = max(sum(overlap[a, b] for a, b in enumerate(match)) for match in permutations(range(len(routes))))
            legs = [(a, b) for order in routes for a, b in zip([0, *order], order) if problem.estimated[a][b]]
            leg_seconds = sum(problem.T[a][b] for a, b in legs)
            run = {"scenario": scenario, "method": method, "seed": seed, "iterations": count}
            rows.append({**run, "initial_cost": initial_cost, "cost": real["cost"],
                         "travel_seconds": real["travel_seconds"], "wait_seconds": real["wait_seconds"],
                         "late_stops": real["late_stops"], "late_packages": real["late_packages"],
                         "late_minutes": real["late_seconds"] / 60, "service_seconds": real["service_seconds"],
                         "last_completion_seconds": real["last_completion_seconds"],
                         "longest_duration_seconds": real["longest_duration_seconds"],
                         "vehicles_used": real["vehicles_used"], "moved_stops": len(moved),
                         "moved_stops_best_match": problem.n_stops - kept,
                         "moved_boundary_stops": sum(stop in boundary for stop in moved),
                         "estimated_legs": len(legs), "estimated_leg_seconds": leg_seconds,
                         "estimated_time_share": leg_seconds / real["travel_seconds"],
                         "allest_cost": guess["cost"], "allest_travel_seconds": guess["travel_seconds"],
                         "allest_wait_seconds": guess["wait_seconds"], "allest_late_stops": guess["late_stops"],
                         "allest_late_minutes": guess["late_seconds"] / 60,
                         "allest_longest_duration_seconds": guess["longest_duration_seconds"],
                         "runtime_seconds": seconds})
            for number, item in enumerate(real["vehicles"]):
                vehicle_rows.append({**run, "vehicle": labels[number], "route_id": problem.vehicle_ids[number],
                                     "departure_offset_seconds": problem.offset[number], "stops": item["stops"],
                                     "load_share": item["load_cm3"] / problem.capacity[number],
                                     "cost": item["cost"], "travel_seconds": item["travel_seconds"],
                                     "wait_seconds": item["wait_seconds"], "late_stops": item["late_stops"],
                                     "late_packages": item["late_packages"], "late_minutes": item["late_seconds"] / 60,
                                     "duration_seconds": item["duration_seconds"],
                                     "completion_seconds": item["completion_seconds"]})
            pairs = Counter((problem.home[stop], where[stop]) for stop in moved)
            edge = Counter((problem.home[stop], where[stop]) for stop in moved if stop in boundary)
            move_rows += [{**run, "from_vehicle": labels[a], "to_vehicle": labels[b], "stops": pairs[a, b],
                           "boundary_stops": edge[a, b]} for a, b in sorted(pairs)]
            history_rows += [{**run, "at_iteration": at, "best_cost": best, "current_cost": current}
                             for at, best, current in history]
            solutions.append({**run, "routes": [[problem.ids[stop] for stop in order] for order in routes]})
        folder.mkdir(parents=True, exist_ok=True)
        write_csv(folder / "runs.csv", rows)
        write_csv(folder / "vehicles.csv", vehicle_rows)
        if folder_name != "baseline":
            write_csv(folder / "moves.csv", move_rows, ("scenario", "method", "seed", "iterations", "from_vehicle",
                                                        "to_vehicle", "stops", "boundary_stops"))
        if folder_name in SEARCHES:
            write_csv(folder / "history.csv", history_rows)
        with (folder / "solutions.jsonl").open("w", encoding="utf-8") as file:
            for item in solutions:
                file.write(json.dumps(item) + "\n")
        (folder / "settings.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")
        print("Results:", folder)


if __name__ == "__main__":
    main()
