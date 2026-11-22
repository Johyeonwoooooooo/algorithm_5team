"""한 차량의 배송지 방문 순서를 만드는 세 알고리즘. 외부 패키지는 필요하지 않다.

시간 단위는 초이다. 출발 시각을 0으로 두고 창고에서 마지막 배송지까지 평가한다.
마지막 지점에서 창고로 복귀하는 시간은 포함하지 않는다.
"""
from math import exp, inf, isfinite
from random import Random
from time import perf_counter


LATE_SECOND_COST = 5.0
LATE_STOP_COST = 600.0


class RoutingProblem:
    """배송지 사이 이동시간과 배송지별 물품 처리 순서를 공통으로 준비한다."""

    def __init__(self, route):
        self.stops = tuple(route["stops"])
        self.depot = route["depot"]
        self.travel = route["travel"]
        stop_ids = set(self.stops)
        if len(stop_ids) != len(self.stops) or self.depot in stop_ids:
            raise ValueError("배송지 ID는 서로 달라야 하며 출발지를 포함하지 않아야 합니다.")
        for origin in (*self.stops, self.depot):
            for destination in self.stops:
                if origin != destination:
                    value = self.travel[origin][destination]
                    if not isfinite(value) or value < 0:
                        raise ValueError("이동시간은 0 이상의 유한한 값이어야 합니다.")

        self.deadline = {}
        self.services = {}
        self.timed_packages = {}
        for stop in self.stops:
            packages = []
            for start, end, service in route["windows"][stop]:
                if (start is not None and not isfinite(start)) or (
                    end is not None and not isfinite(end)
                ) or not isfinite(service) or service < 0:
                    raise ValueError("물품의 시간 정보가 올바르지 않습니다.")
                packages.append((
                    -inf if start is None else start,
                    inf if end is None else end,
                    service,
                ))
            # 마감이 빠른 물품부터 처리한다. 마감까지 같으면 시작 시각,
            # 두 조건이 모두 같으면 원래 데이터에 기록된 순서를 따른다.
            packages.sort(key=lambda item: (item[1], item[0]))
            self.deadline[stop] = packages[0][1] if packages else inf
            self.services[stop] = sum(item[2] for item in packages)
            has_window = any(start != -inf or end != inf for start, end, _ in packages)
            self.timed_packages[stop] = packages if has_window else ()

    def _simulate(self, order):
        previous = self.depot
        clock = travel_total = late_total = waiting = service_total = 0.0
        late_stops = 0
        for stop in order:
            leg = self.travel[previous][stop]
            travel_total += leg
            clock += leg
            service_total += self.services[stop]
            packages = self.timed_packages[stop]
            if packages:
                stop_late = False
                for start, end, service in packages:
                    if clock < start:
                        waiting += start - clock
                        clock = start
                    clock += service
                    if clock > end:
                        late_total += clock - end
                        stop_late = True
                late_stops += int(stop_late)
            else:
                # 시간창 없는 지점에서는 서비스 시간 합계만 더해도 결과가 같다.
                clock += self.services[stop]
            previous = stop
        score = travel_total + LATE_SECOND_COST * late_total + LATE_STOP_COST * late_stops
        return score, travel_total, late_total, late_stops, waiting, service_total, clock

    def evaluate(self, order):
        """최종 경로에 모든 배송지가 정확히 한 번 등장하는지 확인하고 평가한다."""
        if len(order) != len(self.stops) or set(order) != set(self.stops):
            raise ValueError("경로에 모든 배송지가 정확히 한 번 포함되어야 합니다.")
        fields = ("score", "travel_seconds", "late_seconds", "late_stops", "wait_seconds",
                  "service_seconds", "elapsed_seconds")
        return dict(zip(fields, self._simulate(order)))

    def score(self, order):
        """삽입 과정의 부분 경로에도 적용하는 공통 비용."""
        return self._simulate(order)[0]


def _insert(order, stop, position):
    return order[:position] + [stop] + order[position:]


def greedy(problem):
    """마감 순으로 배송지를 꺼내어 모든 삽입 위치의 비용을 비교한다."""
    order = []
    for stop in sorted(problem.stops, key=lambda s: (problem.deadline[s], s)):
        positions = range(len(order) + 1)
        best_position = min(positions, key=lambda i: problem.score(_insert(order, stop, i)))
        order.insert(best_position, stop)
    return order


def sa(problem, initial, seconds, seed):
    """두 지점 교환 또는 한 지점 이동을 제안하고 냉각 일정에 따라 수용한다."""
    current = list(initial)
    current_score = problem.evaluate(current)["score"]
    best, best_score = current.copy(), current_score
    if seconds <= 0 or len(current) < 2:
        return best, 0
    rng = Random(seed)
    start = perf_counter()
    iterations = 0
    # 초기 온도는 배송지 하나당 초기 비용에 비례하며 시간 소진 시 1/100로 감소한다.
    initial_temperature = max(1.0, current_score / len(current) * 0.25)
    while (elapsed := perf_counter() - start) < seconds:
        candidate = current.copy()
        i, j = rng.sample(range(len(current)), 2)
        if rng.random() < 0.5:
            candidate[i], candidate[j] = candidate[j], candidate[i]
        else:
            candidate.insert(j, candidate.pop(i))
        candidate_score = problem.score(candidate)
        delta = candidate_score - current_score
        temperature = initial_temperature * 0.01 ** (elapsed / seconds)
        if delta <= 0 or rng.random() < exp(-delta / temperature):
            current, current_score = candidate, candidate_score
            if current_score < best_score:
                best, best_score = current.copy(), current_score
        iterations += 1
    return best, iterations


