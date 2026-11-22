"""공개 입력과 실험 결과를 대조하고 작은 예제의 모든 방문 순서를 검산한다."""

import argparse
import csv
import hashlib
import itertools
import json
import math
from pathlib import Path

from solvers import RoutingProblem, alns, greedy, sa


PROJECT = Path(__file__).resolve().parent


def require(condition, explanation):
    if not condition:
        raise ValueError(explanation)


def verify_order(route, order):
    expected = set(route["stops"])
    require(len(order) == len(expected) and set(order) == expected,
            f"{route['id']}: 배송지 누락·중복 또는 잘못된 지점이 있습니다.")


def verify_data(route):
    stops = route["stops"]
    depot = route["depot"]
    require(len(stops) == len(set(stops)) and depot not in stops,
            f"{route['id']}: 출발지·배송지 식별자 오류")
    nodes = [depot, *stops]
    for origin in nodes:
        for target in nodes:
            seconds = route["travel"][origin][target]
            require(math.isfinite(seconds) and seconds >= 0,
                    f"{route['id']}: {origin}→{target} 이동시간 오류")
    for stop in stops:
        for start, end, seconds in route["windows"][stop]:
            require(math.isfinite(seconds) and seconds >= 0,
                    f"{route['id']}/{stop}: 서비스 시간 오류")
            require(all(t is None or math.isfinite(t) for t in [start, end]),
                    f"{route['id']}/{stop}: 시간창 오류")
            require(start is None or end is None or start <= end,
                    f"{route['id']}/{stop}: 시간창 역전")
    verify_order(route, route["actual"])


def replay(route, order):
    """별도 구현으로 방문 순서에서 이동, 대기, 처리 및 배송 지연을 복원한다."""
    verify_order(route, order)
    legs, waits, services, delays = [], [], [], []
    delayed_stops = set()
    clock = 0.0
    for origin, dest in zip([route["depot"], *order], order):
        travel = route["travel"][origin][dest]
        legs.append(travel)
        clock += travel
        packages = sorted(route["windows"][dest], key=lambda p: (
            math.inf if p[1] is None else p[1],
            -math.inf if p[0] is None else p[0],
        ))
        for begin, deadline, duration in packages:
            wait = 0.0 if begin is None else max(0.0, begin - clock)
            waits.append(wait)
            services.append(duration)
            clock = (max(clock, begin) if begin is not None else clock) + duration
            if deadline is not None and clock > deadline:
                delays.append(clock - deadline)
                delayed_stops.add(dest)
    travel = sum(legs)
    late = sum(delays)
    return dict(travel_seconds=travel, late_seconds=late, late_stops=len(delayed_stops),
                wait_seconds=sum(waits), service_seconds=sum(services), elapsed_seconds=clock,
                score=travel + 5 * late + 600 * len(delayed_stops))


def compare(expected, observed, context):
    for key, value in expected.items():
        actual = float(observed[key])
        # 비교 CSV가 소수점 단위로 반올림될 수 있으므로 0.001초까지 허용한다.
        tolerance = 0.0 if key == "late_stops" else 0.001
        require(math.isclose(value, actual, rel_tol=0.0, abs_tol=tolerance),
                f"{context} {key}: 독립 계산 {value}, 저장값 {actual}")


def check_small(method="all"):
    route = {
        "id": "hand-example", "depot": "S", "stops": ["A", "B", "C"],
        "actual": ["A", "B", "C"],
        "travel": {
            "S": {"S": 0, "A": 2, "B": 3, "C": 10},
            "A": {"S": 70, "A": 0, "B": 3, "C": 7},
            "B": {"S": 70, "A": 5, "B": 0, "C": 7},
            "C": {"S": 70, "A": 6, "B": 4, "C": 0},
        },
        "windows": {"A": [[8, 15, 2], [None, 8, 4]],
                    "B": [[None, 17, 2], [None, 16, 4]],
                    "C": [[None, None, 1]]},
    }
    verify_data(route)
    # S-A-B-C: A 도착 2초, A 출발 10초, B 도착 13초, B 처리 완료 17·19초.
    # B 물품 두 개가 1·2초 지연되므로 지연 배송지는 한 곳, 총지연은 3초이다.
    expected = dict(travel_seconds=12, wait_seconds=2, service_seconds=13,
                    elapsed_seconds=27, late_seconds=3, late_stops=1, score=627)
    compare(expected, replay(route, route["actual"]), "손계산 검증")
    problem = RoutingProblem(route)
    try:
        problem.evaluate(["A", "A", "C"])
    except ValueError:
        pass
    else:
        raise ValueError("중복 방문과 누락이 있는 경로를 평가기가 받아들였습니다.")
    optimum = math.inf
    for order in itertools.permutations(route["stops"]):
        expected_metrics = replay(route, order)
        optimum = min(optimum, expected_metrics["score"])
        compare(expected_metrics, problem.evaluate(list(order)), str(order))
    initial = greedy(problem)
    initial_score = replay(route, initial)["score"]
    for solver in [sa, alns]:
        if method not in ("all", solver.__name__):
            continue
        candidate, iterations = solver(problem, initial.copy(), 0.05, 42)
        candidate_score = replay(route, candidate)["score"]
        require(optimum <= candidate_score <= initial_score,
                f"{solver.__name__}: 작은 예제의 최적값·초기값 범위 오류")
        require(iterations >= 0, f"{solver.__name__}: 반복 횟수 오류")
    print(f"손계산 및 3개 배송지의 6개 순서 검산 통과 (최적 비용 {optimum:g})")


