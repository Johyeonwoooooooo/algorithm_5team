# 결과 폴더 안내

현재 실험(다차량 배정 + 순서)의 결과다. 해석은 [docs/RESULTS_MULTI.md](../docs/RESULTS_MULTI.md), 한눈에 보는 표는 [SUMMARY.md](SUMMARY.md)에 있다. 이전 실험의 결과는 `v1/results/`에 있다.

## 폴더

| 폴더·파일 | 내용 | 만드는 명령 |
|---|---|---|
| `SUMMARY.md` | 아래 결과를 모은 요약. 자동 생성물이라 직접 고치지 않는다 | `python summarize.py` |
| `baseline/` | A: 기록된 배정 + 기록된 순서. 모든 알고리즘이 공통으로 쓴다 | `run_multi.py` (없을 때만 새로 쓴다) |
| `greedy/` | 탐욕법의 B, C | `python run_multi.py --algo greedy` |
| `sa/` | SA의 B, C, C2. 반복 8,000,000회, 시드 5개 | `python run_multi.py --algo sa` |
| `alns/` | ALNS의 B, C, C2. 반복 80,000회, 시드 5개 | `python run_multi.py --algo alns` |
| `extra/limits/` | 반복 한도를 바꿔 본 결과. 폴더 이름이 한도다(`sa2000000_alns20000` = SA 200만, ALNS 2만) | `run_multi.py --output ... --sa-iterations ... --alns-iterations ...` |
| `extra/sensitivity/` | 가중치를 바꿔 본 결과. `wait0`(대기 가중치 0), `penalty_half`(벌점 절반), `penalty_double`(벌점 두 배) | `run_multi.py --output ... --wait-weight ...` 등 |

`extra/` 아래의 폴더 하나하나는 이 폴더와 같은 구조(`baseline/`, `greedy/`, `sa/`, `alns/`)다.

시나리오 이름:

- **A** 기록된 배정 + 기록된 순서
- **B** 기록된 배정 + 알고리즘 순서 (배정 고정)
- **C** 알고리즘 배정 + 알고리즘 순서. 탐욕법이 처음부터 배정하고 SA·ALNS는 그 해에서 출발한다
- **C2** C와 같지만 SA·ALNS가 B의 탐욕 해(기록된 배정)에서 출발한다

## 알고리즘 폴더 안의 파일

| 파일 | 한 행이 뜻하는 것 | 있는 폴더 |
|---|---|---|
| `runs.csv` | 실행 하나(시나리오 × 시드) | 전부 |
| `vehicles.csv` | 실행 하나의 차량 한 대 | 전부 |
| `moves.csv` | 실행 하나에서 원래 차량 → 새 차량 한 쌍 | greedy, sa, alns |
| `history.csv` | 실행 하나의 중간 기록 한 지점 | sa, alns |
| `solutions.jsonl` | 실행 하나의 해(차량별 배송지 ID 순서) | 전부 |
| `settings.json` | 그 폴더를 만든 설정 | 전부 |

시간 단위는 따로 적지 않으면 초다. 시각은 기준 시각(고른 경로 중 가장 이른 출발, 2018-08-03 16:26:20 UTC) 이후의 초다.

### 모든 CSV에 공통인 열

| 열 | 뜻 |
|---|---|
| `scenario` | A, B, C, C2 |
| `method` | `recorded`(기록 그대로), `greedy`, `sa`, `alns` |
| `seed` | 난수 시드. 기록 그대로와 탐욕법은 비어 있다 |
| `iterations` | 반복 횟수 한도. SA는 이동 시도 횟수, ALNS는 제거·복구 횟수 |

### runs.csv

