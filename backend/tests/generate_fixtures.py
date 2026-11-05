"""Recreate entirely synthetic GTFS ZIPs used by the test suite."""
from __future__ import annotations
import csv
import io
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

def csvbytes(rows):
    b=io.StringIO(newline='')
    writer=csv.DictWriter(b, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return b.getvalue().encode('utf-8-sig')

def build(path, feed):
    with ZipFile(path,'w',compression=ZIP_DEFLATED) as z:
        for name,rows in feed.items():
            if rows:
                z.writestr(name+'.txt',csvbytes(rows))

base={
 'agency': [dict(agency_name='SYNTHETIC Sardinia Transit',agency_url='https://example.invalid',agency_timezone='Europe/Rome')],
 'routes': [
  dict(route_id='1',route_short_name='N1',route_long_name='Synthetic overnight',route_type='3'),
  dict(route_id='E',route_short_name='E',route_long_name='Synthetic exact headways',route_type='3'),
  dict(route_id='F',route_short_name='F',route_long_name='Synthetic non-exact headways',route_type='3'),
  dict(route_id='S',route_short_name='S',route_long_name='Synthetic school-only',route_type='3')],
 'stops':[
  dict(stop_id='A',stop_name='Synthetic Sassari',stop_lat='40.72',stop_lon='8.56'),
  dict(stop_id='B',stop_name='Synthetic Porto Torres',stop_lat='40.84',stop_lon='8.40')],
 'trips':[
  dict(route_id='1',service_id='WEEK',trip_id='T_NIGHT',trip_headsign='Synthetic Porto Torres',shape_id='SH1'),
  dict(route_id='E',service_id='WEEK',trip_id='T_EXACT',trip_headsign='Synthetic Porto Torres',shape_id=''),
  dict(route_id='F',service_id='WEEK',trip_id='T_FREQ',trip_headsign='Synthetic Porto Torres',shape_id=''),
  dict(route_id='S',service_id='SCHOOL',trip_id='T_SCHOOL',trip_headsign='Synthetic Porto Torres',shape_id='')],
 'stop_times':[
  dict(trip_id=trip,arrival_time=a,departure_time=a,stop_id='A',stop_sequence='1')
  for trip,a in [('T_NIGHT','23:50:00'),('T_EXACT','09:00:00'),('T_FREQ','10:00:00'),('T_SCHOOL','12:00:00')]]+[
  dict(trip_id=trip,arrival_time=b,departure_time=b,stop_id='B',stop_sequence='2')
  for trip,b in [('T_NIGHT','25:15:00'),('T_EXACT','09:15:00'),('T_FREQ','10:15:00'),('T_SCHOOL','12:30:00')]],
 'calendar':[dict(service_id='WEEK',monday='0',tuesday='0',wednesday='0',thursday='0',friday='1',saturday='0',sunday='0',start_date='20261101',end_date='20261130')],
 'calendar_dates':[
  dict(service_id='WEEK',date='20261113',exception_type='2'),
  dict(service_id='SCHOOL',date='20261107',exception_type='1')],
 'shapes':[
  dict(shape_id='SH1',shape_pt_lat='40.72',shape_pt_lon='8.56',shape_pt_sequence='1'),
  dict(shape_id='SH1',shape_pt_lat='40.84',shape_pt_lon='8.40',shape_pt_sequence='2')],
 'frequencies':[
  dict(trip_id='T_EXACT',start_time='08:00:00',end_time='09:00:00',headway_secs='1200',exact_times='1'),
  dict(trip_id='T_FREQ',start_time='10:00:00',end_time='11:00:00',headway_secs='900',exact_times='0')]
}
other={
 'agency':[dict(agency_id='ZZ',agency_name='SYNTHETIC Other',agency_url='https://example.invalid',agency_timezone='Europe/Rome')],
 'routes':[dict(route_id='1',agency_id='ZZ',route_short_name='X1',route_long_name='Synthetic second feed',route_type='3')],
 'stops':base['stops'],
 'trips':[dict(route_id='1',service_id='WEEK',trip_id='T_NIGHT',trip_headsign='Synthetic',shape_id='')],
 'stop_times':base['stop_times'][:1]+base['stop_times'][4:5],
 'calendar':base['calendar'],
 'calendar_dates':[],
 'shapes':[],
 'frequencies':[]
}
if __name__=='__main__':
    root=Path(__file__).parent/'fixtures'
    root.mkdir(exist_ok=True)
    build(root/'synthetic_sardinia.zip',base)
    build(root/'synthetic_second_source.zip',other)
    print('Created deterministic fixture ZIP files')
