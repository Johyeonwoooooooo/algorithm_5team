"""여러 차량의 배정과 방문 순서를 함께 푸는 세 알고리즘. 외부 패키지는 필요하지 않다.

시계 규칙은 solvers.py 와 같다. 시간 단위는 초이고, 차량은 거점에서 출발해 마지막
배송지에서 끝난다(거점 복귀 시간은 넣지 않는다). 다른 점은 다음과 같다.

- 차량마다 시계가 자기 출발 오프셋에서 시작한다.
- 차량의 소요시간(출발부터 마지막 배송 완료까지)은 근무시간 상한을 넘을 수 없다. 넘는 경로의 비용은
  무한대로 처리해 어떤 알고리즘도 받아들이지 않는다. 상한의 기본값은 기록된 경로 중 가장 긴 소요시간이다.
- 비용 = 이동시간 + WAIT_WEIGHT × 대기시간 + LATE_SECOND_COST × 지각시간 + LATE_STOP_COST × 지각 배송지 수
- 탐색 한도는 시간이 아니라 반복 횟수다. 같은 시드와 같은 반복 횟수면 같은 결과가 나온다.
- 해는 차량 순서대로 놓인 배송지 번호 목록이다. 번호 0은 거점이다.
"""
from math import exp, inf, isfinite
from random import Random


# 비용 가중치. 모두 "이동시간 1초"를 1로 둔 환산값이며 근거가 있는 값이 아니라 실험 가정이다.
WAIT_WEIGHT = 1.0        # 대기 1초당. 시간대가 열리기 전에 도착해 기다린 시간에 곱한다.
LATE_SECOND_COST = 5.0   # 지각 1초당(물품별). 물품 처리 완료가 마감보다 늦은 초를 물품마다 더해 곱한다.
LATE_STOP_COST = 600.0   # 지각 배송지 1곳당(건당). 한 곳에서 물품 여러 개가 늦어도 한 번만 더한다.
NEIGHBOURS = 12          # 이웃 목록 길이. SA 의 이동 대상과 ALNS 의 인접 제거에 쓴다.
POSITIONS = (10, 2)      # ALNS 재삽입: 이동시간 증가가 작은 위치 10곳과 무작위 위치 2곳만 정확히 평가한다.


