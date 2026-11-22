"""Build a multi-vehicle instance: adjacent routes of one station on one day.

python data_fetch_station.py --raw FOLDER
FOLDER holds the four training files of the Amazon 2021 Last Mile Routing Challenge
(route_data, package_data, travel_times, actual_sequences .json); see docs/DATASET_CHANGES.md.
Uses only the Python standard library. Reuses two functions of v1/data_fetch.py.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
from statistics import median
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / 'v1'))      # data_fetch.py lives with the v1 experiment
from data_fetch import TRAIN, first_records, seconds_from_departure


DEPOT = 'DEPOT'
RADIUS_KM = 0.5
SIZES = (6, 7, 8)
BANDS_KM = ((0, 0.25), (0.25, 1), (1, 3), (3, math.inf))


def haversine_km(lat1, lng1, lat2, lng2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(h))


def read_selected(folder, name, wanted):
    path = folder / (name + '.json')
    source = {'url': TRAIN + name + '.json', 'object_size': path.stat().st_size}

    def chunks():
        with path.open('rb') as stream:
            while raw := stream.read(8 * 1024**2):
                yield raw
    return first_records(chunks(), name, len(wanted), wanted, source)


def choose_routes(day):
    """Connected group of 6-8 routes with the largest share of stops that have a stop
    of another chosen route within RADIUS_KM. Returns (route IDs, share, best share per size)."""
    route_ids = sorted(day)
    points = [(stop['lat'], stop['lng'], index) for index, route_id in enumerate(route_ids)
              for stop in day[route_id]['stops'].values() if stop['type'] == 'Dropoff']
    lat0 = sum(p[0] for p in points) / len(points)
    cell_lat = RADIUS_KM / 110.0          # slightly larger than the radius in both directions
    cell_lng = RADIUS_KM / (110.0 * math.cos(math.radians(lat0)))
    grid = {}
    for number, (lat, lng, _) in enumerate(points):
        grid.setdefault((int(lat // cell_lat), int(lng // cell_lng)), []).append(number)
    near = [0] * len(points)              # bit r set: route r has a stop within the radius
    for (row, column), members in grid.items():
        for d_row in (-1, 0, 1):
            for d_column in (-1, 0, 1):
                for a in members:
                    for b in grid.get((row + d_row, column + d_column), ()):
                        if points[a][2] != points[b][2] and haversine_km(*points[a][:2], *points[b][:2]) < RADIUS_KM:
                            near[a] |= 1 << points[b][2]
    masks = [Counter() for _ in route_ids]
    for (_, _, index), mask in zip(points, near):
        masks[index][mask] += 1
    adjacent = [{other for other in range(len(route_ids)) if any(mask >> other & 1 for mask in masks[index])}
                for index in range(len(route_ids))]

    def share(group):
        bits = sum(1 << index for index in group)
        return (sum(count for index in group for mask, count in masks[index].items() if mask & bits)
                / sum(sum(masks[index].values()) for index in group))

    level = {frozenset([index]) for index in range(len(route_ids))}
    best, by_size = None, {}
    for size in range(2, max(SIZES) + 1):
        level = {group | {other} for group in level for index in group for other in adjacent[index] - group}
        if size in SIZES and level:
            # Highest share first; ties go to the alphabetically first set of route IDs.
            top = min(level, key=lambda group: (-share(group), sorted(route_ids[i] for i in group)))
            by_size[size] = share(top)
            if best is None or share(top) > share(best):
                best = top
    if best is None:
        raise ValueError('no connected group of 6-8 routes')
    return sorted(route_ids[index] for index in best), share(best), by_size


def fit_power(pairs):
    """Least squares of log(seconds) on log(km): seconds = a * km ** b."""
    logs = [(math.log(d), math.log(t)) for d, t in pairs if d > 0 and t > 0]
    n = len(logs)
    mean_x, mean_y = sum(x for x, _ in logs) / n, sum(y for _, y in logs) / n
    b = sum((x - mean_x) * (y - mean_y) for x, y in logs) / sum((x - mean_x) ** 2 for x, _ in logs)
    return math.exp(mean_y - b * mean_x), b


def estimate(a, b, distance_km):
    return a * distance_km ** b if distance_km > 0 else 0.0


def error_summary(rows):
    """rows: (km, true seconds, estimated seconds)."""
    def one(subset):
        errors = [e - t for _, t, e in subset]
        relative = [abs(e - t) / t for _, t, e in subset if t > 0]
        return {'pairs': len(subset), 'mean_true_seconds': round(sum(t for _, t, _ in subset) / len(subset), 1),
                'mean_abs_error_seconds': round(sum(map(abs, errors)) / len(errors), 1),
                'median_abs_pct_error': round(100 * median(relative), 1),
                'bias_seconds': round(sum(errors) / len(errors), 1)}
    result = one(rows)
    result['by_distance_km'] = {f'{low}-{high}': one(part) for low, high in BANDS_KM
                                if (part := [r for r in rows if low <= r[0] < high])}
    return result


def fit_report(pairs_by_route):
    """Fit on all routes; error measured in-sample and leaving one route out."""
    everything = [pair for pairs in pairs_by_route.values() for pair in pairs]
    a, b = fit_power(everything)
    held_out = []
    for route_id, pairs in pairs_by_route.items():
        a_cv, b_cv = fit_power([pair for other, rows in pairs_by_route.items() if other != route_id for pair in rows])
        held_out += [(d, t, estimate(a_cv, b_cv, d)) for d, t in pairs]
    return {'a': a, 'b': b, 'pairs_fitted': sum(d > 0 and t > 0 for d, t in everything),
            'pairs_skipped_zero': sum(d <= 0 or t <= 0 for d, t in everything),
            'error_in_sample': error_summary([(d, t, estimate(a, b, d)) for d, t in everything]),
            'error_leave_one_route_out': error_summary(held_out)}


def build(route_ids, day, packages, times, sequences):
    reference = min(datetime.fromisoformat(day[r]['date_YYYY_MM_DD'] + 'T' + day[r]['departure_time_utc'])
                    for r in route_ids).replace(tzinfo=timezone.utc)
    depots, vehicles, stops, travel, bad_dimensions, self_times = set(), [], {}, {}, [], []
    between, outbound, inbound = {}, {}, {}
    for route_id in route_ids:
        meta = day[route_id]
        departure = datetime.fromisoformat(meta['date_YYYY_MM_DD'] + 'T' + meta['departure_time_utc']).replace(tzinfo=timezone.utc)
        station, = [key for key, node in meta['stops'].items() if node['type'] == 'Station']
        dropoffs = sorted(key for key, node in meta['stops'].items() if node['type'] == 'Dropoff')
        nodes = set(dropoffs) | {station}
        recorded = sequences[route_id]['actual']
        if not (nodes == set(meta['stops']) == set(times[route_id]) == set(recorded) == set(packages[route_id])):
            raise ValueError(f'{route_id}: missing or unknown stop')
        order = sorted(recorded, key=recorded.get)
        if order[0] != station or sorted(recorded.values()) != list(range(len(nodes))):
            raise ValueError(f'{route_id}: invalid recorded visit order')
        depots.add((meta['stops'][station]['lat'], meta['stops'][station]['lng']))
        new_id = {key: f'{route_id}:{key}' for key in dropoffs} | {station: DEPOT}
        for key in dropoffs:
            node = meta['stops'][key]
            windows, volume = [], 0.0
            for package_id, package in packages[route_id][key].items():
                size = package['dimensions']
                dims = [size['depth_cm'], size['height_cm'], size['width_cm']]
                if any(x <= 0 for x in dims):
                    bad_dimensions.append({'stop': new_id[key], 'package': package_id, 'dimensions_cm': dims})
                volume += dims[0] * dims[1] * dims[2]
                opens = seconds_from_departure(package['time_window']['start_time_utc'], reference)
                closes = seconds_from_departure(package['time_window']['end_time_utc'], reference)
                service = package['planned_service_time_seconds']
                if not math.isfinite(service) or service < 0 or opens is not None and closes is not None and opens > closes:
                    raise ValueError(f'{route_id}: invalid package window or planned service time')
                windows.append([opens, closes, service])
            zone = node['zone_id']
            stops[new_id[key]] = {'route_id': route_id, 'raw_id': key, 'lat': node['lat'], 'lng': node['lng'],
                                  'zone_id': zone if isinstance(zone, str) else None,
                                  'volume_cm3': volume, 'windows': windows}
        order_nodes = [station] + dropoffs
        matrix = [[times[route_id][a][b] for b in order_nodes] for a in order_nodes]
        if any(not math.isfinite(x) or x < 0 for row in matrix for x in row):
            raise ValueError(f'{route_id}: invalid travel time matrix')
        # The raw file has a few non-zero times from a stop to itself; kept as they are and reported.
        self_times += [{'node': new_id[key], 'seconds': matrix[i][i]} for i, key in enumerate(order_nodes) if matrix[i][i] != 0]
        travel[route_id] = {'nodes': [new_id[key] for key in order_nodes], 'seconds': matrix}
        vehicles.append({'route_id': route_id, 'capacity_cm3': meta['executor_capacity_cm3'],
                         'departure_offset_seconds': int((departure - reference).total_seconds()),
                         'departure_utc': departure.isoformat(), 'route_score': meta['route_score'],
                         'raw_station_id': station, 'recorded_order': [new_id[key] for key in order[1:]]})
        place = {key: (meta['stops'][key]['lat'], meta['stops'][key]['lng']) for key in order_nodes}
        between[route_id] = [(haversine_km(*place[a], *place[b]), times[route_id][a][b])
                             for a in dropoffs for b in dropoffs if a != b]
        outbound[route_id] = [(haversine_km(*place[station], *place[key]), times[route_id][station][key]) for key in dropoffs]
        inbound[route_id] = [(haversine_km(*place[key], *place[station]), times[route_id][key][station]) for key in dropoffs]
    if len(depots) != 1:
        raise ValueError('chosen routes do not share one station coordinate')
    (lat, lng), = depots
    vehicles.sort(key=lambda vehicle: (vehicle['departure_offset_seconds'], vehicle['route_id']))
    instance = {'station_code': day[route_ids[0]]['station_code'], 'date': day[route_ids[0]]['date_YYYY_MM_DD'],
                'reference_utc': reference.isoformat(), 'depot': {'id': DEPOT, 'lat': lat, 'lng': lng},
                'vehicles': vehicles, 'stops': stops}
    fits = {'between_dropoffs': fit_report(between), 'depot_to_dropoff': fit_report(outbound),
            'dropoff_to_depot': fit_report(inbound)}
    return instance, travel, fits, bad_dimensions, self_times


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw', type=Path, required=True, help='Folder with the four raw training JSON files')
    parser.add_argument('--station', default='DLA7')
    parser.add_argument('--date', default='2018-08-03')
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parent / 'data' / 'station_day')
    args = parser.parse_args()
    metadata = json.loads((args.raw / 'route_data.json').read_text(encoding='ascii'))
    day = {route_id: meta for route_id, meta in metadata.items()
           if meta['station_code'] == args.station and meta['date_YYYY_MM_DD'] == args.date}
    if not day:
        parser.error('no routes for this station and date')
    route_ids, share, by_size = choose_routes(day)
    wanted = set(route_ids)
    sequences = json.loads((args.raw / 'actual_sequences.json').read_text(encoding='ascii'))
    tables = {name: read_selected(args.raw, name, wanted) for name in ('package_data', 'travel_times')}
    instance, travel, fits, bad_dimensions, self_times = build(route_ids, day, tables['package_data'][0], tables['travel_times'][0], sequences)
    args.output.mkdir(parents=True, exist_ok=True)
    digests = {}
    for name, content in (('station_day.json', instance), ('travel.json', travel)):
        payload = json.dumps(content, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
        (args.output / name).write_bytes(payload)
        digests[name] = sha256(payload).hexdigest()
    stops = instance['stops']
    manifest = {
        'dataset': '2021 Amazon Last Mile Routing Research Challenge',
        'attribution': 'Amazon; Merchan et al., 2021 Amazon Last Mile Routing Research Challenge: Data Set',
        'source': 'https://registry.opendata.aws/amazon-last-mile-challenges/',
        'paper': 'https://doi.org/10.1287/trsc.2022.1173',
        'license': 'CC BY-NC 4.0', 'license_url': 'https://creativecommons.org/licenses/by-nc/4.0/',
        'selection': {'station_code': args.station, 'date': args.date, 'routes_that_day_in_training_partition': len(day),
                      'rule': f'Connected group of {SIZES[0]}-{SIZES[-1]} routes (two routes are linked when some of their stops '
                              f'are within {RADIUS_KM} km) with the largest share of stops that have a stop of another chosen '
                              f'route within {RADIUS_KM} km.',
                      'share_of_stops_near_another_chosen_route': round(share, 4),
                      'best_share_by_group_size': {str(size): round(value, 4) for size, value in by_size.items()},
                      'route_ids': route_ids},
        'modifications': 'Stop IDs prefixed with the route ID; station stop renamed DEPOT; UTC windows converted to seconds '
                         'since the earliest departure among the chosen routes; package volumes summed per stop; '
                         'package IDs, scan status and single package dimensions omitted.',
        'reference_utc': instance['reference_utc'],
        'routes': len(route_ids), 'dropoffs': len(stops),
        'packages': sum(len(stop['windows']) for stop in stops.values()),
        'packages_with_end': sum(w[1] is not None for stop in stops.values() for w in stop['windows']),
        'dropoffs_without_zone': sum(stop['zone_id'] is None for stop in stops.values()),
        'nonpositive_dimension_packages': bad_dimensions,
        'nonzero_self_travel_seconds': self_times,
        'travel_time_model': {
            'form': 'seconds = a * km ** b, km = great-circle distance, fitted by least squares on log-log values',
            'fitted_on': 'directed within-route pairs of the chosen routes (raw travel times)',
            'use': 'dataset.py uses between_dropoffs for pairs of dropoffs from different routes. Depot legs are in '
                   'the raw matrices for every stop, so the two depot fits are reported but not used for lookups.',
            **fits},
        'source_files': {name: source for name, (_, source) in tables.items()},
        'sha256': digests}
    (args.output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"{len(route_ids)} routes, {manifest['dropoffs']} stops, {manifest['packages']} packages; "
          f"share near another chosen route {share:.1%}; output {args.output}")


if __name__ == '__main__':
    main()
