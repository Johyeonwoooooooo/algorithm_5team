"""실제 배송 경로에서 탐욕법·SA·ALNS의 한 단계를 계산해 보여 준다."""

import argparse
import json
from math import exp, inf
from pathlib import Path
from random import Random

from solvers import RoutingProblem, _destroy, _insert, _repair, greedy


def greedy_example(problem):
    print("\n[탐욕적 삽입법] 마감이 빠른 순서로 배송지 세 곳을 넣는 과정")
    order = []
    candidates = sorted(problem.stops, key=lambda s: (problem.deadline[s], s))[:3]
    for stop in candidates:
        previous_score = problem.score(order)
        scores = []
        print(f"배송지 {stop} 추가")
        for pos in range(len(order) + 1):
            candidate = _insert(order, stop, pos)
            score = problem.score(candidate)
            scores.append(score)
            sequence = " → ".join([problem.depot, *candidate])
            print(f"  {sequence}  비용 {score:.2f}점  변화 {score - previous_score:+.2f}점")
        position = min(range(len(scores)), key=scores.__getitem__)
        order.insert(position, stop)
        print(f"  선택: {' → '.join([problem.depot, *order])}")


def sa_example(problem, order, seed):
    print("\n[SA] 탐욕 경로에서 초기 온도로 후보 한 개 평가")
    rng = Random(seed)
    candidate = order.copy()
    i, j = rng.sample(range(len(order)), 2)
    if rng.random() < 0.5:
        candidate[i], candidate[j] = candidate[j], candidate[i]
        print(f"  교환: {i + 1}번째 {order[i]} ↔ {j + 1}번째 {order[j]}")
    else:
        candidate.insert(j, candidate.pop(i))
        print(f"  이동: 배송지 {order[i]}를 {i + 1}번째에서 {j + 1}번째로")
    current_score = problem.evaluate(order)["score"]
    candidate_score = problem.evaluate(candidate)["score"]
    delta = candidate_score - current_score
    temperature = max(1.0, current_score / len(order) * 0.25)
    print(f"  현재 비용 {current_score:.2f}점 → 후보 비용 {candidate_score:.2f}점")
    if delta > 0:
        probability = exp(-delta / temperature)
        draw = rng.random()
        accepted = draw < probability
        print(f"  초기 온도 {temperature:.2f}, 비용 증가 {delta:.2f}점")
        print(f"  수용 확률 exp(-증가량/온도) = {probability:.6g}, 난수 = {draw:.6f}")
    else:
        accepted = True
        print("  비용이 감소하거나 같으므로 후보를 수용한다.")
    print(f"  후보 {'수용' if accepted else '거절'} · 지금까지의 최선 비용 {min(current_score, candidate_score):.2f}점")


def alns_example(problem, order, seed):
    print("\n[ALNS] 탐욕 경로에서 한 번 제거하고 재삽입")
    rng = Random(seed)
    removal_methods = ("random", "long_detour", "nearby")
    repair_methods = ("deadline_first", "regret_2")
    names = {"random": "무작위 제거", "long_detour": "우회비용 큰 순 제거",
             "nearby": "가까운 지점 제거", "deadline_first": "마감 순 복구",
             "regret_2": "둘째 삽입 위치를 택할 때 손해가 큰 지점 먼저 복구"}
    removal = rng.choices(removal_methods, weights=[1] * len(removal_methods))[0]
    repair = rng.choices(repair_methods, weights=[1] * len(repair_methods))[0]
    n = len(order)
    count = rng.randint(2, min(n, max(2, min(8, round(n * 0.1)))))
    partial, removed = _destroy(problem, order, count, removal, rng)
    candidate = _repair(problem, partial, removed, repair, rng, deadline=inf)
    score_before = problem.evaluate(order)["score"]
    score_after = problem.evaluate(candidate)["score"]
    print(f"  선택한 조합: {names[removal]} + {names[repair]}")
    print(f"  제거한 배송지 {count}곳: {', '.join(removed)}")
    for stop in removed:
        print(f"    {stop}의 방문 순번: {order.index(stop)+1} → {candidate.index(stop)+1}")
    print(f"  현재 비용 {score_before:.2f}점 → 후보 비용 {score_after:.2f}점")
    tolerance = score_before * 0.01
    accepted = score_after <= score_before + tolerance
    reward = 5.0 if score_after < score_before else 1.0 if accepted else 0.3
    print(f"  첫 단계 허용비용 {score_before + tolerance:.2f}점, 후보 {'수용' if accepted else '거절'}")
    print(f"  두 선택 방법의 가중치: 각각 1.00 → {0.8 + 0.2 * reward:.2f} (보상 {reward:g})")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path(__file__).resolve().parent / "data" / "routes.json")
    parser.add_argument("--route", type=int, default=1, help="데이터의 경로 순번(1부터)")
    parser.add_argument("--method", choices=("all", "greedy", "sa", "alns"), default="all")
    parser.add_argument("--seed", type=int, default=224, help="한 단계 학습 예시용 난수 시드")
    args = parser.parse_args()
    routes = json.loads(args.data.read_bytes())
    if not 1 <= args.route <= len(routes):
        parser.error(f"--route 범위는 1~{len(routes)}입니다.")
    route = routes[args.route - 1]
    problem = RoutingProblem(route)
    print(f"실제 자료의 한 단계 학습 예시 · 경로 {args.route} · 배송지 {len(problem.stops)}곳")
    print(f"{route['id']} · 출발지 {problem.depot} · 예시 시드 {args.seed}")
    print("아래는 탐색 시작 시점의 온도·허용 폭으로 계산한 설명 예시다. results/v1_순서만/에는 시간 제한 실험의 최종 순서와 지표가 있다.")
    if args.method in ("all", "greedy"):
        greedy_example(problem)
    if args.method != "greedy":
        if len(problem.stops) < 2:
            parser.error("SA와 ALNS 예시에는 배송지가 두 곳 이상 필요합니다.")
        initial = greedy(problem)
        if args.method in ("all", "sa"):
            sa_example(problem, initial, args.seed)
        if args.method in ("all", "alns"):
            alns_example(problem, initial, args.seed)


if __name__ == "__main__":
    main()
