# 배송 실험: 차량 배정과 방문 순서

Amazon 2021 Last Mile Routing Challenge의 공개 자료로, 어느 차에 어느 배송지를 실을지와 방문 순서를 함께 푼다. 대상은 DLA7 스테이션 2018-08-03의 인접 경로 6대, 배송지 923곳이다. 탐욕법, SA, ALNS 세 알고리즘을 비교한다.

- 결과와 해석: [docs/RESULTS_MULTI.md](docs/RESULTS_MULTI.md)
- 한눈에 보는 표: [results/SUMMARY.md](results/SUMMARY.md)
- 결과 파일과 열 이름: [results/README.md](results/README.md)
- 데이터 설명: [docs/DATASET_CHANGES.md](docs/DATASET_CHANGES.md)
- 담당 결과 양식: [docs/담당결과_양식.md](docs/담당결과_양식.md)
- 이전 실험(한 차량의 방문 순서만): [v1/README.md](v1/README.md)

## 실행 방법

Python 3.10 이상이면 된다. 추가 패키지는 필요 없다. 팀원은 각자 맡은 알고리즘 하나만 돌린다. 명령은 모두 이 폴더(저장소 맨 위)에서 실행한다.

**1. 받기**

```shell
git clone https://github.com/Johyeonwoooooooo/algorithm_5team.git
cd algorithm_5team
```

이미 받았다면 `git pull`만 한다.

**2. 검증**

```shell
python check_multi.py
```

손계산 예제(차 2대, 배송지 5곳)와 같은 시드 재실행 검사가 통과해야 한다. 1분 안에 끝난다.

**3. 자기 알고리즘 실행**

```shell
python run_multi.py --algo sa        # SA 담당
python run_multi.py --algo alns      # ALNS 담당
python run_multi.py --algo greedy    # 탐욕법 담당
```

- 결과는 `results/sa/`처럼 알고리즘 이름의 폴더에 저장된다.
- 기본 설정은 SA 8,000,000회, ALNS 80,000회, 시드 11·29·47·61·83이다. 12코어 PC에서 실행 하나에 SA 5~7분, ALNS 8~17분이 걸렸고, 세 알고리즘을 함께 돌리면 29분이 걸렸다.
- 동작만 먼저 보려면 `python run_multi.py --algo sa --sa-iterations 200000 --output try_multi`처럼 작게 돌린다. `try_multi`는 커밋되지 않는다.
- 탐색 한도는 시간이 아니라 반복 횟수다. 같은 시드와 같은 반복 횟수면 같은 결과가 나오게 만들었다. 저장소에 이미 들어 있는 결과와 `git diff`로 비교했을 때 실행시간(`runtime_seconds`)과 `settings.json`의 PC 정보만 달라야 한다. 다른 값이 달라지면 팀에 알린다.
- `--jobs`는 동시에 쓰는 프로세스 수이고 결과에는 영향이 없다.

**4. 결과 검산**

```shell
python check_multi.py --results results
```

모든 해를 별도 코드로 다시 계산해 배송지 누락·중복, 용량, 근무시간 상한, 비용을 대조한다.

**5. 요약 생성**

```shell
python summarize.py
```

`results/SUMMARY.md`를 다시 만든다. 아직 안 돌린 알고리즘은 "결과 없음"으로 나온다. matplotlib이 설치돼 있으면 수렴 그래프(`results/convergence.png`)도 만든다.

**6. 자기 결과 폴더만 커밋**

```shell
git add results/sa
git commit -m "SA 결과"
git push
```

`SUMMARY.md`는 자동 생성물이라 여러 명이 같이 올리면 충돌한다. 자기 알고리즘 폴더만 올리고, 요약은 팀장이 모아서 다시 만든다.

## 폴더 구조

```
README.md                 시작점
docs/                     설명 문서 (결과 해석, 데이터 설명, 담당 결과 양식)
data/station_day/         실험 데이터 (차량 6대, 배송지 923곳, 경로별 이동시간)
solvers_multi.py          세 알고리즘과 채점기
run_multi.py              실험 실행
check_multi.py            검증
summarize.py              결과 요약 생성
dataset.py                데이터 읽기와 이동시간 조회
data_fetch_station.py     원자료에서 데이터를 다시 만드는 스크립트
results/                  결과
  SUMMARY.md                전체 요약
  baseline/                 A (기록 그대로)
  greedy/ sa/ alns/         본 실험
  extra/limits/             반복 한도 비교
  extra/sensitivity/        민감도
v1/                       이전 실험 (한 차량의 방문 순서만). 코드, 데이터, 결과가 모두 들어 있다
```

## 실험 규칙

- **비교 대상.** A는 기록된 배정과 기록된 순서, B는 기록된 배정에 알고리즘 순서(배정 고정), C는 알고리즘이 배정과 순서를 모두 정한 것이다. C2는 C와 같지만 SA·ALNS가 기록된 배정에서 출발한다. B와 C·C2의 차이가 배정을 바꿔서 얻는 효과다.
- **비용** = 이동시간 + 대기 가중치 × 대기시간 + 지각 초당 벌점 × 지각시간 + 지각 건당 벌점 × 지각 배송지 수.

  | 인자 | 기본값 | 뜻 |
  |---|---:|---|
  | `--wait-weight` | 1 | 시간대가 열리기 전에 도착해 기다린 1초당 비용 |
  | `--late-second-cost` | 5 | 물품 처리 완료가 마감보다 늦은 1초당 비용. 물품마다 따로 센다 |
  | `--late-stop-cost` | 600 | 지각한 배송지 1곳당 비용. 한 곳에서 물품 여러 개가 늦어도 한 번만 센다 |

  모두 "이동시간 1초 = 1"로 둔 환산값이고, 근거가 있는 값이 아니라 실험 가정이다. 그래서 값을 바꾼 민감도 결과를 같이 보고한다.
- **제약.** 모든 배송지는 정확히 한 차에 실리고, 차량 용량을 넘을 수 없다. 차량별 소요시간(출발부터 마지막 배송 완료까지)은 근무시간 상한을 넘을 수 없다. 상한의 기본값은 기록된 6개 경로 중 가장 긴 소요시간(8.23시간)이고 `--max-duration-seconds`로 바꾼다.
- **시계.** 차량마다 자기 출발 오프셋에서 시작하고, 거점에서 출발해 마지막 배송지에서 끝난다. 시간대·대기·물품 처리 순서는 이전 실험과 같다.
- **이동시간.** `dataset.py`의 `travel()`로만 조회한다. 원래 같은 경로였던 쌍은 원자료 값, 다른 경로의 배송지 쌍은 추정값이다.

## 데이터와 라이선스

데이터 저작권은 Amazon.com, Inc. 또는 그 계열사에 있다. 2018년 미국 5개 도시의 배송 경로를 담은 공개 자료에서 실험용 입력을 가공했다. 자료와 가공본의 이용 조건은 [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/)이고 전문은 `data/Amazon_License.txt`에 있다. 원자료는 용량이 커서 저장소에 없다. 받는 방법은 [docs/DATASET_CHANGES.md](docs/DATASET_CHANGES.md)에 있다.

[공식 데이터 안내](https://registry.opendata.aws/amazon-last-mile-challenges/) · [필드 설명](https://github.com/MIT-CAVE/rc-cli/blob/main/templates/data_structures.md) · [Merchán 외, 데이터셋 논문](https://doi.org/10.1287/trsc.2022.1173)
