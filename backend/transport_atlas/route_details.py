"""Source-scoped stop-sequence variants and calendar evidence for inspectors."""
from __future__ import annotations
from collections import defaultdict
import hashlib
import json
from .storage import rows, one
from .provenance import declared_info
from .archive_provenance import PUBLISHER_DIRECT, classify


def route_variants(con, snapshot_id, route_id):
    stops = rows(con, '''SELECT st.trip_id,st.stop_id,st.stop_sequence,st.pickup_type,st.drop_off_type,
        s.name FROM stop_times st JOIN trips t ON t.snapshot_id=st.snapshot_id AND t.trip_id=st.trip_id
        JOIN stops s ON s.snapshot_id=st.snapshot_id AND s.stop_id=st.stop_id
        WHERE t.snapshot_id=? AND t.route_id=? ORDER BY st.trip_id,st.stop_sequence''', (snapshot_id, route_id))
    by_trip = defaultdict(list)
    for stop in stops:
        by_trip[stop['trip_id']].append(stop)
    variants = {}
    trip_variants = {}
    for t in rows(con, 'SELECT * FROM trips WHERE snapshot_id=? AND route_id=? ORDER BY trip_id', (snapshot_id, route_id)):
        sequence = by_trip[t['trip_id']]
        signature = [snapshot_id, route_id, t['direction_id'], t['shape_id'],
                     [(s['stop_id'], s['pickup_type'], s['drop_off_type']) for s in sequence]]
        vid = hashlib.sha256(json.dumps(signature, ensure_ascii=False).encode()).hexdigest()[:20]
        trip_variants[t['trip_id']] = vid
        if vid not in variants:
            variants[vid] = dict(variant_id=vid, direction_id=t['direction_id'], shape_id=t['shape_id'],
                                 representative_trip_id=t['trip_id'], headsign=t['headsign'],
                                 stops=sequence, trip_templates=0)
        variants[vid]['trip_templates'] += 1
    return list(variants.values()), trip_variants


def route_evidence(con, snapshot_id, route_id):
    params = (snapshot_id, snapshot_id, route_id)
    services = 'SELECT DISTINCT service_id FROM trips WHERE snapshot_id=? AND route_id=?'
    provenance = one(con, '''SELECT s.*,f.publisher,f.landing_url,f.license_name
            FROM feed_snapshots s JOIN feed_sources f ON s.source_id=f.source_id
            WHERE s.snapshot_id=?''', (snapshot_id,))
    if provenance:
        provenance['feed_info'] = declared_info(provenance['raw_zip_path'], provenance['content_sha256'])
        # Only a publisher-direct record supplies a download URL; an archived copy's
        # archive URL is never presented as the official download (Phase 7C).
        evidence = classify(provenance, publisher=provenance['publisher'], landing_url=provenance['landing_url'])
        provenance['provenance_kind'] = evidence['kind']
        provenance['download_url'] = evidence['download_origin'] if evidence['kind'] == PUBLISHER_DIRECT else None
        provenance['landing_url'] = evidence['publisher']['landing_url'] or provenance['landing_url']
    return dict(
        provenance=provenance,
        calendars=rows(con, f'SELECT * FROM calendars WHERE snapshot_id=? AND service_id IN ({services}) ORDER BY service_id', params),
        exceptions=rows(con, f'SELECT * FROM calendar_exceptions WHERE snapshot_id=? AND service_id IN ({services}) ORDER BY service_date,service_id', params),
        warnings=rows(con, 'SELECT issue_code,COUNT(*) AS count FROM qa_issues WHERE snapshot_id=? GROUP BY issue_code', (snapshot_id,)))
