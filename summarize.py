"""results/multi 아래 결과(baseline, greedy, sa, alns)를 모아 SUMMARY.md 를 만든다.

python summarize.py                 results/multi/SUMMARY.md 생성
python summarize.py --root 폴더     다른 결과 폴더 요약

아직 돌리지 않은 알고리즘은 "결과 없음"으로 표시한다. matplotlib 이 있으면 수렴 그래프를
convergence.png 로 저장하고, 없으면 표만 만든다. 표준 라이브러리만으로 동작한다.
"""
import argparse
import contextlib
import csv
import io
import json
import statistics
from pathlib import Path


ROOT = Path(__file__).resolve().parent
NAMES = {"recorded": "기록 그대로", "greedy": "탐욕법", "sa": "SA", "alns": "ALNS"}
ROWS = [("A", "recorded"), ("B", "greedy"), ("B", "sa"), ("B", "alns"), ("C", "greedy"), ("C", "sa"), ("C", "alns"),
        ("C2", "sa"), ("C2", "alns")]
FOLDER = {"recorded": "baseline", "greedy": "greedy", "sa": "sa", "alns": "alns"}
MISSING = "결과 없음"


def read_csv(path):
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as source:
        return list(csv.DictReader(source))


def load(root):
    data = {}
    for name in ("baseline", "greedy", "sa", "alns"):
        folder = root / name
        if (folder / "settings.json").exists():
            data[name] = {"settings": json.loads((folder / "settings.json").read_text(encoding="utf-8")),
                          **{part: read_csv(folder / f"{part}.csv") for part in ("runs", "vehicles", "moves", "history")}}
    return data


def runs(data, scenario, method, part="runs"):
    rows = data.get(FOLDER[method], {}).get(part, [])
    return [row for row in rows if row["scenario"] == scenario and row["method"] == method]


def values(rows, key, scale=1.0):
    return [float(row[key]) * scale for row in rows if row[key] not in ("", None)]


def both(rows, key, scale=1.0, digits=0):
    """시드 평균 / 중앙값. 실행이 하나면 값 하나만."""
    numbers = values(rows, key, scale)
    if not numbers:
        return MISSING
    text = f"{{:,.{digits}f}}"
    if len(numbers) == 1:
        return text.format(numbers[0])
    return f"{text.format(statistics.mean(numbers))} / {text.format(statistics.median(numbers))}"


def table(header, rows):
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" if i < 2 else "---:" for i in range(len(header))) + "|"]
    return "\n".join(lines + ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows])


def mean_cost(data, scenario, method, key="cost"):
    numbers = values(runs(data, scenario, method), key)
    return statistics.mean(numbers) if numbers else None


def effect(data, method, target, key="cost"):
    """B 대비 target(C 또는 C2)의 평균 변화율과, 같은 시드에서 target 이 더 좋은 횟수."""
    before, after = mean_cost(data, "B", method, key), mean_cost(data, target, method, key)
    if before is None or after is None:
        return None
    b = {row["seed"]: float(row[key]) for row in runs(data, "B", method)}
    wins = sum(float(row[key]) < b[row["seed"]] for row in runs(data, target, method) if row["seed"] in b)
    return before, after, 100 * (after / before - 1), wins, len(runs(data, target, method))


def ranking(data, scenario):
    costs = [(mean_cost(data, scenario, method), NAMES[method]) for method in ("greedy", "sa", "alns")
             if (scenario, method) in ROWS and mean_cost(data, scenario, method) is not None]
    return " < ".join(f"{name} {cost:,.0f}" for cost, name in sorted(costs)), [name for _, name in sorted(costs)]


def history_points(data, scenario, method, fractions):
    """시드별 기록에서 반복 비율별 최선 비용을 뽑아 평균낸다."""
    by_seed = {}
    for row in runs(data, scenario, method, "history"):
        by_seed.setdefault(row["seed"], []).append((int(row["at_iteration"]), float(row["best_cost"])))
    if not by_seed:
        return None
    result = []
    for fraction in fractions:
        picks = []
        for points in by_seed.values():
            target = points[-1][0] * fraction
            picks.append(min(points, key=lambda point: abs(point[0] - target))[1])
        result.append(statistics.mean(picks))
    return result


def curve(data, scenario, method):
    by_iteration = {}
    for row in runs(data, scenario, method, "history"):
        by_iteration.setdefault(int(row["at_iteration"]), []).append(float(row["best_cost"]))
    return sorted((at, statistics.mean(costs)) for at, costs in by_iteration.items())


