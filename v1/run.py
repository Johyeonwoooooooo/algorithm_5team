"""Run a paired delivery-route experiment on normalized Amazon routes (Python 3.10+)."""
import argparse
import csv
import hashlib
import json
import platform
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from time import perf_counter

from solvers import RoutingProblem, alns, greedy, sa


ROOT = Path(__file__).resolve().parent
METHODS = ("actual", "greedy", "sa", "alns")
METRICS = ("score", "travel_seconds", "late_seconds", "late_stops",
           "wait_seconds", "service_seconds", "elapsed_seconds")


def write_csv(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def summarize(rows):
    # Average seeds within route first so each historical route has equal weight.
    groups = defaultdict(list)
    for row in rows:
        groups[row["route_id"], row["method"]].append(row)
    paired = []
    for (route_id, method), runs in groups.items():
        item = {"route_id": route_id, "n_stops": runs[0]["n_stops"],
                "method": method, "n_runs": len(runs)}
        for key in (*METRICS, "runtime_seconds"):
            values = [row[key] for row in runs if row[key] is not None]
            item[key] = statistics.mean(values) if values else None
        item["score_seed_sd"] = statistics.stdev(run["score"] for run in runs) if len(runs) > 1 else None
        paired.append(item)
    baseline = {row["route_id"]: row for row in paired if row["method"] == "greedy"}
    summary = []
    for method in METHODS:
        subset = [row for row in paired if row["method"] == method]
        if not subset:
            continue
        item = {"method": method, "n_routes": len(subset)}
        for key in (*METRICS, "runtime_seconds"):
            values = [row[key] for row in subset if row[key] is not None]
            item[key] = statistics.mean(values) if values else None
        diffs = [baseline[row["route_id"]]["score"] - row["score"] for row in subset]
        item["routes_beating_greedy"] = sum(diff > 1e-6 for diff in diffs)
        item["routes_tied_with_greedy"] = sum(abs(diff) <= 1e-6 for diff in diffs)
        summary.append(item)
    return paired, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=ROOT / "data" / "routes.json")
    parser.add_argument("--method", choices=("all", "greedy", "sa", "alns"), default="all",
                        help="Choose the solver to run; actual and greedy are always included")
    parser.add_argument("--output", type=Path, help="Output folder (default: results or results_METHOD)")
    parser.add_argument("--limit", type=int, help="Use only the first N normalized routes")
    parser.add_argument("--seconds", type=float, default=1.0, help="Search budget per route, method and seed")
    parser.add_argument("--seeds", nargs="+", type=int, default=[11, 29, 47])
    args = parser.parse_args()
    if args.output is None:
        args.output = ROOT / ("results" if args.method == "all" else f"results_{args.method}")
    if not 0 < args.seconds < float("inf") or args.limit is not None and args.limit <= 0:
        parser.error("seconds and limit must be positive")
    if len(set(args.seeds)) != len(args.seeds):
        parser.error("seeds must be unique")
    raw = args.data.read_bytes()
    routes = json.loads(raw)[:args.limit]
    if not routes or len({r["id"] for r in routes}) != len(routes):
        parser.error("empty data or duplicate route IDs")
    selected_searches = [(name, search) for name, search in (("sa", sa), ("alns", alns))
                         if args.method in ("all", name)]
    rows, orders = [], []
    for number, route in enumerate(routes, start=1):
        problem = RoutingProblem(route)
        def record(method, order, seed=None, iterations=None, init=None, search=None):
            if len(order) != len(route["stops"]) or set(order) != set(route["stops"]):
                raise ValueError(f'{method} produced an invalid order for {route["id"]}')
            elapsed = None if init is None else init + (search or 0.0)
            metrics = problem.evaluate(order)
            rows.append({"route_id": route["id"], "n_stops": len(order), "method": method,
                         "seed": seed, "iterations": iterations, "initial_seconds": init,
                         "search_seconds": search, "runtime_seconds": elapsed,
                         **{key: metrics[key] for key in METRICS}})
            orders.append({"route_id": route["id"], "method": method, "seed": seed, "order": list(order)})
        record("actual", route["actual"])
        start = perf_counter()
        initial = greedy(problem)
        initial_seconds = perf_counter() - start
        record("greedy", initial, init=initial_seconds)
        for index, seed in enumerate(args.seeds if selected_searches else []):
            # Alternate running order across routes and seeds on the same machine.
            searches = selected_searches[::-1] if (number + index) % 2 else selected_searches
            for name, search in searches:
                start = perf_counter()
                best, iterations = search(problem, list(initial), args.seconds, seed)
                search_seconds = perf_counter() - start
                record(name, best, seed, iterations, initial_seconds, search_seconds)
        print(f'{number}/{len(routes)}  {route["id"]}  ({len(initial)} stops)', flush=True)
    paired, summary = summarize(rows)
    args.output.mkdir(parents=True, exist_ok=True)
    write_csv(args.output / "runs.csv", rows)
    write_csv(args.output / "by_route.csv", paired)
    write_csv(args.output / "summary.csv", summary)
    with (args.output / "orders.jsonl").open("w", encoding="utf-8") as file:
        for item in orders:
            file.write(json.dumps(item) + "\n")
    settings = {"method": args.method, "data_sha256": hashlib.sha256(raw).hexdigest(), "n_routes": len(routes),
                "search_seconds_budget": args.seconds, "seeds": args.seeds, "python": sys.version,
                "platform": platform.platform(), "route_ids": [r["id"] for r in routes],
                "solver_sha256": hashlib.sha256((ROOT / "solvers.py").read_bytes()).hexdigest()}
    (args.output / "settings.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")
    print("Results:", args.output)


if __name__ == "__main__":
    main()
