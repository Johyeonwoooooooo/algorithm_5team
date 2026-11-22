"""Read data/station_day and look up travel times between any two nodes.

    from dataset import StationDay
    day = StationDay()
    seconds, estimated = day.travel(origin_id, destination_id)

python dataset.py runs a self-check of the stored files. Standard library only.
"""
from hashlib import sha256
import json
import math
from pathlib import Path


FOLDER = Path(__file__).resolve().parent / 'data' / 'station_day'


def haversine_km(lat1, lng1, lat2, lng2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(h))


class StationDay:
    def __init__(self, folder=FOLDER):
        folder = Path(folder)
        self.manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
        instance = json.loads((folder / 'station_day.json').read_text(encoding='utf-8'))
        self.reference_utc = instance['reference_utc']
        self.depot = instance['depot']
        self.vehicles = instance['vehicles']      # sorted by departure offset
        self.stops = instance['stops']            # stop ID -> route, coordinates, zone, volume, windows
        self.matrices = json.loads((folder / 'travel.json').read_text(encoding='utf-8'))
        self._position = {route_id: {node: i for i, node in enumerate(table['nodes'])}
                          for route_id, table in self.matrices.items()}
        fit = self.manifest['travel_time_model']['between_dropoffs']
        self._a, self._b = fit['a'], fit['b']

    def travel(self, origin, destination):
        """Return (seconds, estimated).

        Raw value when both nodes are in one original route or one of them is the depot
        (every route's raw matrix contains the depot). Otherwise the fitted power law
        on great-circle distance, with estimated=True.
        """
        depot = self.depot['id']
        if origin == destination == depot:
            return 0.0, False
        route_a = None if origin == depot else self.stops[origin]['route_id']
        route_b = None if destination == depot else self.stops[destination]['route_id']
        if route_a is None or route_b is None or route_a == route_b:
            route_id = route_a or route_b
            position = self._position[route_id]
            return self.matrices[route_id]['seconds'][position[origin]][position[destination]], False
        a, b = self.stops[origin], self.stops[destination]
        distance = haversine_km(a['lat'], a['lng'], b['lat'], b['lng'])
        return (self._a * distance ** self._b if distance > 0 else 0.0), True


def self_check(folder=FOLDER):
    day = StationDay(folder)
    for name, digest in day.manifest['sha256'].items():
        assert sha256((Path(folder) / name).read_bytes()).hexdigest() == digest, f'{name}: hash differs from manifest'
    depot = day.depot['id']
    assert len(day.stops) == day.manifest['dropoffs']
    assert sum(len(stop['windows']) for stop in day.stops.values()) == day.manifest['packages']
    assigned = [stop for vehicle in day.vehicles for stop in vehicle['recorded_order']]
    assert len(assigned) == len(set(assigned)) and set(assigned) == set(day.stops), 'recorded orders must cover every stop once'
    assert min(vehicle['departure_offset_seconds'] for vehicle in day.vehicles) == 0
    raw_pairs = 0
    for vehicle in day.vehicles:
        table = day.matrices[vehicle['route_id']]
        assert table['nodes'][0] == depot and set(table['nodes'][1:]) == set(vehicle['recorded_order'])
        assert all(day.stops[stop]['route_id'] == vehicle['route_id'] for stop in vehicle['recorded_order'])
        for i, origin in enumerate(table['nodes']):
            for j, destination in enumerate(table['nodes']):
                assert day.travel(origin, destination) == (table['seconds'][i][j], False)
                raw_pairs += 1
        load = sum(day.stops[stop]['volume_cm3'] for stop in vehicle['recorded_order'])
        assert load <= vehicle['capacity_cm3'], f"{vehicle['route_id']}: recorded load exceeds capacity"
    first, second = day.vehicles[0]['recorded_order'][0], day.vehicles[1]['recorded_order'][0]
    seconds, estimated = day.travel(first, second)
    assert estimated and seconds > 0
    print(f'OK: {len(day.vehicles)} vehicles, {len(day.stops)} stops, {raw_pairs} raw pairs returned unchanged; '
          f'cross-route example {seconds:.0f} s (estimated)')


if __name__ == '__main__':
    self_check()
