"""Build a real-data pilot from the first N complete Amazon training routes.

Online: python data_fetch.py --count 24 --output data_repeat
Offline: python data_fetch.py --source-zip ORIGINAL.zip --count 24 --output data_repeat
Uses only the Python standard library.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
from urllib.request import Request, urlopen
from zipfile import ZipFile


BASE = 'https://amazon-last-mile-challenges.s3.us-west-2.amazonaws.com/almrrc2021/'
TRAIN = BASE + 'almrrc2021-data-training/model_build_inputs/'
CHUNK_SIZE = {'route_data': 512 * 1024, 'package_data': 2 * 1024**2,
              'travel_times': 4 * 1024**2, 'actual_sequences': 256 * 1024}


def http_chunks(url, chunk_size, source):
    offset = 0
    etag = None
    size = None
    while size is None or offset < size:
        headers = {'Range': f'bytes={offset}-{offset + chunk_size - 1}'}
        if etag:
            headers['If-Match'] = etag
        with urlopen(Request(url, headers=headers), timeout=90) as response:
            byte_range = response.headers.get('Content-Range', '')
            if response.status != 206 or not byte_range.startswith(f'bytes {offset}-'):
                raise OSError(f'Unexpected S3 range for {url}: {byte_range}')
            limits, size_text = byte_range.split('/')
            size = int(size_text)
            raw = response.read()
            if len(raw) != int(limits.split('-')[1]) - offset + 1:
                raise OSError(f'Incomplete HTTP response: {url}')
            etag = response.headers['ETag']
        offset += len(raw)
        source.update(etag=etag, object_size=size)
        yield raw


def zip_chunks(path, name, chunk_size, source):
    member = 'almrrc2021/almrrc2021-data-training/model_build_inputs/' + name + '.json'
    with ZipFile(path) as archive:
        info = archive.getinfo(member)
        source.update(member=member, object_size=info.file_size)
        with archive.open(info) as stream:
            while raw := stream.read(chunk_size):
                yield raw


def first_records(chunks, name, count, wanted, source):
    """Read complete JSON records as far as the requested IDs, across chunk boundaries."""
    decoder = json.JSONDecoder()
    buffer = ''
    offset = 0
    found = {}
    for raw in chunks:
        offset += len(raw)
        # Keys/IDs and timestamps are ASCII, including the package and time tables.
        buffer += raw.decode('ascii')
        if offset == len(raw):
            buffer = buffer.lstrip().removeprefix('{')
        pos = 0
        while True:
            start = pos
            while pos < len(buffer) and buffer[pos] in ' \r\t\n,':
                pos += 1
            if pos >= len(buffer) or buffer[pos] == '}':
                break
            try:
                route_id, after_key = decoder.raw_decode(buffer, pos)
                colon = buffer.index(':', after_key)
                value_pos = colon + 1
                while value_pos < len(buffer) and buffer[value_pos].isspace():
                    value_pos += 1
                value, pos = decoder.raw_decode(buffer, value_pos)
            except (ValueError, IndexError):
                pos = start
                break
            if wanted is None or route_id in wanted:
                found[route_id] = value
            if len(found) == count:
                source['bytes_read'] = offset
                return found, source
        buffer = buffer[pos:]
    raise ValueError(f'{name}: found only {len(found)} of the requested {count} records')


def read_routes(name, count, wanted=None, source_zip=None):
    url = TRAIN + name + '.json'
    source = {'url': url}
    if source_zip is None:
        chunks = http_chunks(url, CHUNK_SIZE[name], source)
    else:
        chunks = zip_chunks(source_zip, name, CHUNK_SIZE[name], source)
    with closing(chunks):
        return first_records(chunks, name, count, wanted, source)


def seconds_from_departure(value, departure):
    if value is None or isinstance(value, float) and math.isnan(value):
        return None
    timestamp = datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    return int((timestamp - departure).total_seconds())


def normalize(route_id, meta, packages, times, sequence):
    departure = datetime.fromisoformat(meta['date_YYYY_MM_DD'] + 'T' + meta['departure_time_utc']).replace(tzinfo=timezone.utc)
    station, = [key for key, node in meta['stops'].items() if node['type'] == 'Station']
    stops = sorted(key for key, node in meta['stops'].items() if node['type'] == 'Dropoff')
    nodes = set(stops) | {station}
    if not (nodes == set(meta['stops']) == set(times) == set(sequence['actual']) == set(packages)):
        raise ValueError(f'{route_id}: missing or unknown stop')
    actual = sorted(sequence['actual'], key=sequence['actual'].get)
    if actual[0] != station or sorted(sequence['actual'].values()) != list(range(len(nodes))):
        raise ValueError(f'{route_id}: invalid recorded visit order')
    for start, costs in times.items():
        if set(costs) != nodes or costs[start] != 0 or any(not math.isfinite(x) or x < 0 for x in costs.values()):
            raise ValueError(f'{route_id}: invalid travel time matrix')
    windows = {}
    for stop in stops:
        windows[stop] = []
        for package in packages[stop].values():
            window = package['time_window']
            opens = seconds_from_departure(window['start_time_utc'], departure)
            closes = seconds_from_departure(window['end_time_utc'], departure)
            service = package['planned_service_time_seconds']
            if not math.isfinite(service) or service < 0 or opens is not None and closes is not None and opens > closes:
                raise ValueError(f'{route_id}: invalid package window or planned service time')
            windows[stop].append([opens, closes, service])
    return {'id': route_id, 'depot': station, 'stops': stops, 'travel': times,
            'windows': windows, 'actual': actual[1:], 'station_code': meta['station_code'],
            'departure_utc': departure.isoformat(), 'route_score': meta['route_score']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--count', type=int, default=24)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parent / 'data')
    parser.add_argument('--source-zip', type=Path, help='Read original members from the full Amazon archive offline')
    args = parser.parse_args()
    if not 1 <= args.count <= 6112:
        parser.error('count must be between 1 and 6112')
    args.output.mkdir(parents=True, exist_ok=True)
    metadata, route_source = read_routes('route_data', args.count, source_zip=args.source_zip)
    wanted = set(metadata)
    with ThreadPoolExecutor(max_workers=3) as pool:
        tasks = {name: pool.submit(read_routes, name, args.count, wanted, args.source_zip)
                 for name in ('package_data', 'travel_times', 'actual_sequences')}
        tables = {name: task.result() for name, task in tasks.items()}
    routes = [normalize(key, value, *(tables[name][0][key] for name in tasks)) for key, value in metadata.items()]
    payload = json.dumps(routes, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    if args.source_zip is None:
        with urlopen(BASE + 'License.txt', timeout=30) as response:
            license_data = response.read()
    else:
        with ZipFile(args.source_zip) as archive:
            license_data = archive.read('almrrc2021/License.txt')
    (args.output / 'Amazon_License.txt').write_bytes(license_data)
    manifest = {'dataset': '2021 Amazon Last Mile Routing Research Challenge',
                'attribution': 'Amazon; Merchan et al., 2021 Amazon Last Mile Routing Research Challenge: Data Set',
                'source': 'https://registry.opendata.aws/amazon-last-mile-challenges/',
                'paper': 'https://doi.org/10.1287/trsc.2022.1173',
                'license': 'CC BY-NC 4.0', 'license_url': 'https://creativecommons.org/licenses/by-nc/4.0/',
                'selection': 'First N route_data.json entries in the official training partition, joined on route ID to three other files. All stops and packages retained. Deterministic convenience sample, no quality or feasibility filtering.',
                'modifications': 'Training subset, UTC windows converted to seconds since route departure; package IDs, dimensions, coordinates and unused fields omitted.',
                'routes': len(routes), 'dropoffs': sum(len(route['stops']) for route in routes),
                'packages': sum(len(pkgs) for route in routes for pkgs in route['windows'].values()),
                'packages_with_end': sum(w[1] is not None for route in routes for pkgs in route['windows'].values() for w in pkgs),
                'source_files': {'route_data': route_source, **{name: pair[1] for name,pair in tables.items()}},
                'sha256_routes_json': sha256(payload).hexdigest()}
    staged = args.output / 'routes.json.tmp'
    if args.source_zip is not None:
        manifest['local_archive'] = args.source_zip.name
    staged.write_bytes(payload)
    staged.replace(args.output / 'routes.json')
    (args.output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"{len(routes)} routes, {manifest['dropoffs']} stops, {manifest['packages']} packages; output {args.output}")


if __name__ == '__main__':
    main()
