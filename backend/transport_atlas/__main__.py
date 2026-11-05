"""Command-line interface. All commands operate on local data only."""
from __future__ import annotations
import argparse
import json
import os
from datetime import date
from pathlib import Path
from .storage import connect
from .ingest import import_file
from .schedule import daily_summary, stop_board

def main(argv=None):
    p=argparse.ArgumentParser(prog='transport_atlas',description='Private GTFS analysis')
    p.add_argument('--db',default=os.getenv('TRANSPORT_ATLAS_DB','data/transport_atlas.duckdb'))
    p.add_argument('--backend',choices=['duckdb','sqlite'],default=os.getenv('TRANSPORT_ATLAS_DB_BACKEND','duckdb'))
    sub=p.add_subparsers(dest='command',required=True)
    add=sub.add_parser('import',help='Import GTFS ZIP atomically')
    add.add_argument('--file',type=Path,required=True)
    add.add_argument('--source',required=True)
    add.add_argument('--publisher')
    add.add_argument('--landing-url')
    add.add_argument('--license')
    add.add_argument('--archive',default='data/raw')
    s=sub.add_parser('summary',help='Show active trips by dataset and service day')
    s.add_argument('--date',type=date.fromisoformat,required=True)
    d=sub.add_parser('departures',help='Stop-local departure board')
    d.add_argument('--snapshot',required=True)
    d.add_argument('--stop',required=True)
    d.add_argument('--date',type=date.fromisoformat,required=True)
    d.add_argument('--start-hour',type=int,default=0)
    d.add_argument('--end-hour',type=int,default=24)
    sub.add_parser('sources',help='Print currently selected source snapshots')
    serve=sub.add_parser('serve',help='Run localhost API')
    serve.add_argument('--port',type=int,default=8000)
    args=p.parse_args(argv)
    if args.command=='serve':
        import uvicorn
        os.environ['TRANSPORT_ATLAS_DB']=str(args.db)
        os.environ['TRANSPORT_ATLAS_DB_BACKEND']=str(args.backend)
        uvicorn.run('transport_atlas.api:app',host='127.0.0.1',port=args.port,log_level='info')
        return
    con=connect(args.db,args.backend)
    try:
        if args.command=='import':
            result=import_file(con,args.file,source_id=args.source,publisher=args.publisher,
               landing_url=args.landing_url,license_name=args.license,archive_dir=args.archive)
        elif args.command=='summary':result=daily_summary(con,args.date)
        elif args.command=='departures':result=stop_board(con,args.snapshot,args.stop,args.date,start_hour=args.start_hour,end_hour=args.end_hour)
        else:
            from .schedule import active_snapshots
            result=active_snapshots(con)
        print(json.dumps(result,indent=2,ensure_ascii=False,default=str))
    finally:con.close()

if __name__=='__main__': main()