| 열 | 뜻 |
|---|---|
| `initial_cost` | 출발점(탐욕 해)의 비용. SA·ALNS만 |
| `cost` | 비용 = 이동 + 대기 가중치 × 대기 + 지각 초당 벌점 × 지각 초 + 지각 건당 벌점 × 지각 배송지 수 |
| `travel_seconds` | 6대의 이동시간 합 |
| `wait_seconds` | 시간대가 열리기 전에 도착해 기다린 시간의 합 |
| `late_stops` | 물품이 하나라도 마감보다 늦은 배송지 수 |
| `late_packages` | 마감보다 늦은 물품 수 |
| `late_minutes` | 물품별 지각 시간의 합(분) |
| `service_seconds` | 서비스시간의 합. 배정과 순서를 바꿔도 같다 |
| `last_completion_seconds` | 가장 늦게 끝난 차량의 마지막 배송 완료 시각 |
| `longest_duration_seconds` | 차량별 소요시간(출발~마지막 배송 완료) 중 가장 긴 값. 근무시간 상한 이하여야 한다 |
| `vehicles_used` | 배송지가 하나라도 실린 차량 수 |
| `moved_stops` | 기록된 차량과 다른 차량에 실린 배송지 수 |
| `moved_stops_best_match` | 차량 이름만 바뀐 경우를 빼려고, 원래 경로와 가장 많이 겹치도록 차량을 짝지은 뒤 센 값 |
| `moved_boundary_stops` | 옮겨진 배송지 중 경계 배송지(원래 다른 경로의 배송지가 500m 안에 있던 곳) 수 |
| `estimated_legs` | 추정 이동시간을 쓴 구간(서로 다른 원래 경로의 배송지 사이) 수 |
| `estimated_leg_seconds` | 그 구간들의 이동시간 합 |
| `estimated_time_share` | 그 합이 전체 이동시간에서 차지하는 비율(0~1) |
| `allest_cost` | 같은 해를 모든 구간이 추정값인 이동시간 표로 다시 채점한 비용 |
| `allest_travel_seconds`, `allest_wait_seconds`, `allest_late_stops`, `allest_late_minutes`, `allest_longest_duration_seconds` | 그 재채점의 이동, 대기, 지각 배송지 수, 지각(분), 가장 긴 소요시간. 재채점에는 근무시간 상한을 적용하지 않는다 |
| `runtime_seconds` | 계산시간(탐욕 출발점 생성 + 탐색). PC마다 다르다 |

### vehicles.csv

| 열 | 뜻 |
|---|---|
| `vehicle` | V1~V6. 출발이 이른 순서다 |
| `route_id` | 그 차량의 원래 경로 ID |
| `departure_offset_seconds` | 출발 시각. 차량의 시계가 여기서 시작한다 |
| `stops` | 실린 배송지 수 |
| `load_share` | 실린 부피 ÷ 차량 용량(0~1) |
| `cost`, `travel_seconds`, `wait_seconds`, `late_stops`, `late_packages`, `late_minutes` | runs.csv와 같은 뜻의 차량별 값 |
| `duration_seconds` | 출발부터 마지막 배송 완료까지 걸린 시간 |
| `completion_seconds` | 마지막 배송 완료 시각. 배송지가 없으면 비어 있다 |

### moves.csv

| 열 | 뜻 |
|---|---|
| `from_vehicle` | 기록된 차량 |
| `to_vehicle` | 해에서 실린 차량 |
| `stops` | 그렇게 옮겨진 배송지 수 |
| `boundary_stops` | 그중 경계 배송지 수 |

옮겨진 배송지가 없는 실행(B)은 행이 없다.

### history.csv

| 열 | 뜻 |
|---|---|
| `at_iteration` | 기록한 시점의 반복 번호. 0은 출발점이다 |
| `best_cost` | 그때까지 찾은 가장 낮은 비용 |
| `current_cost` | 그 시점에 탐색이 들고 있던 해의 비용 |

실행 하나당 200개 남짓의 지점을 같은 간격으로 기록한다.

### solutions.jsonl

한 줄이 실행 하나다. `routes`는 차량 순서(V1~V6)대로 놓인 목록이고, 각 목록은 그 차량이 방문하는 배송지 ID를 순서대로 담는다. 거점은 들어 있지 않다. 배송지 ID는 `data/station_day/station_day.json`의 것과 같다.

### settings.json

| 항목 | 뜻 |
|---|---|
| `weights` | `wait_weight`(대기 1초당), `late_second_cost`(물품별 지각 1초당), `late_stop_cost`(지각 배송지 1곳당) |
| `max_duration_seconds`, `max_duration_source` | 근무시간 상한과 그 출처(기록된 가장 긴 경로 또는 인자) |
| `vehicles` | V1~V6과 원래 경로 ID의 대응 |
| `boundary_stops` | 경계 배송지 수 |
| `data_sha256` | 실행에 쓴 데이터 파일의 해시. 검산 때 대조한다 |
| `algo`, `iterations`, `seeds` | 알고리즘, 반복 횟수, 시드 |
| `neighbours`, `alns_positions` | 이웃 목록 길이, ALNS가 재삽입 때 정확히 평가하는 위치 수(가까운 곳, 무작위) |
| `greedy_report` | 탐욕법이 근무시간 상한을 맞추려고 한 처리의 기록(B와 C 각각) |
| `solver_sha256`, `python`, `platform` | 솔버 파일 해시와 실행 환경. PC마다 다를 수 있다 |

## 검산

```shell
python check_multi.py --results results
python check_multi.py --results results/extra/limits/sa2000000_alns20000
```

모든 해를 별도 코드로 다시 계산해 이 폴더의 값과 대조한다.