class MultiProblem:
    """이동시간 표, 물품 처리 순서, 차량 정보를 번호 기준으로 준비한다."""

    def __init__(self, depot, vehicles, stops, travel,
                 wait_weight=WAIT_WEIGHT, late_second_cost=LATE_SECOND_COST, late_stop_cost=LATE_STOP_COST,
                 max_duration=None):
        """max_duration: 차량별 소요시간 상한(초). None 이면 기록된 경로 중 가장 긴 소요시간.
        vehicles: route_id, capacity_cm3, departure_offset_seconds, recorded_order 를 가진 사전 목록.
        stops: 배송지 ID -> volume_cm3, windows([시작, 마감, 서비스시간] 목록).
        travel: (출발 ID, 도착 ID) -> (초, 추정 여부)."""
        self.wait_weight, self.late_second_cost, self.late_stop_cost = wait_weight, late_second_cost, late_stop_cost
        self.ids = [depot] + sorted(stops)
        self.index = {name: number for number, name in enumerate(self.ids)}
        size = len(self.ids)
        self.n_stops = size - 1
        self.T = [[0.0] * size for _ in range(size)]
        self.estimated = [[False] * size for _ in range(size)]
        for i, origin in enumerate(self.ids):
            for j, destination in enumerate(self.ids):
                if i != j:
                    seconds, estimated = travel(origin, destination)
                    if not isfinite(seconds) or seconds < 0:
                        raise ValueError("이동시간은 0 이상의 유한한 값이어야 합니다.")
                    # 원자료는 0.1초 단위다. 추정값도 0.1초로 맞춰 PC 간 부동소수 차이를 없앤다.
                    self.T[i][j] = round(seconds, 1) if estimated else seconds
                    self.estimated[i][j] = estimated
        self.vehicle_ids = [vehicle["route_id"] for vehicle in vehicles]
        self.capacity = [vehicle["capacity_cm3"] for vehicle in vehicles]
        self.offset = [float(vehicle["departure_offset_seconds"]) for vehicle in vehicles]
        self.recorded = [[self.index[name] for name in vehicle["recorded_order"]] for vehicle in vehicles]
        self.home = [None] * size
        for number, order in enumerate(self.recorded):
            for stop in order:
                if self.home[stop] is not None:
                    raise ValueError("기록된 배정에 같은 배송지가 두 번 있습니다.")
                self.home[stop] = number
        if None in self.home[1:]:
            raise ValueError("기록된 배정에 없는 배송지가 있습니다.")
        self.volume, self.service, self.timed, self.deadline = [0.0] * size, [0.0] * size, [None] * size, [inf] * size
        for name, stop in stops.items():
            packages = []
            for start, end, service in stop["windows"]:
                if not isfinite(service) or service < 0:
                    raise ValueError("서비스 시간이 올바르지 않습니다.")
                packages.append((-inf if start is None else start, inf if end is None else end, service))
            # 마감이 빠른 물품부터 처리한다. 마감이 같으면 시작 시각, 그다음은 원자료 순서.
            packages.sort(key=lambda item: (item[1], item[0]))
            number = self.index[name]
            self.volume[number] = stop["volume_cm3"]
            self.service[number] = sum(item[2] for item in packages)
            self.deadline[number] = packages[0][1] if packages else inf
            if any(start != -inf or end != inf for start, end, _ in packages):
                self.timed[number] = tuple(packages)
        everyone = range(1, size)

        def nearest(stop, pool):
            ranked = sorted((self.T[stop][other] + self.T[other][stop], other) for other in pool if other != stop)
            return [other for _, other in ranked[:NEIGHBOURS]]
        self.near = [None] + [nearest(stop, everyone) for stop in everyone]
        self.near_home = [None] + [nearest(stop, self.recorded[self.home[stop]]) for stop in everyone]
        if max_duration is None:
            max_duration = max(self.route_duration(v, order) for v, order in enumerate(self.recorded))
        self.max_duration = max_duration

    @classmethod
    def from_station_day(cls, day, **weights):
        """dataset.StationDay 로 만든다. 이동시간은 day.travel() 로만 조회한다."""
        return cls(day.depot["id"], day.vehicles, day.stops, day.travel, **weights)

    def load(self, order):
        return sum(self.volume[stop] for stop in order)

    def route_duration(self, vehicle, order):
        """출발부터 마지막 배송 완료까지 걸리는 시간(이동 + 대기 + 서비스)."""
        T, service, timed = self.T, self.service, self.timed
        clock = self.offset[vehicle]
        previous = 0
        for stop in order:
            clock += T[previous][stop]
            if timed[stop] is None:
                clock += service[stop]
            else:
                for start, _, seconds in timed[stop]:
                    if clock < start:
                        clock = start
                    clock += seconds
            previous = stop
        return clock - self.offset[vehicle]

    def route_cost(self, vehicle, order):
        """탐색용 비용. route_metrics 와 같은 시계 규칙이다. 근무시간 상한을 넘으면 inf."""
        T, service, timed = self.T, self.service, self.timed
        clock = self.offset[vehicle]
        previous = 0
        travel = wait = late = 0.0
        late_stops = 0
        for stop in order:
            leg = T[previous][stop]
            travel += leg
            clock += leg
            packages = timed[stop]
            if packages is None:
                clock += service[stop]
            else:
                stop_late = False
                for start, end, seconds in packages:
                    if clock < start:
                        wait += start - clock
                        clock = start
                    clock += seconds
                    if clock > end:
                        late += clock - end
                        stop_late = True
                if stop_late:
                    late_stops += 1
            previous = stop
        if clock - self.offset[vehicle] > self.max_duration:
            return inf
        return travel + self.wait_weight * wait + self.late_second_cost * late + self.late_stop_cost * late_stops

    def route_metrics(self, vehicle, order, times=None):
        """한 차량의 이동·대기·지각·완료 시각. times 를 주면 그 이동시간 표로 채점한다."""
        T = self.T if times is None else times
        clock = self.offset[vehicle]
        previous = 0
        travel = wait = late = service_total = 0.0
        late_stops = late_packages = 0
        for stop in order:
            travel += T[previous][stop]
            clock += T[previous][stop]
            stop_late = False
            for start, end, seconds in self.timed[stop] or ((-inf, inf, self.service[stop]),):
                if clock < start:
                    wait += start - clock
                    clock = start
                clock += seconds
                service_total += seconds
                if clock > end:
                    late += clock - end
                    late_packages += 1
                    stop_late = True
            late_stops += stop_late
            previous = stop
        cost = travel + self.wait_weight * wait + self.late_second_cost * late + self.late_stop_cost * late_stops
        return {"cost": cost, "travel_seconds": travel, "wait_seconds": wait, "late_seconds": late,
                "late_stops": late_stops, "late_packages": late_packages, "service_seconds": service_total,
                "completion_seconds": clock if order else None, "duration_seconds": clock - self.offset[vehicle],
                "stops": len(order), "load_cm3": self.load(order)}

    def evaluate(self, routes, times=None):
        """모든 배송지가 정확히 한 번 들어갔고 용량과 근무시간 상한을 넘지 않는지 확인한 뒤 전체를 채점한다.
        times 를 준 재채점에서는 이동시간이 달라지므로 근무시간 상한을 검사하지 않는다."""
        visited = [stop for order in routes for stop in order]
        if len(routes) != len(self.capacity) or sorted(visited) != list(range(1, self.n_stops + 1)):
            raise ValueError("모든 배송지가 정확히 한 차량에 한 번씩 들어가야 합니다.")
        vehicles = [self.route_metrics(number, order, times) for number, order in enumerate(routes)]
        for number, item in enumerate(vehicles):
            if item["load_cm3"] > self.capacity[number]:
                raise ValueError(f"{self.vehicle_ids[number]}: 용량 초과")
            if times is None and item["duration_seconds"] > self.max_duration:
                raise ValueError(f"{self.vehicle_ids[number]}: 근무시간 상한 초과")
        total = {key: sum(item[key] for item in vehicles)
                 for key in ("cost", "travel_seconds", "wait_seconds", "late_seconds", "late_stops",
                             "late_packages", "service_seconds")}
        total["last_completion_seconds"] = max(item["completion_seconds"] for item in vehicles if item["stops"])
        total["longest_duration_seconds"] = max(item["duration_seconds"] for item in vehicles)
        total["vehicles_used"] = sum(item["stops"] > 0 for item in vehicles)
        total["vehicles"] = vehicles
        return total


