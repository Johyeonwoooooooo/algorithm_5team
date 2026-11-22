"""다차량 솔버 검산: 손계산 예제, 모든 해 열거, 같은 시드 재실행, 결과 폴더의 독립 재계산.

python check_multi.py                          손계산 예제와 재현성만 검사
python check_multi.py --results results        위 검사 뒤 그 아래 baseline/greedy/sa/alns 결과를 다시 계산해 대조
"""
import argparse
import csv
import hashlib
import itertools
import json
import math
from pathlib import Path

from dataset import FOLDER, StationDay, haversine_km
from solvers_multi import MultiProblem, alns, greedy, sa


PROJECT = Path(__file__).resolve().parent
EXPECTED = {"baseline": [("A", "recorded")], "greedy": [("B", "greedy"), ("C", "greedy")],
            "sa": [(s, "sa") for s in ("B", "C", "C2")], "alns": [(s, "alns") for s in ("B", "C", "C2")]}


def require(condition, explanation):
    if not condition:
        raise ValueError(explanation)


def replay(depot, vehicles, stops, travel, routes, weights, max_duration=None):
    """솔버와 별도로 짠 채점기. routes 는 차량 순서대로 놓인 배송지 ID 목록이다.
    max_duration 을 주면 차량별 소요시간(출발~마지막 배송 완료)이 그 이하인지도 검사한다."""
    visited = [stop for order in routes for stop in order]
    require(len(routes) == len(vehicles) and len(visited) == len(set(visited)) and set(visited) == set(stops),
            "배송지 누락·중복 또는 차량 수 불일치")
    total = dict(travel_seconds=0.0, wait_seconds=0.0, late_seconds=0.0, late_stops=0, late_packages=0,
                 service_seconds=0.0)
    completions, durations = [], []
    for vehicle, order in zip(vehicles, routes):
        require(sum(stops[stop]["volume_cm3"] for stop in order) <= vehicle["capacity_cm3"],
                f"{vehicle['route_id']}: 용량 초과")
        clock = float(vehicle["departure_offset_seconds"])
        for origin, stop in zip([depot, *order], order):
            seconds = travel(origin, stop)
            total["travel_seconds"] += seconds
            clock += seconds
            packages = sorted(stops[stop]["windows"], key=lambda p: (
                math.inf if p[1] is None else p[1], -math.inf if p[0] is None else p[0]))
            late_here = False
            for begin, deadline, duration in packages:
                if begin is not None and clock < begin:
                    total["wait_seconds"] += begin - clock
                    clock = begin
                clock += duration
                total["service_seconds"] += duration
                if deadline is not None and clock > deadline:
                    total["late_seconds"] += clock - deadline
                    total["late_packages"] += 1
                    late_here = True
            total["late_stops"] += late_here
        completions.append(clock if order else None)
        durations.append(clock - vehicle["departure_offset_seconds"])
        require(max_duration is None or durations[-1] <= max_duration, f"{vehicle['route_id']}: 근무시간 상한 초과")
    total["cost"] = (total["travel_seconds"] + weights["wait_weight"] * total["wait_seconds"]
                     + weights["late_second_cost"] * total["late_seconds"]
                     + weights["late_stop_cost"] * total["late_stops"])
    total["last_completion_seconds"] = max(c for c in completions if c is not None)
    total["longest_duration_seconds"] = max(durations)
    return total, completions


def close(a, b):
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-6)


def compare(expected, observed, context):
    for key, value in expected.items():
        require(close(value, float(observed[key])), f"{context} {key}: 독립 계산 {value}, 저장값 {observed[key]}")


# ---------------------------------------------------------------- 손계산 예제
TOY_WEIGHTS = dict(wait_weight=1.0, late_second_cost=5.0, late_stop_cost=600.0)


def toy():
    """차 2대, 배송지 5곳. 기록된 순서에서 대기(b)와 지각(c)이 모두 생긴다."""
    forward = {("D", "a"): 5, ("D", "b"): 8, ("D", "c"): 9, ("D", "d"): 6, ("D", "e"): 10,
               ("a", "b"): 4, ("a", "c"): 7, ("a", "d"): 6, ("a", "e"): 9, ("b", "c"): 6,
               ("b", "d"): 5, ("b", "e"): 4, ("c", "d"): 8, ("c", "e"): 5, ("d", "e"): 7}
    table = dict(forward) | {(b, a): seconds + 1 for (a, b), seconds in forward.items()}   # 반대 방향은 1초 더 걸린다.
    vehicles = [{"route_id": "V1", "capacity_cm3": 10, "departure_offset_seconds": 0, "recorded_order": ["a", "b", "c"]},
                {"route_id": "V2", "capacity_cm3": 10, "departure_offset_seconds": 10, "recorded_order": ["d", "e"]}]
    stops = {"a": {"volume_cm3": 4, "windows": [[None, None, 2]]},
             "b": {"volume_cm3": 3, "windows": [[20, 40, 3]]},
             "c": {"volume_cm3": 3, "windows": [[None, 18, 2]]},
             "d": {"volume_cm3": 5, "windows": [[None, None, 4]]},
             "e": {"volume_cm3": 4, "windows": [[None, None, 2], [None, 30, 1]]}}
    return "D", vehicles, stops, table