def run_key(row):
    raw = row["seed"]
    return row["route_id"], row["method"], None if raw in (None, "") else int(raw)


def check_results(routes, results_dir, settings, method):
    with (results_dir / "runs.csv").open(encoding="utf-8-sig", newline="") as source:
        rows = list(csv.DictReader(source))
    with (results_dir / "orders.jsonl").open(encoding="utf-8-sig") as source:
        orders = [json.loads(line) for line in source if line.strip()]
    records = {run_key(row): row for row in rows}
    orders_by_key = {run_key(row): row["order"] for row in orders}
    require(records and len(records) == len(rows) and len(orders_by_key) == len(orders),
            "비어 있는 결과 또는 중복 결과 행")
    require(records.keys() == orders_by_key.keys(), "평가 결과와 경로 목록이 일치하지 않습니다.")
    represented = {key[0] for key in records}
    require(represented == set(settings["route_ids"]) and len(represented) == settings["n_routes"],
            "실험 설정과 결과 경로 수·ID가 일치하지 않습니다.")
    planned_seeds = set(settings["seeds"])
    require(planned_seeds and len(planned_seeds) == len(settings["seeds"]), "시드 누락 또는 중복")
    searches = ("sa", "alns") if method == "all" else (() if method == "greedy" else (method,))
    expected_runs = {("actual", None), ("greedy", None)}
    expected_runs.update((name, seed) for name in searches for seed in planned_seeds)
    for rid in represented:
        require(rid in routes, f"원자료에 없는 경로 {rid}")
        actual_runs = {(m, seed) for r, m, seed in records if r == rid}
        require(actual_runs == expected_runs, f"{rid}: 선택한 방법 또는 반복 실행 시드 불일치")
        require(orders_by_key[rid, "actual", None] == routes[rid]["actual"],
                f"{rid}: 실제 방문 순서 불일치")
    for key, row in records.items():
        rid, method, _ = key
        route = routes[rid]
        require(method in {"actual", "greedy", "sa", "alns"}, f"알 수 없는 방법 {method}")
        require(int(row["n_stops"]) == len(route["stops"]), f"{key}: 배송지 수 불일치")
        metrics = replay(route, orders_by_key[key])
        compare(metrics, row, str(key))
        if method in ("sa", "alns"):
            initial_score = float(records[rid, "greedy", None]["score"])
            require(metrics["score"] <= initial_score + 0.001, f"{key}: 초기 해보다 악화")
    print(f"실제 경로 {len(represented)}개, 결과 {len(records)}행: 방문지 및 모든 비용 독립 검산 통과")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=PROJECT / "data" / "routes.json")
    parser.add_argument("--results", type=Path, default=PROJECT / "results")
    parser.add_argument("--method", choices=("all", "greedy", "sa", "alns"),
                        help="검산할 담당 방법. 생략하면 settings.json의 방법을 사용합니다.")
    parser.add_argument("--toy-only", action="store_true", help="손계산과 작은 예제만 검산")
    args = parser.parse_args()
    if args.toy_only:
        check_small(args.method or "all")
        return
    settings = json.loads((args.results / "settings.json").read_text(encoding="utf-8"))
    method = settings.get("method", "all")  # Older pilot files used the full comparison.
    require(method in ("all", "greedy", "sa", "alns"), "저장된 실행 방법을 확인하세요.")
    require(args.method is None or args.method == method,
            f"요청한 검산 방법({args.method})과 저장된 방법({method})이 다릅니다.")
    check_small(method)
    payload = args.data.read_bytes()
    require(hashlib.sha256(payload).hexdigest() == settings["data_sha256"],
            "검산할 데이터 파일이 실행에 사용한 데이터와 다릅니다.")
    raw = json.loads(payload)
    routes = {route["id"]: route for route in raw}
    require(raw and len(routes) == len(raw), "데이터 경로 누락 또는 중복 식별자")
    for route in raw:
        verify_data(route)
    check_results(routes, args.results, settings, method)


if __name__ == "__main__":
    main()