def _travel_change(problem, previous, stop, following):
    """기존 연결 사이에 stop을 넣을 때 늘어나는 이동시간. 마지막 뒤에 넣을 수도 있다."""
    extra = problem.travel[previous][stop]
    if following is not None:
        extra += problem.travel[stop][following] - problem.travel[previous][following]
    return extra


def _destroy(problem, order, count, method, rng):
    if method == "random":
        removed = rng.sample(order, count)
    elif method == "long_detour":
        def detour(i):
            previous = problem.depot if i == 0 else order[i - 1]
            following = order[i + 1] if i + 1 < len(order) else None
            return _travel_change(problem, previous, order[i], following)
        ranked = sorted(range(len(order)), key=detour, reverse=True)
        removed = [order[i] for i in ranked[:count]]
    else:
        anchor = rng.choice(order)
        nearby = sorted((s for s in order if s != anchor),
                        key=lambda s: problem.travel[anchor][s] + problem.travel[s][anchor])
        removed = [anchor] + nearby[:count - 1]
    removed_set = set(removed)
    return [s for s in order if s not in removed_set], removed


def _positions(problem, partial, stop, rng):
    """큰 경로에서는 이동시간 증가가 작은 위치와 무작위 위치를 함께 평가한다."""
    positions = list(range(len(partial) + 1))
    if len(positions) <= 12:
        return positions

    def extra(i):
        previous = partial[i - 1] if i else problem.depot
        following = partial[i] if i < len(partial) else None
        return _travel_change(problem, previous, stop, following)

    ranked = sorted(positions, key=extra)
    return ranked[:10] + rng.sample(ranked[10:], 2)


def _repair(problem, partial, removed, method, rng, deadline):
    # regret-2는 첫째·차선 위치의 비용 차가 커서 좋은 위치를 놓칠 위험이
    # 큰 배송지를 먼저 배치한다. 다른 방법은 마감 순으로 하나씩 배치한다.
    remaining = sorted(removed, key=lambda s: (problem.deadline[s], s))
    while remaining:
        options = remaining[:1] if method == "deadline_first" else remaining
        selected = None
        largest_regret = -inf
        for stop in options:
            if perf_counter() >= deadline:
                return None
            choices = sorted(
                (problem.score(_insert(partial, stop, i)), i)
                for i in _positions(problem, partial, stop, rng)
            )
            regret = choices[1][0] - choices[0][0] if len(choices) > 1 else 0
            if regret > largest_regret:
                largest_regret = regret
                selected = (stop, choices[0][1])
        stop, position = selected
        partial.insert(position, stop)
        remaining.remove(stop)
    return partial


def alns(problem, initial, seconds, seed):
    """제거·복구 방법을 성과에 따라 가중 추출한다. 반환값은 최선 경로와 완료 반복 수."""
    current = list(initial)
    current_score = problem.evaluate(current)["score"]
    best, best_score = current.copy(), current_score
    n = len(current)
    if seconds <= 0 or n < 2:
        return best, 0
    rng = Random(seed)
    removal = ("random", "long_detour", "nearby")
    repair = ("deadline_first", "regret_2")
    remove_weights, repair_weights = [1.0] * len(removal), [1.0] * len(repair)
    start = perf_counter()
    deadline = start + seconds
    iterations = 0
    while (now := perf_counter()) < deadline:
        i = rng.choices(range(len(removal)), weights=remove_weights)[0]
        j = rng.choices(range(len(repair)), weights=repair_weights)[0]
        # 시간 제한 내 여러 재구성을 평가하도록 한 번에 최대 8곳을 제거한다.
        count = rng.randint(2, min(n, max(2, min(8, round(n * 0.1)))))
        partial, removed = _destroy(problem, current, count, removal[i], rng)
        candidate = _repair(problem, partial, removed, repair[j], rng, deadline)
        if candidate is None:
            break
        candidate_score = problem.score(candidate)
        # 우수해 주변에서 약간 나쁜 후보도 수용한다. 시간에 따라 허용 폭을 줄인다.
        tolerance = best_score * 0.01 * (deadline - now) / seconds
        reward = 0.3
        if candidate_score <= current_score or candidate_score <= best_score + tolerance:
            if candidate_score < best_score:
                best, best_score = candidate.copy(), candidate_score
                reward = 5.0
            elif candidate_score < current_score:
                reward = 3.0
            else:
                reward = 1.0
            current, current_score = candidate, candidate_score
        remove_weights[i] = 0.8 * remove_weights[i] + 0.2 * reward
        repair_weights[j] = 0.8 * repair_weights[j] + 0.2 * reward
        iterations += 1
    return best, iterations
