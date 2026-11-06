"""Timetable pages and server-side direction/variant filters are complete and stable."""
from datetime import date
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from transport_atlas.api import create_app
from transport_atlas.ingest import import_file
from transport_atlas.storage import connect
from transport_atlas.timetable import route_timetable


@pytest.fixture
def scenario(tmp_path):
    """Route E with 9 trips: alternating directions and two stop sequences (A-B and a looped A-B-A-B)."""
    con = connect(tmp_path/'pages.sqlite', 'sqlite')
    sid = import_file(con, Path(__file__).parent/'fixtures'/'synthetic_sardinia.zip',
                      source_id='demo', archive_dir=tmp_path/'raw')['snapshot_id']
    for table in ('frequencies', 'stop_times', 'trips', 'calendars', 'calendar_exceptions'):
        con.execute(f'DELETE FROM {table} WHERE snapshot_id=?', (sid,))
    con.execute('INSERT INTO calendar_exceptions VALUES(?,?,?,?)', (sid, 'PAGE', '2026-11-06', 1))
    for n in range(9):
        direction = n % 2
        stops = ['A', 'B'] if n < 6 else ['A', 'B', 'A', 'B']
        if direction:
            stops = list(reversed(stops))
        tid = f'T{n}'
        con.execute('INSERT INTO trips VALUES(?,?,?,?,?,?,?,?)',
                    (sid, tid, 'E', 'PAGE', None, direction, f'Headsign {direction}', None))
        for sequence, stop_id in enumerate(stops, 1):
            seconds = 6*3600 + n*600 + (sequence-1)*300
            con.execute('INSERT INTO stop_times VALUES(?,?,?,?,?,?,?,?,?)',
                        (sid, tid, sequence, stop_id, seconds, seconds, 0, 0, 1))
    con.commit()
    yield con, sid, tmp_path
    con.close()


def _ids(result):
    return [d['trip_id'] for d in result['departures']]


def test_offset_pages_partition_the_full_ordering(scenario):
    con, sid, _ = scenario
    full = route_timetable(con, sid, 'E', date(2026, 11, 6), limit=500)
    assert len(full['departures']) == 9 and not full['truncated']
    pages, offset = [], 0
    while True:
        page = route_timetable(con, sid, 'E', date(2026, 11, 6), limit=4, offset=offset)
        assert page['offset'] == offset and page['total_departures'] == 9
        pages += _ids(page)
        if not page['truncated']:
            assert page['next_offset'] is None
            break
        offset = page['next_offset']
    assert pages == _ids(full)


def test_direction_and_variant_filters_apply_before_paging(scenario):
    con, sid, _ = scenario
    full = route_timetable(con, sid, 'E', date(2026, 11, 6), limit=500)
    outbound = route_timetable(con, sid, 'E', date(2026, 11, 6), limit=2, direction='0')
    expected = [d['trip_id'] for d in full['departures'] if d['direction_id'] == 0]
    assert outbound['matching_departures'] == len(expected) == 5
    assert _ids(outbound) == expected[:2] and outbound['truncated']
    assert outbound['total_departures'] == 9  # whole-route summary is unchanged
    variant = next(v for v in full['variants'] if v['direction_id'] == 1 and len(v['stops']) == 4)
    chosen = route_timetable(con, sid, 'E', date(2026, 11, 6), limit=500, direction='1', variant=variant['variant_id'])
    assert _ids(chosen) == [d['trip_id'] for d in full['departures'] if d['variant_id'] == variant['variant_id']]
    assert chosen['matching_departures'] == variant['scheduled_departures'] == 1
    assert route_timetable(con, sid, 'E', date(2026, 11, 6), direction='0', variant=variant['variant_id'])['departures'] == []


def test_api_exposes_paging_parameters(scenario):
    con, sid, root = scenario
    con.close()
    with TestClient(create_app(root/'pages.sqlite', 'sqlite')) as client:
        first = client.get(f'/api/v1/route/{sid}/E/timetable?date=2026-11-06&limit=4&offset=4&direction=all&variant=all')
        assert first.status_code == 200
        body = first.json()
        assert body['offset'] == 4 and len(body['departures']) == 4 and body['next_offset'] == 8
        assert client.get(f'/api/v1/route/{sid}/E/timetable?date=2026-11-06&offset=-1').status_code == 422
        assert client.get(f'/api/v1/route/{sid}/E/timetable?date=2026-11-06&direction=null').json()['departures'] == []