def check_toy():
    depot, vehicles, stops, table = toy()
    travel = lambda a, b: table[a, b]
    problem = MultiProblem(depot, vehicles, stops, lambda a, b: (table[a, b], False), **TOY_WEIGHTS)
    recorded = [vehicle["recorded_order"] for vehicle in vehicles]
    # V1 (0초 출발): a 도착 5, 출발 7 / b 도착 11, 20까지 9초 대기, 출발 23 / c 도착 29, 완료 31 → 마감 18보다 13초 지각.
    #    이동 5+4+6=15, 대기 9, 지각 13초·1곳, 완료 31, 소요 31.  비용 15 + 1×9 + 5×13 + 600×1 = 689
    # V2 (10초 출발): d 도착 16, 출발 20 / e 도착 27, 마감 30 물품 먼저 28, 나머지 30.
    #    이동 6+7=13, 대기 0, 지각 0, 완료 30, 소요 20.  비용 13
    # 근무시간 상한(기록된 경로 중 가장 긴 소요시간) = 31
    by_hand = dict(travel_seconds=28, wait_seconds=9, late_seconds=13, late_stops=1, late_packages=1,
                   service_seconds=14, cost=702, last_completion_seconds=31, longest_duration_seconds=31)
    independent, completions = replay(depot, vehicles, stops, travel, recorded, TOY_WEIGHTS)
    compare(by_hand, independent, "손계산")
    require(completions == [31, 30], f"손계산 완료 시각: {completions}")
    require(problem.max_duration == 31, f"근무시간 상한 기본값: {problem.max_duration}")
    as_numbers = lambda routes: [[problem.index[s] for s in order] for order in routes]
    as_names = lambda routes: [[problem.ids[s] for s in order] for order in routes]
    compare(by_hand, problem.evaluate(as_numbers(recorded)), "솔버 채점기 손계산")
    # 마지막 예: V1 이 b(대기 후 23) → c(31) → a(8초 이동 + 2초 = 41) 로 가면 소요 41 > 상한 31.
    for bad, reason in (([["a", "b"], ["d", "e"]], "누락"), ([["a", "b", "c", "a"], ["d", "e"]], "중복"),
                        ([["a", "b", "c", "d"], ["e"]], "용량 초과"), ([["b", "c", "a"], ["d", "e"]], "근무시간 상한 초과")):
        for judge in (lambda: problem.evaluate(as_numbers(bad)),
                      lambda: replay(depot, vehicles, stops, travel, bad, TOY_WEIGHTS, 31)):
            try:
                judge()
            except (ValueError, KeyError):
                continue
            raise ValueError(f"채점기가 {reason} 해를 받아들였습니다.")
    require(problem.route_cost(0, as_numbers([["b", "c", "a"]])[0]) == math.inf, "상한을 넘은 경로의 탐색 비용이 inf 가 아닙니다.")
    # 720개(5! × 나누는 자리 6곳)의 모든 해를 두 채점기로 비교하고 최적값을 구한다.
    optimum = {False: math.inf, True: math.inf}
    feasible = rejected = 0
    for order in itertools.permutations(stops):
        for cut in range(len(order) + 1):
            routes = [list(order[:cut]), list(order[cut:])]
            try:
                expected, _ = replay(depot, vehicles, stops, travel, routes, TOY_WEIGHTS, 31)
            except ValueError:
                rejected += 1
                try:
                    problem.evaluate(as_numbers(routes))
                except ValueError:
                    continue
                raise ValueError(f"두 채점기의 가능 여부 판단이 다릅니다: {routes}")
            feasible += 1
            compare(expected, problem.evaluate(as_numbers(routes)), str(routes))
            total = sum(problem.route_cost(v, r) for v, r in enumerate(as_numbers(routes)))
            require(close(total, expected["cost"]), f"탐색용 비용 불일치 {routes}")
            optimum[False] = min(optimum[False], expected["cost"])
            if all(set(r) == set(v["recorded_order"]) for r, v in zip(routes, vehicles)):
                optimum[True] = min(optimum[True], expected["cost"])
    for fixed in (True, False):
        start, _ = greedy(problem, fixed)
        require(start == greedy(problem, fixed)[0], "탐욕법: 두 번 실행한 결과가 다릅니다.")
        start_cost = replay(depot, vehicles, stops, travel, as_names(start), TOY_WEIGHTS, 31)[0]["cost"]
        require(optimum[fixed] <= start_cost, "탐욕 해가 최적값보다 작습니다.")
        for solver in (sa, alns):
            first, history = solver(problem, start, 300, 42, fixed)
            again, history_again = solver(problem, start, 300, 42, fixed)
            require(first == again and history == history_again, f"{solver.__name__}: 같은 시드인데 결과가 다릅니다.")
            cost = replay(depot, vehicles, stops, travel, as_names(first), TOY_WEIGHTS, 31)[0]["cost"]
            require(optimum[fixed] <= cost <= start_cost, f"{solver.__name__}: 최적값·초기값 범위를 벗어났습니다.")
            require(close(history[-1][1], cost), f"{solver.__name__}: 기록의 최선 비용과 해의 비용이 다릅니다.")
            if fixed:
                require(all(set(r) == set(v["recorded_order"]) for r, v in zip(as_names(first), vehicles)),
                        f"{solver.__name__}: 배정 고정인데 차량이 바뀌었습니다.")
    print(f"손계산 예제 통과: 기록된 해 비용 702·상한 31, 가능한 해 {feasible}개 모두 두 채점기 일치"
          f"(불가능 {rejected}개도 판단 일치), 최적 비용 배정 고정 {optimum[True]:g} / 자유 {optimum[False]:g}")