def _copy(routes):
    return [order[:] for order in routes]


def greedy(problem, fixed=False):
    """마감 순으로 배송지를 꺼내어 용량이 남은 모든 차량의 모든 위치에 넣어 보고 가장 싼 곳에 넣는다.
    근무시간 상한을 넘기는 위치에는 넣지 않는다. fixed=True 면 기록된 차량에만 넣는다(배정 고정).

    상한 안에 넣을 위치가 하나도 없는 배송지는 소요시간이 가장 짧아지는 위치에 일단 넣고,
    모두 넣은 뒤 _shorten 으로 상한을 넘은 차량을 줄인다. 반환값은 (해, 처리 기록)."""
    count = len(problem.capacity)
    routes, costs, loads = [[] for _ in range(count)], [0.0] * count, [0.0] * count
    forced = 0
    for stop in sorted(range(1, problem.n_stops + 1), key=lambda s: (problem.deadline[s], s)):
        allowed = [vehicle for vehicle in ([problem.home[stop]] if fixed else range(count))
                   if loads[vehicle] + problem.volume[stop] <= problem.capacity[vehicle]]
        if not allowed:
            raise ValueError("용량 때문에 넣을 차량이 없는 배송지가 있습니다.")
        best = None
        for vehicle in allowed:
            order = routes[vehicle]
            if costs[vehicle] == inf:         # 이미 상한을 넘은 차량
                continue
            for position in range(len(order) + 1):
                cost = problem.route_cost(vehicle, order[:position] + [stop] + order[position:])
                if cost - costs[vehicle] < (inf if best is None else best[0]):
                    best = (cost - costs[vehicle], vehicle, position, cost)
        if best is None:
            forced += 1
            _, vehicle, position = min(
                (problem.route_duration(vehicle, routes[vehicle][:position] + [stop] + routes[vehicle][position:]),
                 vehicle, position) for vehicle in allowed for position in range(len(routes[vehicle]) + 1))
            cost = inf
        else:
            _, vehicle, position, cost = best
        routes[vehicle].insert(position, stop)
        costs[vehicle] = cost
        loads[vehicle] += problem.volume[stop]
    report = {"stops_without_position_within_limit": forced, **_shorten(problem, routes, loads, fixed)}
    return routes, report


