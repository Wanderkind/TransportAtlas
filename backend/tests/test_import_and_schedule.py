from __future__ import annotations
import copy
from datetime import date
from pathlib import Path
from zipfile import ZipFile
import pytest
from transport_atlas.storage import connect,one,rows
from transport_atlas.ingest import import_file
from transport_atlas.schedule import active_services,daily_summary,stop_board,gtfs_instant,trip_stop_times
from transport_atlas.gtfs import FeedError,clock_seconds,read_feed

FIX=Path(__file__).parent/'fixtures'

@pytest.fixture
def db(tmp_path):
    c=connect(tmp_path/'test.sqlite',backend='sqlite')
    yield c,tmp_path
    c.close()

def load(db,file='synthetic_sardinia.zip',source='arst'):
    c,root=db
    return import_file(c,FIX/file,source_id=source,archive_dir=root/'raw')

def test_import_and_idempotent_snapshot(db):
    a=load(db)
    assert a['status']=='imported'
    assert a['feeds']['trips']==4
    assert a['service_start']=='2026-11-01'
    assert a['service_end']=='2026-11-30'
    b=load(db)
    assert b['status']=='unchanged' and b['snapshot_id']==a['snapshot_id']
    assert one(db[0],'SELECT COUNT(*) AS n FROM feed_snapshots')['n']==1
    assert Path(db[1]/'raw'/'arst'/f"{a['sha256']}.zip").exists()

def test_same_raw_ids_across_feeds_are_distinct(db):
    a=load(db)
    b=load(db,'synthetic_second_source.zip','second')
    assert a['snapshot_id'] != b['snapshot_id']
    assert one(db[0],"SELECT COUNT(*) AS n FROM routes WHERE route_id='1'")['n']==2
    assert len(daily_summary(db[0],'2026-11-06')['snapshots'])==2

def test_calendar_additions_and_removals(db):
    s=load(db)['snapshot_id']
    assert active_services(db[0],s,'2026-11-06')=={'WEEK'}
    assert active_services(db[0],s,'2026-11-07')=={'SCHOOL'}
    assert active_services(db[0],s,'2026-11-13')==set()

def test_daily_summary_frequency_precision(db):
    load(db)
    item=daily_summary(db[0],'2026-11-06')['snapshots'][0]
    assert item['active_trip_templates']==3
    assert item['scheduled_trip_instances']==4  # night + three exact trips
    assert item['non_exact_frequency_windows']==1
    assert item['active_route_records']==3

def test_overnight_departure_and_service_day(db):
    s=load(db)['snapshot_id']
    board=stop_board(db[0],s,'B','2026-11-07')
    overnight=[x for x in board['departures'] if x['trip_id']=='T_NIGHT']
    assert len(overnight)==1
    assert overnight[0]['service_date']=='2026-11-06'
    assert overnight[0]['gtfs_time']=='25:15:00'
    assert overnight[0]['local_departure'].startswith('2026-11-07T01:15:00')
    assert clock_seconds('25:15:00')==90900

def test_exact_and_inexact_frequencies(db):
    s=load(db)['snapshot_id']
    board=stop_board(db[0],s,'A','2026-11-06')
    exact=[x for x in board['departures'] if x['trip_id']=='T_EXACT']
    assert [x['local_departure'][11:16] for x in exact]==['08:00','08:20','08:40']
    assert not any(x['trip_id']=='T_FREQ' for x in board['departures'])
    assert len([w for w in board['frequency_windows'] if w['trip_id']=='T_FREQ'])==1

def test_gtfs_noon_origin_dst():
    for day in ['2026-03-29','2026-10-25']:
        assert gtfs_instant(day,12*3600,'Europe/Rome').strftime('%Y-%m-%dT%H:%M')==day+'T12:00'
    assert gtfs_instant('2026-03-29',3*3600,'Europe/Rome').strftime('%H:%M')=='03:00'

def test_no_silent_stop_merge_and_trip_details(db):
    a=load(db)['snapshot_id']
    b=load(db,'synthetic_second_source.zip','second')['snapshot_id']
    assert one(db[0],"SELECT COUNT(*) AS n FROM stops WHERE stop_id='A'")['n']==2
    assert len(trip_stop_times(db[0],a,'T_NIGHT')['stops'])==2
    assert len(trip_stop_times(db[0],b,'T_NIGHT')['stops'])==2

def test_failed_import_is_atomic(db,tmp_path):
    first=load(db)
    from tests.generate_fixtures import build,base
    bad=copy.deepcopy(base)
    bad['trips'][0]['route_id']='does-not-exist'
    malformed=tmp_path/'malformed.zip'
    build(malformed,bad)
    with pytest.raises(FeedError):
        import_file(db[0],malformed,source_id='arst',archive_dir=tmp_path/'raw')
    assert one(db[0],"SELECT snapshot_id FROM selection_members WHERE source_id='arst'")['snapshot_id']==first['snapshot_id']
    assert one(db[0],"SELECT COUNT(*) AS n FROM feed_snapshots")['n']==1

def test_reject_path_traversal_zip(tmp_path):
    bad=tmp_path/'path.zip'
    with ZipFile(bad,'w') as zipf:zipf.writestr('../escape.txt','not safe')
    with pytest.raises(FeedError):read_feed(bad)

def test_non_ascii_ids(tmp_path,db):
    from tests.generate_fixtures import build,base
    data=copy.deepcopy(base)
    data['routes'][0]['route_id']='노선①'
    data['trips'][0]['route_id']='노선①'
    data['stops'][0]['stop_id']='정류장A'
    for row in data['stop_times']:
        if row['stop_id']=='A':row['stop_id']='정류장A'
    file=tmp_path/'utf8.zip';build(file,data)
    s=import_file(db[0],file,source_id='utf8',archive_dir=tmp_path/'raw')['snapshot_id']
    assert one(db[0],"SELECT route_id FROM routes WHERE snapshot_id=? AND route_id=?",(s,'노선①'))['route_id']=='노선①'

def test_calendar_dates_only(tmp_path,db):
    from tests.generate_fixtures import build,base
    data=copy.deepcopy(base)
    data['calendar']=[]
    data['calendar_dates']=[dict(service_id='WEEK',date='20261106',exception_type='1'),
                            dict(service_id='SCHOOL',date='20261107',exception_type='1')]
    file=tmp_path/'dates_only.zip';build(file,data)
    s=import_file(db[0],file,source_id='datesonly',archive_dir=tmp_path/'raw')['snapshot_id']
    assert active_services(db[0],s,'2026-11-06')=={'WEEK'}
    assert active_services(db[0],s,'2026-11-08')==set()

def test_mid_transaction_failure_preserves_active_snapshot(tmp_path,db):
    """Simulate database write failure after a source has a valid prior snapshot."""
    from tests.generate_fixtures import build,base
    original=load(db)['snapshot_id']
    changed=copy.deepcopy(base)
    changed['routes'][0]['route_long_name']='Different content: synthetic update'
    candidate=tmp_path/'candidate.zip';build(candidate,changed)
    c=db[0]
    c.execute("""CREATE TRIGGER fail_new_route BEFORE INSERT ON routes
      WHEN NEW.route_id='E' BEGIN SELECT RAISE(ABORT,'simulated write failure'); END""")
    c.commit()
    with pytest.raises(Exception,match='simulated write failure'):
        import_file(c,candidate,source_id='arst',archive_dir=tmp_path/'raw')
    assert one(c,"SELECT snapshot_id FROM selection_members WHERE source_id='arst'")['snapshot_id']==original
    assert one(c,'SELECT COUNT(*) AS n FROM feed_snapshots')['n']==1