# ---------------------------------------------------------------- 실제 데이터
def station_travel(day):
    """추정값은 솔버와 같이 0.1초로 맞춘다."""
    def travel(a, b):
        seconds, estimated = day.travel(a, b)
        return round(seconds, 1) if estimated else seconds
    return travel


def all_estimated_travel(day):
    """모든 구간을 manifest 의 멱함수로 계산한다(거점 구간은 거점용 계수)."""
    model = day.manifest["travel_time_model"]
    depot = day.depot["id"]
    place = {depot: day.depot, **day.stops}

    def travel(a, b):
        fit = model["depot_to_dropoff" if a == depot else "dropoff_to_depot" if b == depot else "between_dropoffs"]
        distance = haversine_km(place[a]["lat"], place[a]["lng"], place[b]["lat"], place[b]["lng"])
        return round(fit["a"] * distance ** fit["b"], 1) if distance > 0 else 0.0
    return travel


def check_repeat(day):
    problem = MultiProblem.from_station_day(day)
    for solver, iterations in ((sa, 20000), (alns, 60)):
        for fixed in (True, False):
            first = solver(problem, problem.recorded, iterations, 7, fixed)
            again = solver(problem, problem.recorded, iterations, 7, fixed)
            require(first == again, f"{solver.__name__}: 실제 데이터에서 같은 시드인데 결과가 다릅니다.")
            problem.evaluate(first[0])          # 누락·중복·용량·근무시간 상한
            if fixed:
                require(all(sorted(a) == sorted(b) for a, b in zip(first[0], problem.recorded)), "배정 고정 위반")
    print("실제 데이터에서 같은 시드로 두 번 실행: SA·ALNS, 배정 고정·자유 모두 해와 기록이 같음")


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as source:
        return list(csv.DictReader(source))