def plot(data, path):
    try:
        with contextlib.redirect_stderr(io.StringIO()):     # 깨진 설치가 내는 긴 경고를 숨긴다.
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
    except Exception:           # 설치돼 있지 않거나 불러올 수 없으면 표만 만든다.
        return False
    methods = [m for m in ("sa", "alns") if m in data]
    if not methods:
        return False
    figure, axes = plt.subplots(1, len(methods), figsize=(6 * len(methods), 4), squeeze=False)
    for axis, method in zip(axes[0], methods):
        for scenario in ("B", "C", "C2"):
            points = curve(data, scenario, method)
            if points:
                axis.plot([p[0] for p in points], [p[1] for p in points], label=scenario)
        axis.set_title(f"{method.upper()}: best cost, mean over seeds")
        axis.set_xlabel("iteration")
        axis.set_ylabel("cost")
        axis.legend()
    figure.tight_layout()
    figure.savefig(path, dpi=120)
    plt.close(figure)
    return True


def summarize(root):
    data = load(root)
    if "baseline" not in data:
        raise SystemExit(f"{root} 에 baseline 결과가 없습니다. 먼저 run_multi.py 를 실행하세요.")
    base = data["baseline"]["settings"]
    weights = base["weights"]
    out = ["# 다차량 실험 요약", "",
           "이 파일은 `python summarize.py` 가 결과 폴더에서 자동으로 만든다. 직접 고치지 않는다.", "",
           "- A: 기록된 배정 + 기록된 순서",
           "- B: 기록된 배정 + 알고리즘 순서 (배정 고정)",
           "- C: 알고리즘 배정 + 알고리즘 순서. 탐욕법이 처음부터 배정하고 SA·ALNS 는 그 해에서 출발한다.",
           "- C2: C 와 같지만 SA·ALNS 가 B 의 탐욕 해(기록된 배정)에서 출발한다.", "",
           "## 설정", "",
           f"- 비용 = 이동 + {weights['wait_weight']:g} × 대기 + {weights['late_second_cost']:g} × 지각 초(물품별) "
           f"+ {weights['late_stop_cost']:g} × 지각 배송지 수",
           f"- 근무시간 상한(차량별 출발~마지막 배송 완료): {base['max_duration_seconds']:,.1f}초 = "
           f"{base['max_duration_seconds'] / 3600:.2f}시간 ({base['max_duration_source']})"]
    for name in ("greedy", "sa", "alns"):
        if name not in data:
            out.append(f"- {NAMES[name]}: {MISSING}")
        elif name == "greedy":
            out.append("- 탐욕법: 결과 있음")
        else:
            settings = data[name]["settings"]
            out.append(f"- {NAMES[name]}: 반복 {settings['iterations']:,}회, 시드 {', '.join(map(str, settings['seeds']))}")
    out += ["", "표의 값은 `시드 평균 / 중앙값`이다. 실행이 하나뿐인 행(A, 탐욕법)은 값 하나만 적는다.", ""]

    out += ["## 비교표", ""]
    rows = []
    for scenario, method in ROWS:
        r = runs(data, scenario, method)
        if not r:
            rows.append([scenario, NAMES[method]] + [MISSING] + [""] * 8)
            continue
        rows.append([scenario, NAMES[method], len(r), both(r, "cost"), both(r, "travel_seconds", 1 / 60, 1),
                     both(r, "wait_seconds", 1 / 60, 1), both(r, "late_stops", 1, 1), both(r, "late_packages", 1, 1),
                     both(r, "late_minutes", 1, 1), both(r, "vehicles_used", 1, 1),
                     both(r, "longest_duration_seconds", 1 / 3600, 2)])
    out += [table(["시나리오", "알고리즘", "실행 수", "비용", "이동(분)", "대기(분)", "지각 배송지(곳)", "지각 물품(개)",
                   "지각(분)", "쓴 차량(대)", "가장 긴 소요(시간)"], rows), ""]

    labels = list(base["vehicles"])
    out += ["## 차량별 마지막 배송 완료 시각", "",
            "기준 시각(가장 이른 출발) 이후 시간이고 괄호 안은 배송지 수다. 시드 평균이다.", ""]
    rows = []
    for scenario, method in ROWS:
        v = runs(data, scenario, method, "vehicles")
        if not v:
            rows.append([scenario, NAMES[method]] + [MISSING] + [""] * (len(labels) - 1))
            continue
        cells = []
        for label in labels:
            mine = [row for row in v if row["vehicle"] == label]
            done = values(mine, "completion_seconds", 1 / 3600)
            cells.append(f"{statistics.mean(done):.2f} ({statistics.mean(values(mine, 'stops')):.0f})" if done
                         else f"미사용 ({statistics.mean(values(mine, 'stops')):.0f})")
        rows.append([scenario, NAMES[method]] + cells)
    out += [table(["시나리오", "알고리즘"] + labels, rows), ""]

    out += ["## 배정 효과", "",
            "같은 알고리즘의 B 와 비교한 평균 비용 변화다. 음수면 배정을 바꾼 쪽이 싸다.", ""]
    rows = []
    for method in ("greedy", "sa", "alns"):
        for target in ("C2", "C"):
            if (target, method) not in ROWS:
                continue
            e = effect(data, method, target)
            if e is None:
                rows.append([NAMES[method], f"B → {target}", MISSING, "", "", ""])
            else:
                rows.append([NAMES[method], f"B → {target}", f"{e[0]:,.0f}", f"{e[1]:,.0f}", f"{e[2]:+.2f}%",
                             f"{e[3]}/{e[4]}" if method != "greedy" else "-"])
    out += [table(["알고리즘", "비교", "B 평균 비용", "대상 평균 비용", "변화", "대상이 더 싼 시드"], rows), ""]

    out += ["## 옮겨진 배송지와 추정 구간", "",
            "`차량 짝지은 뒤`는 차량 이름만 바뀐 경우를 빼려고, 원래 경로와 가장 많이 겹치도록 차량을 짝지은 뒤 센 값이다. "
            f"경계 배송지는 원래 다른 경로의 배송지가 500 m 안에 있던 곳으로 전체 {base['boundary_stops']}곳이다.", ""]
    rows = []
    for scenario, method in ROWS:
        if scenario in ("C", "C2"):
            r = runs(data, scenario, method)
            rows.append([scenario, NAMES[method]] + ([MISSING, "", "", "", ""] if not r else [
                both(r, "moved_stops", 1, 1), both(r, "moved_stops_best_match", 1, 1),
                both(r, "moved_boundary_stops", 1, 1), both(r, "estimated_legs", 1, 1),
                both(r, "estimated_time_share", 100, 2) + "%"]))
    out += [table(["시나리오", "알고리즘", "옮겨진 배송지", "차량 짝지은 뒤", "그중 경계 배송지", "추정 구간 수",
                   "추정 구간의 이동시간 비중"], rows), ""]
    for method in ("sa", "alns"):
        moves = runs(data, "C2", method, "moves")
        seeds = len(runs(data, "C2", method))
        if not seeds:
            out.append(f"- C2 {NAMES[method]} 이동 방향: {MISSING}")
            continue
        pairs = {}
        for row in moves:
            pairs[row["from_vehicle"], row["to_vehicle"]] = pairs.get((row["from_vehicle"], row["to_vehicle"]), 0) + float(row["stops"])
        top = sorted(pairs.items(), key=lambda item: -item[1])[:8]
        out.append(f"- C2 {NAMES[method]} 이동 방향(시드당 평균, 많은 순): "
                   + (", ".join(f"{a}→{b} {count / seeds:.1f}곳" for (a, b), count in top) or "이동 없음"))
    out.append("")

    out += ["## 전부 추정 이동시간으로 다시 채점", "",
            "같은 해를 모든 구간이 멱함수 추정값인 이동시간 표로 다시 채점했다. 근무시간 상한은 이 재채점에 적용하지 않는다.", ""]
    rows = []
    for scenario, method in ROWS:
        r = runs(data, scenario, method)
        rows.append([scenario, NAMES[method]] + ([MISSING] + [""] * 6 if not r else [
            both(r, "cost"), both(r, "allest_cost"), both(r, "travel_seconds", 1 / 60, 1),
            both(r, "allest_travel_seconds", 1 / 60, 1), both(r, "allest_late_stops", 1, 1),
            both(r, "allest_late_minutes", 1, 1), both(r, "allest_longest_duration_seconds", 1 / 3600, 2)]))
    out += [table(["시나리오", "알고리즘", "원래 비용", "전부 추정 비용", "원래 이동(분)", "전부 추정 이동(분)",
                   "전부 추정 지각 배송지", "전부 추정 지각(분)", "전부 추정 가장 긴 소요(시간)"], rows), ""]
    rows = []
    for method in ("greedy", "sa", "alns"):
        for target in ("C2", "C"):
            if (target, method) in ROWS:
                real, cost, travel = (effect(data, method, target, key) for key in ("cost", "allest_cost", "allest_travel_seconds"))
                rows.append([NAMES[method], f"B → {target}"] + ([MISSING, "", "", ""] if real is None else [
                    f"{real[2]:+.2f}%", f"{cost[2]:+.2f}%", f"{travel[2]:+.2f}%",
                    f"{cost[3]}/{cost[4]}" if method != "greedy" else "-"]))
    out += [table(["알고리즘", "비교", "원래 채점 비용 변화", "전부 추정 비용 변화", "전부 추정 이동시간 변화",
                   "전부 추정 비용에서 대상이 더 싼 시드"], rows), ""]

    out += ["## 수렴", "", "반복 진행 비율별 최선 비용(시드 평균)이다. 마지막 열은 마지막 10% 구간에서 줄어든 비율이다.", ""]
    fractions = (0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0)
    rows = []
    for scenario, method in ROWS:
        if method in ("sa", "alns"):
            points = history_points(data, scenario, method, fractions)
            rows.append([scenario, NAMES[method]] + ([MISSING] + [""] * 7 if points is None else
                        [f"{p:,.0f}" for p in points] + [f"{100 * (points[-2] / points[-1] - 1):.3f}%"]))
    out += [table(["시나리오", "알고리즘"] + [f"{int(f * 100)}%" for f in fractions] + ["마지막 10% 개선"], rows), ""]
    if plot(data, root / "convergence.png"):
        out += ["![수렴 그래프](convergence.png)", ""]
    else:
        out += ["수렴 그래프: matplotlib 을 불러올 수 없어 표만 만들었다.", ""]

    def limit(level):
        return tuple(level[m]["settings"]["iterations"] if m in level else None for m in ("sa", "alns"))
    levels = [(load(folder), folder.name) for folder in sorted((root / "limits").iterdir())
              if (folder / "baseline").exists()] if (root / "limits").exists() else []
    if levels:
        # 이 폴더의 본 실험도 한 단계로 넣는다.
        levels = sorted(levels + [(data, "본 실험")], key=lambda item: [x or 0 for x in limit(item[0])])
        out += ["## 반복 한도 비교", "",
                "한도를 두 배씩 늘렸을 때의 평균 비용이다. 괄호 안은 바로 앞 한도 대비 변화다.", ""]
        rows = []
        for scenario, method in ROWS:
            if method in ("sa", "alns"):
                cells, previous = [], None
                for level, _ in levels:
                    cost = mean_cost(level, scenario, method)
                    cells.append(MISSING if cost is None else f"{cost:,.0f}" + (
                        "" if previous is None else f" ({100 * (cost / previous - 1):+.2f}%)"))
                    previous = cost
                rows.append([scenario, NAMES[method]] + cells)
        header = [" / ".join(f"{NAMES[m]} {level[m]['settings']['iterations']:,}" for m in ("sa", "alns") if m in level)
                  for level, _ in levels]
        out += [table(["시나리오", "알고리즘"] + header, rows), ""]

    variants = sorted(folder for folder in (root / "sensitivity").iterdir() if (folder / "baseline").exists()) \
        if (root / "sensitivity").exists() else []
    if variants:
        out += ["## 민감도", "", "가중치를 바꿔 다시 돌린 결과다. 순위는 평균 비용이 낮은 순이다.", ""]
        rows = []
        loaded = [(folder.name, load(folder)) for folder in variants]
        # 기본 가중치 쪽은 민감도 실행과 반복 횟수가 같은 결과를 쓴다(없으면 본 실험).
        same = [level for level, _ in levels if limit(level) == limit(loaded[0][1])]
        default = same[0] if same else data
        reference = {scenario: ranking(default, scenario)[1] for scenario in ("B", "C", "C2")}
        for name, variant in [("기본", default)] + loaded:
            w = variant["baseline"]["settings"]["weights"]
            for scenario in ("B", "C", "C2"):
                text, order = ranking(variant, scenario)
                rows.append([name, f"대기 {w['wait_weight']:g}, 지각 초 {w['late_second_cost']:g}, 지각 배송지 {w['late_stop_cost']:g}",
                             " / ".join(f"{x:,}" for x in limit(variant) if x), scenario, text or MISSING,
                             "-" if name == "기본" or not order else ("같음" if order == reference[scenario] else "바뀜")])
        out += [table(["설정", "가중치", "반복(SA / ALNS)", "시나리오", "평균 비용 순위", "기본 대비 순위"], rows), ""]
        rows = []
        for name, variant in [("기본", default)] + loaded:
            for method in ("greedy", "sa", "alns"):
                for target in ("C2", "C"):
                    if (target, method) in ROWS:
                        e = effect(variant, method, target)
                        rows.append([name, NAMES[method], f"B → {target}"] + ([MISSING, ""] if e is None else
                                    [f"{e[2]:+.2f}%", f"{e[3]}/{e[4]}" if method != "greedy" else "-"]))
        out += [table(["설정", "알고리즘", "비교", "평균 비용 변화", "대상이 더 싼 시드"], rows), ""]

    (root / "SUMMARY.md").write_text("\n".join(out), encoding="utf-8")
    print("요약:", root / "SUMMARY.md")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=ROOT / "results" / "multi")
    summarize(parser.parse_args().root)
