from datetime import date
from pathlib import Path
import pytest
from transport_atlas.ingest import import_file
from transport_atlas.storage import connect
from transport_atlas.network import network_geometry

FIX=Path(__file__).parent/'fixtures'

@pytest.fixture(params=['sqlite','duckdb'])
def catalog(tmp_path,request):
    con=connect(tmp_path/'coverage.db',request.param)
    sid=import_file(con,FIX/'synthetic_sardinia.zip',source_id='demo',archive_dir=tmp_path/'raw')['snapshot_id']
    con.execute('INSERT INTO routes SELECT snapshot_id,?,agency_id,?,?,route_type,route_color FROM routes WHERE snapshot_id=? AND route_id=?',('ORPHAN','O','No trips',sid,'1'))
    con.execute('INSERT INTO routes SELECT snapshot_id,?,agency_id,?,?,route_type,route_color FROM routes WHERE snapshot_id=? AND route_id=?',('BAD','B','Missing coordinates',sid,'1'))
    con.execute('INSERT INTO trips VALUES(?,?,?,?,?,?,?,?)',(sid,'BAD_TRIP','BAD','WEEK',None,0,'Unknown',None))
    for i in (1,2):
        con.execute('INSERT INTO stops VALUES(?,?,?,?,?,?,?,?)',(sid,'N'+str(i),'No coordinate',None,None,0,None,None))
        con.execute('INSERT INTO stop_times VALUES(?,?,?,?,?,?,?,?,?)',(sid,'BAD_TRIP',i,'N'+str(i),28800+i,28800+i,0,0,1))
    con.commit()
    yield con,sid,tmp_path
    con.close()

def reconcile(c):
    assert c['total_route_records']==c['routes_without_trip_references']+c['inactive_routes_excluded']+c['eligible_routes']
    assert c['routes_with_trip_references']==c['total_route_records']-c['routes_without_trip_references']
    assert c['eligible_routes']==c['rendered_routes']+c['routes_with_insufficient_geometry']+c['routes_omitted_by_limits']

def test_coverage_distinguishes_inactivity_data_gaps_and_display_limits(catalog):
    con,_,_=catalog
    for active_only,limit,expected in [(True,1200,(4,3,1,0,1)),(False,1200,(5,4,1,0,0)),(True,1,(4,1,1,2,1))]:
        geo=network_geometry(con,date(2026,11,6),active_only=active_only,max_routes=limit)
        c=geo['metadata']['coverage']
        reconcile(c)
        assert c['total_route_records']==6 and c['routes_with_trip_references']==5
        assert c['active_routes']==4 and c['routes_without_trip_references']==1
        assert tuple(c[k] for k in ('eligible_routes','rendered_routes','routes_with_insufficient_geometry','routes_omitted_by_limits','inactive_routes_excluded'))==expected
        assert geo['metadata']['coverage']==geo['metadata']['snapshots'][0]['coverage']
        assert geo['metadata']['truncated']==(limit==1)

def test_coverage_totals_reconcile_across_sources_and_filters(catalog):
    con,_,root=catalog
    import_file(con,FIX/'synthetic_second_source.zip',source_id='second',archive_dir=root/'raw')
    result=network_geometry(con,date(2026,11,6))['metadata']
    reconcile(result['coverage'])
    for key,value in result['coverage'].items():
        assert value==sum(s['coverage'][key] for s in result['snapshots'])
    assert result['coverage']['total_route_records']==7
    empty=network_geometry(con,date(2026,11,6),mode='rail')['metadata']['coverage']
    assert all(value==0 for value in empty.values())

def test_point_limits_are_separate_from_missing_data(catalog):
    con,_,_=catalog
    geo=network_geometry(con,date(2026,11,6),max_points=2)
    c=geo['metadata']['coverage']
    reconcile(c)
    assert c['rendered_routes']==1 and c['routes_omitted_by_limits']==2
    assert c['routes_with_insufficient_geometry']==1 and c['routes_without_trip_references']==1
    assert geo['metadata']['truncated']

def test_partial_variants_are_a_subset_of_drawn_routes(catalog):
    con,sid,_=catalog
    con.execute('INSERT INTO trips SELECT snapshot_id,?,route_id,service_id,?,1,headsign,block_id FROM trips WHERE snapshot_id=? AND trip_id=?',('ALT','SH2',sid,'T_NIGHT'))
    con.execute('INSERT INTO shapes VALUES(?,?,?,?,?)',(sid,'SH2',1,40.70,8.5))
    con.execute('INSERT INTO shapes VALUES(?,?,?,?,?)',(sid,'SH2',2,40.80,8.6))
    con.execute('INSERT INTO stop_times SELECT snapshot_id,?,stop_sequence,stop_id,arrival_sec,departure_sec,pickup_type,drop_off_type,timepoint FROM stop_times WHERE snapshot_id=? AND trip_id=?',('ALT',sid,'T_NIGHT'))
    con.commit()
    geo=network_geometry(con,date(2026,11,6),max_points=7)
    c=geo['metadata']['coverage'];reconcile(c)
    assert c['rendered_routes']==3 and c['routes_omitted_by_limits']==0
    assert c['partially_rendered_routes']==1 and geo['metadata']['truncated']