def check_results(day, root):
    depot, vehicles, stops = day.depot["id"], day.vehicles, day.stops
    recorded = [vehicle["recorded_order"] for vehicle in vehicles]
    home = {stop: number for number, order in enumerate(recorded) for stop in order}
    actual, estimated = station_travel(day), all_estimated_travel(day)
    key = lambda row: (row["scenario"], row["method"], "" if row["seed"] in (None, "") else str(row["seed"]))
    folders = [name for name in EXPECTED if (root / name / "settings.json").exists()]
    require("baseline" in folders, f"{root}: baseline 폴더가 없습니다.")
    base = json.loads((root / "baseline" / "settings.json").read_text(encoding="utf-8"))
    greedy_cost = {}
    checked = 0
    for name in folders:
        folder = root / name
        settings = json.loads((folder / "settings.json").read_text(encoding="utf-8"))
        for file, digest in settings["data_sha256"].items():
            require(hashlib.sha256((FOLDER / file).read_bytes()).hexdigest() == digest, f"{name}/{file}: 실행 때와 다른 데이터")
        require(all(settings[k] == base[k] for k in ("weights", "max_duration_seconds", "max_duration_source")),
                f"{name}: baseline 과 가중치·근무시간 상한이 다릅니다.")
        weights, limit = settings["weights"], settings["max_duration_seconds"]
        if settings["max_duration_source"] != "argument":
            longest = replay(depot, vehicles, stops, actual, recorded, weights)[0]["longest_duration_seconds"]
            require(close(limit, longest), f"{name}: 근무시간 상한 {limit} 이 기록된 최장 소요시간 {longest} 과 다릅니다.")
        records = {key(row): row for row in read_csv(folder / "runs.csv")}
        with (folder / "solutions.jsonl").open(encoding="utf-8") as source:
            routes_by_key = {key(row): row["routes"] for row in map(json.loads, filter(str.strip, source))}
        seeds = [str(seed) for seed in settings.get("seeds", [""])]
        require(set(records) == {(s, m, seed) for s, m in EXPECTED[name] for seed in seeds} == set(routes_by_key),
                f"{name}: 예정된 실행과 저장된 실행이 다릅니다.")
        last = {}
        if name in ("sa", "alns"):
            for row in read_csv(folder / "history.csv"):
                last[key(row)] = float(row["best_cost"])
        for run, row in records.items():
            routes = routes_by_key[run]
            # 누락·중복·용량·근무시간 상한은 replay 안에서 검사한다.
            totals, _ = replay(depot, vehicles, stops, actual, routes, weights, limit)
            compare({**{k: totals[k] for k in ("cost", "travel_seconds", "wait_seconds", "late_stops", "late_packages",
                                               "last_completion_seconds", "longest_duration_seconds")},
                     "late_minutes": totals["late_seconds"] / 60,
                     "vehicles_used": sum(bool(order) for order in routes)}, row, f"{name} {run}")
            other, _ = replay(depot, vehicles, stops, estimated, routes, weights)
            compare({"allest_cost": other["cost"], "allest_travel_seconds": other["travel_seconds"],
                     "allest_wait_seconds": other["wait_seconds"], "allest_late_stops": other["late_stops"],
                     "allest_late_minutes": other["late_seconds"] / 60}, row, f"{name} {run}")
            moved = sum(home[stop] != number for number, order in enumerate(routes) for stop in order)
            overlap = [[sum(home[stop] == a for stop in order) for order in routes] for a in range(len(routes))]
            kept = max(sum(overlap[a][b] for a, b in enumerate(match))
                       for match in itertools.permutations(range(len(routes))))
            legs = [day.travel(a, b) for order in routes for a, b in zip([depot, *order], order)]
            guessed = [round(seconds, 1) for seconds, flag in legs if flag]
            compare({"moved_stops": moved, "moved_stops_best_match": len(stops) - kept,
                     "estimated_legs": len(guessed), "estimated_leg_seconds": sum(guessed)}, row, f"{name} {run}")
            if run[0] == "A":
                require(routes == recorded, "A 가 기록된 배정·순서와 다릅니다.")
            if run[0] in "AB":
                require(moved == 0, f"{name} {run}: 배정 고정인데 옮겨진 배송지가 있습니다.")
            if name == "greedy":
                greedy_cost[run[0]] = totals["cost"]
            if name in ("sa", "alns"):
                require(totals["cost"] <= float(row["initial_cost"]) + 1e-6, f"{name} {run}: 초기 해보다 나쁩니다.")
                require(close(last[run], totals["cost"]), f"{name} {run}: 기록의 마지막 최선 비용과 해의 비용이 다릅니다.")
                start = "B" if run[0] == "C2" else run[0]      # C2 는 B 의 탐욕 해에서 출발한다.
                if start in greedy_cost:
                    require(close(float(row["initial_cost"]), greedy_cost[start]),
                            f"{name} {run}: 출발점 비용이 greedy 폴더의 결과와 다릅니다.")
            checked += 1
    print(f"{root}: {', '.join(folders)} 의 해 {checked}개 모두 배송지 한 번씩·용량 이내·근무시간 상한 이내, "
          f"비용·대기·지각·추정 채점 독립 재계산 일치")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--results", type=Path, nargs="*", default=[],
                        help="run_multi.py 의 --output 폴더(baseline, greedy, sa, alns 를 담은 폴더)")
    parser.add_argument("--toy-only", action="store_true", help="손계산 예제만 검사")
    args = parser.parse_args()
    check_toy()
    if args.toy_only:
        return
    day = StationDay()
    check_repeat(day)
    for root in args.results:
        check_results(day, root)


if __name__ == "__main__":
    main()