def _shorten(problem, routes, loads, fixed):
    """근무시간 상한을 넘은 차량을 줄인다. (1) 같은 차량 안에서 이동시간이 가장 많이 줄어드는
    한 곳 옮기기를 반복한다. (2) 더 줄일 수 없는데도 넘으면, 배정 고정일 때는 그 차량을 기록된
    순서로 되돌리고, 아닐 때는 배송지를 하나씩 다른 차량의 가장 싼 자리로 보낸다."""
    T, limit = problem.T, problem.max_duration
    report = {"vehicles_over_limit_after_insertion": [], "relocations_within_vehicle": 0,
              "stops_sent_to_other_vehicle": 0, "vehicles_reset_to_recorded_order": []}
    for vehicle in range(len(routes)):
        if problem.route_duration(vehicle, routes[vehicle]) <= limit:
            continue
        report["vehicles_over_limit_after_insertion"].append(problem.vehicle_ids[vehicle])
        while (current := problem.route_duration(vehicle, routes[vehicle])) > limit:
            order = routes[vehicle]
            moves = []
            for i, stop in enumerate(order):
                before = order[i - 1] if i else 0
                after = order[i + 1] if i + 1 < len(order) else None
                saving = T[before][stop] + (T[stop][after] - T[before][after] if after is not None else 0.0)
                rest = order[:i] + order[i + 1:]
                previous = 0
                for position in range(len(rest) + 1):
                    following = rest[position] if position < len(rest) else None
                    extra = T[previous][stop] + (T[stop][following] - T[previous][following] if following is not None else 0.0)
                    if position != i and extra - saving < -1e-9:
                        moves.append((extra - saving, i, position))
                    if following is not None:
                        previous = following
            for _, i, position in sorted(moves):
                trial = order[:i] + order[i + 1:]
                trial.insert(position, order[i])
                if problem.route_duration(vehicle, trial) < current:
                    routes[vehicle] = trial
                    report["relocations_within_vehicle"] += 1
                    break
            else:
                break
        if problem.route_duration(vehicle, routes[vehicle]) <= limit:
            continue
        if fixed:
            routes[vehicle] = problem.recorded[vehicle][:]
            report["vehicles_reset_to_recorded_order"].append(problem.vehicle_ids[vehicle])
            continue
        while problem.route_duration(vehicle, routes[vehicle]) > limit:
            order = routes[vehicle]
            options = []
            for i, stop in enumerate(order):
                for other in range(len(routes)):
                    if other == vehicle or loads[other] + problem.volume[stop] > problem.capacity[other]:
                        continue
                    base = problem.route_cost(other, routes[other])
                    for position in range(len(routes[other]) + 1):
                        cost = problem.route_cost(other, routes[other][:position] + [stop] + routes[other][position:])
                        if cost < inf:
                            options.append((cost - base, i, other, position))
            if not options:
                raise ValueError("탐욕법이 근무시간 상한을 맞추지 못했습니다.")
            _, i, other, position = min(options)
            stop = order.pop(i)
            routes[other].insert(position, stop)
            loads[vehicle] -= problem.volume[stop]
            loads[other] += problem.volume[stop]
            report["stops_sent_to_other_vehicle"] += 1
    return report


def sa(problem, initial, iterations, seed, fixed=False, checkpoints=200):
    """한 배송지를 다른 배송지 옆으로 옮기거나 두 배송지를 맞바꾼다. 상대가 다른 차량에 있으면
    차량 간 이동·교환이 되고, 그때마다 용량을 검사한다. fixed=True 면 같은 차량 안에서만 고른다.

    반환값은 (최선 해, 기록). 기록은 (반복 번호, 그때까지의 최선 비용, 현재 비용) 목록이다."""
    rng = Random(seed)
    routes = _copy(initial)
    costs = [problem.route_cost(v, order) for v, order in enumerate(routes)]
    loads = [problem.load(order) for order in routes]
    where = [None] * (problem.n_stops + 1)
    for vehicle, order in enumerate(routes):
        for stop in order:
            where[stop] = vehicle
    total = sum(costs)
    if total == inf:
        raise ValueError("초기 해가 근무시간 상한을 넘습니다.")
    best, best_total, changed = _copy(routes), total, set()
    history = [(0, best_total, total)]
    n = problem.n_stops
    if iterations <= 0 or n < 2:
        return best, history
    near = problem.near_home if fixed else problem.near
    volume, capacity, route_cost = problem.volume, problem.capacity, problem.route_cost
    # 초기 온도는 배송지 하나당 초기 비용의 1/4 이고, 마지막 반복에서 그 1/100 이 된다.
    start_temperature = max(1.0, total / n * 0.25)
    every = max(1, iterations // checkpoints)
    for iteration in range(1, iterations + 1):
        if iteration % every == 1 or every == 1:
            if iteration > 1:
                history.append((iteration - 1, best_total, total))
        stop = rng.randrange(1, n + 1)
        a = where[stop]
        if rng.random() < 0.15 or not near[stop]:   # 가끔은 이웃이 아닌 임의의 상대를 고른다.
            other = routes[a][rng.randrange(len(routes[a]))] if fixed else rng.randrange(1, n + 1)
        else:
            other = near[stop][rng.randrange(len(near[stop]))]
        relocate = rng.random() < 0.6
        after = rng.random() < 0.5
        chance = rng.random()
        if stop == other:
            continue
        b = where[other]
        new_a = routes[a][:]
        new_b = None
        if relocate:
            new_a.remove(stop)
            if a == b:
                new_a.insert(new_a.index(other) + after, stop)
            else:
                if loads[b] + volume[stop] > capacity[b]:
                    continue
                new_b = routes[b][:]
                new_b.insert(new_b.index(other) + after, stop)
        elif a == b:
            i, j = new_a.index(stop), new_a.index(other)
            new_a[i], new_a[j] = other, stop
        else:
            if (loads[a] - volume[stop] + volume[other] > capacity[a]
                    or loads[b] - volume[other] + volume[stop] > capacity[b]):
                continue
            new_b = routes[b][:]
            new_a[new_a.index(stop)] = other
            new_b[new_b.index(other)] = stop
        cost_a = route_cost(a, new_a)
        delta = cost_a - costs[a]
        if new_b is not None:
            cost_b = route_cost(b, new_b)
            delta += cost_b - costs[b]
        temperature = start_temperature * 0.01 ** (iteration / iterations)
        if delta <= 0 or chance < exp(-delta / temperature):
            routes[a], costs[a] = new_a, cost_a
            changed.add(a)
            if new_b is not None:
                routes[b], costs[b] = new_b, cost_b
                changed.add(b)
                if relocate:
                    loads[a] -= volume[stop]
                    loads[b] += volume[stop]
                    where[stop] = b
                else:
                    loads[a] += volume[other] - volume[stop]
                    loads[b] += volume[stop] - volume[other]
                    where[stop], where[other] = b, a
            total = sum(costs)
            if total < best_total:
                best_total = total
                for vehicle in changed:
                    best[vehicle] = routes[vehicle][:]
                changed.clear()
    history.append((iterations, best_total, total))
    return best, history


def _destroy(problem, routes, count, method, rng):
    n = problem.n_stops
    if method == "random":
        return rng.sample(range(1, n + 1), count)
    if method == "nearby":
        anchor = rng.randrange(1, n + 1)
        return [anchor] + problem.near[anchor][:count - 1]
    # long_detour: 빼면 이동시간이 많이 줄어드는 배송지일수록 뽑힐 확률이 높다.
    T = problem.T
    ranked = []
    for order in routes:
        for i, stop in enumerate(order):
            previous = order[i - 1] if i else 0
            saving = T[previous][stop]
            if i + 1 < len(order):
                saving += T[stop][order[i + 1]] - T[previous][order[i + 1]]
            ranked.append((-saving, stop))
    ranked.sort()
    return [ranked.pop(int(rng.random() ** 3 * len(ranked)))[1] for _ in range(count)]


def _insertions(problem, routes, costs, loads, stop, rng, fixed):
    """넣을 수 있는 (비용 증가, 차량, 위치) 목록을 싼 순으로. 용량이 모자란 차량은 뺀다."""
    T = problem.T
    candidates = []
    for vehicle in ([problem.home[stop]] if fixed else range(len(routes))):
        if loads[vehicle] + problem.volume[stop] > problem.capacity[vehicle]:
            continue
        previous = 0
        for position, following in enumerate(routes[vehicle]):
            candidates.append((T[previous][stop] + T[stop][following] - T[previous][following], vehicle, position))
            previous = following
        candidates.append((T[previous][stop], vehicle, len(routes[vehicle])))
    if len(candidates) > sum(POSITIONS):
        candidates.sort()
        candidates = candidates[:POSITIONS[0]] + rng.sample(candidates[POSITIONS[0]:], POSITIONS[1])
    choices = ((problem.route_cost(vehicle, routes[vehicle][:position] + [stop] + routes[vehicle][position:])
                - costs[vehicle], vehicle, position) for _, vehicle, position in candidates)
    return sorted(choice for choice in choices if choice[0] < inf)     # 근무시간 상한을 넘기는 자리는 뺀다.


def _repair(problem, routes, costs, loads, removed, method, rng, fixed):
    """deadline_first 는 마감 순으로 하나씩, regret_2 는 최선과 차선의 차가 큰 배송지부터 넣는다.
    용량과 근무시간 상한 안에 넣을 자리가 없는 배송지가 생기면 False."""
    remaining = sorted(removed, key=lambda s: (problem.deadline[s], s))
    while remaining:
        chosen, largest = None, -inf
        for stop in (remaining[:1] if method == "deadline_first" else remaining):
            choices = _insertions(problem, routes, costs, loads, stop, rng, fixed)
            if not choices:
                return False
            regret = choices[1][0] - choices[0][0] if len(choices) > 1 else 0.0
            if regret > largest:
                largest, chosen = regret, (stop, *choices[0])
        stop, increase, vehicle, position = chosen
        routes[vehicle].insert(position, stop)
        costs[vehicle] += increase
        loads[vehicle] += problem.volume[stop]
        remaining.remove(stop)
    return True


def alns(problem, initial, iterations, seed, fixed=False, checkpoints=200):
    """배송지 몇 곳을 빼고 다시 넣는다. 제거·복구 방법은 성과에 따라 가중 추출한다.
    fixed=True 면 뺀 배송지를 원래 차량에만 다시 넣는다. 반환값은 sa 와 같다."""
    rng = Random(seed)
    routes = _copy(initial)
    costs = [problem.route_cost(v, order) for v, order in enumerate(routes)]
    loads = [problem.load(order) for order in routes]
    total = sum(costs)
    if total == inf:
        raise ValueError("초기 해가 근무시간 상한을 넘습니다.")
    best, best_total = _copy(routes), total
    history = [(0, best_total, total)]
    n = problem.n_stops
    if iterations <= 0 or n < 2:
        return best, history
    removal = ("random", "long_detour", "nearby")
    repair = ("deadline_first", "regret_2")
    remove_weights, repair_weights = [1.0] * len(removal), [1.0] * len(repair)
    largest = min(n, max(2, min(8, round(n * 0.1))))    # 한 번에 2~8곳을 뺀다.
    every = max(1, iterations // checkpoints)
    for iteration in range(1, iterations + 1):
        i = rng.choices(range(len(removal)), weights=remove_weights)[0]
        j = rng.choices(range(len(repair)), weights=repair_weights)[0]
        removed = _destroy(problem, routes, rng.randint(2, largest), removal[i], rng)
        gone = set(removed)
        candidate, candidate_costs, candidate_loads = _copy(routes), costs[:], loads[:]
        for vehicle, order in enumerate(candidate):
            if not gone.isdisjoint(order):
                candidate[vehicle] = [stop for stop in order if stop not in gone]
                candidate_costs[vehicle] = problem.route_cost(vehicle, candidate[vehicle])
                candidate_loads[vehicle] = problem.load(candidate[vehicle])
        reward = 0.3
        # 배송지를 빼서 오히려 상한을 넘는 드문 경우와 넣을 자리가 없는 경우는 이번 반복을 버린다.
        if inf not in candidate_costs and _repair(problem, candidate, candidate_costs, candidate_loads,
                                                  removed, repair[j], rng, fixed):
            # 삽입 때 더한 증가분의 누적 오차를 없애려고 바뀐 차량의 비용을 다시 계산한다.
            for vehicle in range(len(candidate)):
                if candidate[vehicle] != routes[vehicle]:
                    candidate_costs[vehicle] = problem.route_cost(vehicle, candidate[vehicle])
                    candidate_loads[vehicle] = problem.load(candidate[vehicle])
                else:
                    candidate_costs[vehicle], candidate_loads[vehicle] = costs[vehicle], loads[vehicle]
            candidate_total = sum(candidate_costs)
            # 최선 해보다 조금 나쁜 후보도 받는다. 허용 폭은 최선 비용의 1%에서 0으로 줄어든다.
            tolerance = best_total * 0.01 * (1 - iteration / iterations)
            if candidate_total <= total or candidate_total <= best_total + tolerance:
                if candidate_total < best_total:
                    best, best_total, reward = _copy(candidate), candidate_total, 5.0
                elif candidate_total < total:
                    reward = 3.0
                else:
                    reward = 1.0
                routes, costs, loads, total = candidate, candidate_costs, candidate_loads, candidate_total
        remove_weights[i] = 0.8 * remove_weights[i] + 0.2 * reward
        repair_weights[j] = 0.8 * repair_weights[j] + 0.2 * reward
        if iteration % every == 0 and iteration < iterations:
            history.append((iteration, best_total, total))
    history.append((iterations, best_total, total))
    return best, history
