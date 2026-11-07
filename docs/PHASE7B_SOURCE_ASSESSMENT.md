# Phase 7B — Source assessment for the first real same-date History comparison (read-only)

**Status:** research only. No GTFS ZIP was downloaded or fetched. Nothing was imported, activated or
restored, and no backup was taken. The real database and its `selection_members` were not changed.
**Date of all observations:** 2026-11-07 (Europe/Rome and Asia/Seoul, same calendar day).
**Baseline:** branch `codex/local-real-feed-atlas`, HEAD = origin =
`b9894482d1e9f0d53f42622a49d3cce590b8b173`. At the start the worktree was clean apart from the two
known untracked files, `PHASE4_APPLY.md` and `codex-phase3f-review.patch`, which were left untouched.

> **No historical (older) archive has been obtained or analysed.** Every statement in this report
> about the older versions comes from public Transitland metadata. A difference such as 17 versus
> 15 routes is a difference in GTFS **records**. It is not evidence that real-world routes closed.

---

## 1. Recommendation (summary)

| | |
|---|---|
| **Selected operator** | **ASPO Olbia** (TransportAtlas source `aspo`, Transitland `f-spqb-aspoolbia`) |
| **Selected pair** | baseline **`b48388a26eeffbadde634b526dc3cb1acd8606ab`** (Transitland fetched 2026-08-28) → comparison **`a710bc7c867c9f1882988050ec0f4d904a7fcd4b`** (fetched 2026-10-27), which is the currently selected local snapshot |
| **Proposed service date** | **2026-11-06** (Friday) |
| **Local SHA-1 check** | **Exact match.** The local ASPO archive's SHA-1 is `a710bc7c…4fcd4b`, the same as Transitland's latest version. |
| **Older ZIP accessible without user action?** | **No.** The publisher keeps no archive. Transitland's raw historical downloads need an eligible account (free Hobbyist/Academic by application, or paid Transitland+). |
| **Backup choice** | ATP Sassari `d6c37667…` → `e9c2899f…`. The local SHA-1 also matches exactly, but the common window is shorter (2026-10-20 to 2026-10-31) and already in the past. |
| **Gate** | **NO-GO for any download until** (a) the user explicitly authorizes acquiring one specific version through an account they hold, and (b) a separate, reviewed code phase adds third-party-archive provenance (§3). |

---

## 2. Local evidence (read-only)

### 2.1 Commands run

```text
git branch --show-current; git rev-parse HEAD; git status --porcelain
sha256sum data/transport_atlas.duckdb                      (before and after)
python scripts/manage_snapshots.py status --db data/transport_atlas.duckdb --source aspo --json --verify-archives
python scripts/manage_snapshots.py status --db data/transport_atlas.duckdb --source atp_sassari --json --verify-archives
python -I <scratch>/inspect7b.py <aspo archive> <atp archive>   # SHA-1/SHA-256, feed_info, counts, 2026-11-06 activity
SnapshotHistory(open_readonly(db)).day_metrics(..., 2026-11-06)   # cross-check through the app's own History code
```

`--apply` was never passed, and no mutation helper was called. The backend and frontend were already
stopped: no Python process was running and the database was not locked. The database hash was
`3c308708af91052c06a5ed4479ec6b0d5332a71c056a9d25d8215da322523ca0` and its modification time
2026-11-06 17:33:51 +09:00, both identical before and after the read-only connections.

### 2.2 Snapshot inventory

| source | selected snapshot | local SHA-256 (TransportAtlas identity) | retrieved_at (UTC) | observed coverage | `feed_version` | archive present / SHA-256 verified / sidecar |
|---|---|---|---|---|---|---|
| `aspo` | `2fdf386102741d57c5045190da363097` (only snapshot) | `003278f32ac43d90a69bc4e603f124d261b80345c300fefa658ad8f0195cb382` | 2026-11-06T07:51:59Z | 2026-10-27 to 2026-12-31 | `1` | yes / yes / yes |
| `atp_sassari` | `2f029bb389634f4846d668bc473ed890` (only snapshot) | `5398fc76c8ce5ae0cea85e8d2e3f2f72ea88c050bcb5720469e5280c18dbe3e5` | 2026-11-06T08:26:27Z | 2026-10-20 to 2026-12-31 | none (no `feed_info.txt`) | yes / yes / yes |

The archives are `data/raw/aspo/003278f3….zip` (695,178 bytes) and
`data/raw/atp_sassari/5398fc76….zip` (1,592,843 bytes). Their provenance sidecars record official
downloads from `https://www.aspo.it/wp-content/uploads/open_data.zip` and
`https://atpsassari.it/media/files/atpss-gtfs.zip`, each with the catalog landing URL.

Status also lists one unreferenced archive, `data/raw/synthetic_smoke/8ffa967d….zip`. It is a known
synthetic fixture left by earlier smoke tests, it is harmless, and it was left alone.

### 2.3 Hash comparison (same algorithm only)

Transitland identifies a feed version by the **SHA-1 of the original ZIP bytes**. TransportAtlas
identifies a snapshot by the **SHA-256 of the same bytes**. The two digests are different functions,
so they are never compared directly, and prefixes from one are never matched against the other.
Each local archive was hashed with both algorithms, and only SHA-1 was compared with SHA-1.

| source | local SHA-1 | Transitland version SHA-1 (latest/active) | result |
|---|---|---|---|
| `aspo` | `a710bc7c867c9f1882988050ec0f4d904a7fcd4b` | `a710bc7c867c9f1882988050ec0f4d904a7fcd4b` | **exact 40-hex match** |
| `atp_sassari` | `e9c2899fcf6e19c2ad33f9a8583f5c60b253ef4d` | `e9c2899fcf6e19c2ad33f9a8583f5c60b253ef4d` | **exact 40-hex match** |

So the locally selected ASPO and ATP snapshots are byte-identical to Transitland's newer
versions. That makes them valid comparison sides without re-acquiring anything. Only the older
side is missing.

### 2.4 Contents of the local ASPO archive (counts only)

`feed_info.txt` contains publisher `ASPO S.p.a`, language `it`, `feed_start_date 20261027`,
`feed_end_date 20261231` and `feed_version 1`. This agrees with Transitland's active-version panel
and with the official dataset page (§4).

| file | rows | | file | rows |
|---|---|---|---|---|
| routes.txt | 15 | | calendar.txt | 18 |
| stops.txt | 422 | | calendar_dates.txt | 45 |
| trips.txt | 394 | | (also: agency, shapes, stop_times, transfers) | |

The 15 routes and 422 stops match Transitland's counts for `a710bc7c…`.

The ATP archive has 50 routes and 1,032 stops, matching Transitland for `e9c2899f…`. It has 3,404
trips, 4 calendar rows and 52 calendar_dates rows, and no `feed_info.txt`.

### 2.5 Service on 2026-11-06

Two independent methods give the same result for each feed: a direct `calendar` + `calendar_dates`
evaluation, and TransportAtlas `SnapshotHistory.day_metrics`.

| local snapshot | service day | available | routes scheduled | trips | stops served |
|---|---|---|---|---|---|
| ASPO `a710bc7c…` (selected) | Fri 2026-11-06 | **yes** | 15 / 15 | 319 | 421 |
| ATP `e9c2899f…` (selected) | Fri 2026-11-06 | yes | 46 / 50 | 1,023 | 917 |

- **ASPO older `b48388a2…`:** 2026-11-06 lies **inside** the published coverage envelope
  (2026-08-27 to 2026-12-31). Whether it actually has service that day is **unknown until the ZIP is
  acquired**. An envelope does not guarantee active trips: exceptions or weekday masks may remove
  them.
- **ATP older `d6c37667…`:** its envelope (2026-08-05 to 2026-10-31) does **not** include
  2026-11-06. For ATP, a same-date comparison must use a date between 2026-10-20 and 2026-10-31,
  and active service on that date is again unknown until acquired.

---

## 3. Why a Transitland ZIP cannot pass the current import path, and what to build instead

### 3.1 The current rules (Phase 7A, inspected at `b989448`)

- `configs/sardinia_sources.json` defines `aspo` with landing URL
  `https://www.sardegnamobilita.it/open-data/quadri-orari-aspo` and `download_hosts`
  `aspo.it` / `www.aspo.it`.
- `official.safe_url` accepts only `https` URLs whose host is in
  `ALLOWED_OFFICIAL_HOSTS = {sardegnamobilita.it, www.sardegnamobilita.it}` plus the source's
  `download_hosts`. It rejects credentials and non-443 ports.
- `admin._plan_staged` / `_provenance_problems`, used for a real (non-synthetic) catalog source:
  - A sidecar is required.
  - The sidecar's `source_id`, `sha256`, `bytes` and timezone-aware `retrieved_at` must match the staged
    bytes.
  - `download_url` must pass `safe_url` with the allowlist above.
  - `landing_url` must equal the catalog value exactly.
  - `--synthetic` is refused for catalog sources.
- `ingest.import_file(..., activate=False, expected_sha256=…)` makes the import non-activating and
  imports only the verified bytes, which is good. It also, unconditionally, runs
  `UPDATE feed_sources SET download_url = <sidecar download_url>`. `download_url` is stored **once
  per source**, not once per snapshot.
- `history.SnapshotHistory.inventory` orders snapshots by `retrieved_at`. History's documentation
  defines `retrieved_at` as "when this copy was downloaded".

### 3.2 Consequences for a historical ZIP from Transitland

1. **An honest sidecar is rejected.** The truthful `download_url` would be a Transitland endpoint
   (host `transit.land` or its storage host). That host is not in the allowlist, so the plan reports
   `provenance download_url rejected` and the import is refused. This is correct behaviour.
2. **A dishonest sidecar would pass, so it must never be written.** Transitland's version page
   shows the URL it originally fetched from, `https://www.aspo.it/wp-content/uploads/open_data.zip`.
   Copying that URL into a sidecar would satisfy every current check. It would also falsely claim
   that TransportAtlas downloaded these bytes from ASPO, and that `retrieved_at` is an ASPO
   retrieval. This report treats that as provenance falsification and rules it out.
3. **The source-level `download_url` would be overwritten.** Even with a new host on the
   allowlist, importing a third-party archive would replace the `aspo` row's official download URL
   with the archive URL. That misrepresents the current official snapshot.
4. **History's ordering would invert.** A copy obtained now of an August version would get a
   `retrieved_at` later than the current snapshot's (2026-11-06). The inventory would then list the
   older feed *after* the newer one. The Compare view lets the user choose the baseline explicitly,
   so the comparison itself still works, but the listing would mislead.
5. **`--synthetic` is not an escape hatch.** It is refused for catalog sources, and History would
   then (correctly) label the data as synthetic. A real feed must not be presented as a fixture
   either.

### 3.3 Proposed future design (a separate, reviewable code phase; **not implemented**)

- **Per-snapshot origin record.** Add a `snapshot_provenance` table, or equivalent columns, keyed by
  `snapshot_id`, with these fields:
  - `origin_kind`: `official_download` or `third_party_archive`;
  - `publisher_url`: the URL the publisher served, as reported by the archive;
  - `archive_provider`: e.g. `transitland`;
  - `archive_version_id`: the Transitland feed-version SHA-1;
  - `archive_fetched_at`: when the *provider* fetched it from the publisher;
  - `acquired_at`: when *we* obtained it;
  - `acquired_url`: provider endpoint, stripped of any API key;
  - `licence` and `attribution_text`.
  - Leave `feed_sources.download_url` meaning "the official current URL" only, and stop overwriting
    it for archive imports.
- **Separate sidecar schema** (e.g. `*.archive-provenance.json`, `schema: third_party_archive/1`),
  never accepted by the official path. Its validation would require:
  - an allowlisted provider host (`transit.land`, plus its documented download/storage hosts, to be
    confirmed when implementing);
  - a provider version ID equal to the **computed SHA-1** of the staged bytes, and a SHA-256 equal to
    the computed SHA-256;
  - the publisher URL to pass today's official `safe_url` for that catalog source, recorded only as
    *reported origin*;
  - no API key or token anywhere in the file.
- **CLI:** `manage_snapshots.py import --archive-provenance` would require the sidecar, refuse
  `--synthetic`, stay non-activating, and keep every Phase 7A safeguard (staging, backup,
  expected-SHA-256, expected-current, journal).
- **History:** order the inventory by origin time (`archive_fetched_at` when present, otherwise
  `retrieved_at`). Show "Archived copy from Transitland (originally published by ASPO S.p.a.,
  CC BY 4.0)". Never present an archive copy as an official download.
- **Tests:**
  - an honest archive sidecar is accepted;
  - an archive sidecar carrying an official `download_url` is rejected;
  - a SHA-1 mismatch is rejected;
  - `feed_sources.download_url` is unchanged after an archive import;
  - inventory ordering follows origin time;
  - sidecars and logs contain no secrets.

---

## 4. External evidence (HTML and metadata only, accessed 2026-11-07)

ZIP endpoints were neither clicked nor fetched. On Transitland version pages, "Download GTFS" is a
`<button>` with no `href`; it was not pressed.

| # | URL | Observed (2026-11-07) |
|---|---|---|
| E1 | https://www.transit.land/data/feeds/f-spqb-aspoolbia | ASPO Olbia: "archived 53 versions", last fetched about 23 h earlier. **Current** URL `https://www.aspo.it/wp-content/uploads/open_data.zip`; **historic** URL listed as `http://www.sardegnamobilita.it/opendata/dati_aspo.zip` (legacy, plain HTTP, not versioned, not followed). Licence `CC-BY-4.0`, `deed.it` URL, "Use without attribution: No", "Derived products: Yes". feed_info of the active version: publisher ASPO S.p.a, version name 1, 2026-10-27 to 2026-12-31. Version list: `a710bc7c` (2026-10-27, 10-27 to 12-31), `b48388a2` (2026-08-28, 08-27 to 12-31), `c14e9328` and `387259f6` (2026-08, ending 09-15), …, `14361d69` (2026-05-05, 05-04 to 12-31), … |
| E2 | …/f-spqb-aspoolbia/versions/b48388a26eeffbadde634b526dc3cb1acd8606ab | Fetched 2026-08-28 from `https://www.aspo.it/wp-content/uploads/open_data.zip`, SHA-1 `b48388a2…06ab`, service 2026-08-27 to 2026-12-31, 1 agency, **17 routes, 423 stops**, import status *Archived*. "The rest of this page requires a Transitland account." |
| E3 | …/f-spqb-aspoolbia/versions/a710bc7c867c9f1882988050ec0f4d904a7fcd4b | Fetched 2026-10-27, same URL, SHA-1 `a710bc7c…4fcd4b`, service 2026-10-27 to 2026-12-31, **15 routes, 422 stops**, *Imported / Active*, "Latest". |
| E4 | https://www.transit.land/data/feeds/f-spnp-atpsassari | ATP: "archived 47 versions"; tag **Unstable URL**. Current URL `https://atpsassari.it/media/files/atpss-gtfs.zip`; historic URLs `https://atpsassari.it/download/a248a7f4391f31bf993cef97192e7e3dd6a6c3f2.zip` and `http://www.atpsassari.it/public/atpss-gtfs.zip` (neither followed; the SHA-1-like name `a248a7f4…` is **not** either version of the ATP pair). CC BY 4.0 (deed.it), attribution required, derived products allowed. Versions: `e9c2899f` (2026-10-26, 10-20 to 12-31), `d6c37667` (2026-09-07, 08-05 to 10-31), `e0c9adea` (2026-07-01, 06-25 to 09-13), … |
| E5 | …/f-spnp-atpsassari/versions/d6c3766735ef67a7acfd3d447a24c5a9811909b4 | Fetched 2026-09-07 from `https://atpsassari.it/media/files/atpss-gtfs.zip`, service 2026-08-05 to 2026-10-31, 50 routes, 1,032 stops, *Archived*. |
| E6 | …/f-spnp-atpsassari/versions/e9c2899fcf6e19c2ad33f9a8583f5c60b253ef4d | Fetched 2026-10-26, service 2026-10-20 to 2026-12-31, 50 routes, 1,032 stops, *Imported / Active*. |
| E7 | https://www.transit.land/data/feeds/archive | Says that original ZIPs can be downloaded from feed pages or the REST API "for feeds where the original agency permits redistribution", and that downloads are included with Transitland+, Hobbyist/Academic and Enterprise but **not** Explorer or Pay As You Go. Feed versions are identified by SHA-1. |
| E8 | https://www.transit.land/plans-pricing | Feed Archive tiers: **Hobbyist/Academic** is free by application, 500 historical feeds/month, **no commercial use**. **Transitland+** is $125/month with 10 historical feeds/month included, then $3.00 each, commercial use allowed. Enterprise is custom. |
| E9 | Transitland Atlas `feeds/sardegnamobilita.it.dmfr.json` (GitHub `transitland/transitland-atlas`, latest commit touching it `e5434a1a`, 2026-10-16) | Licence fields for both feeds: CC BY 4.0, `use_without_attribution: no`, `create_derived_product: yes`. **`redistribution_allowed` is not set** in either record. |
| E10 | https://www.sardegnamobilita.it/open-data/quadri-orari-aspo (the catalog's landing URL) | R_SARDEG:TRASP_00003. Validity **2026/10/27 to 2026/12/31**. Last modified **2026/10/28**. Update frequency IRREG. **One resource**: the current `aspo.it/…/open_data.zip`. No historical or versioned links. |
| E11 | https://www.sardegnamobilita.it/open-data/aspo | This is a **tag page** linking to E10, not the dataset page. The brief's "dataset page" values match E10. |
| E12 | https://www.sardegnamobilita.it/open-data/quadri-orari-atp-sassari | R_SARDEG:TRASP_00004. Validity 2026/10/20 to 2026/12/31. Last modified 2026/10/27. One resource, the current ZIP. No archive. |
| E13 | https://www.sardegnamobilita.it/open-data | The datasets are released under **Creative Commons "Attribuzione 4.0 Internazionale" (CC BY 4.0)**, which permits sharing and adaptation for any purpose, including commercial use. |
| E14 | https://www.aspo.it/open-data/ | Lists a single attachment, `Open_data.zip` (the current file), plus a ticket-office CSV. It advises users to refresh their data weekly. **No historical archive and no licence text** on this page. |

### 4.1 Answers to the brief's questions

- **Is there a direct, legitimate, publisher-hosted historical archive link?**
  - **ASPO: no.** The publisher pages (E10, E14) offer only the current file, at a fixed URL that is
    overwritten in place. Transitland fetched both `b48388a2` and `a710bc7c` from that same URL (E2,
    E3). The legacy `sardegnamobilita.it/opendata/dati_aspo.zip` listed by Transitland is a
    non-versioned, plain-HTTP URL. Nothing indicates that it serves the August 2026 version, and
    it was not fetched.
  - **ATP: no verified one.** `atpsassari.it/download/a248a7f4….zip` looks content-addressed but
    is not either version of the pair. Whether it is live was not checked, since checking would
    fetch ZIP bytes.
- **Does third-party access need an eligible plan or account?** Yes (E2, E7, E8). Explorer
  (free) and Pay As You Go do not include downloads. Hobbyist/Academic is free but needs an approved
  application and is limited to non-commercial use. Transitland+ costs $125/month. **This report
  does not assume the user is eligible or holds an account.**
- **Is redistribution of the raw historical ZIP allowed?** The licence of the data is
  CC BY 4.0, under which redistribution is permitted with attribution (E13, E1, E4). Transitland
  gates its own downloads on agency permission (E7), but the Atlas record leaves
  `redistribution_allowed` unset (E9). **Whether the download control is enabled for these two
  versions could not be verified without an account.** Regardless of that, TransportAtlas must
  never commit or re-publish raw ZIPs.
- **What attribution is required?** CC BY 4.0 with attribution, i.e. "Use without attribution:
  No". Suggested wording: "Data: ASPO S.p.a. / Regione Autonoma della Sardegna (Sardegna Mobilità
  open data), CC BY 4.0. Historical version obtained from the Transitland Feed Archive (feed version
  SHA-1 b48388a2…)." Any changes made to the data should also be stated.

### 4.2 Search snippets compared with the pages

Every value in this report comes from the pages themselves as rendered on 2026-11-07. None comes
from search snippets. Relative times on Transitland ("2 months ago", "11 days ago") are relative to
the access date and agree with the absolute dates shown. Two deviations from the brief were found:
- the brief's ASPO "dataset page" (`/open-data/aspo`) is actually a tag page (E11 vs E10);
- the brief's ASPO values for E10 are confirmed.

---

## 5. ASPO versus ATP

| criterion | ASPO `b48388a2` → `a710bc7c` | ATP `d6c37667` → `e9c2899f` |
|---|---|---|
| Newer side available locally | **yes**: SHA-1 exact match, selected | **yes**: SHA-1 exact match, selected |
| Publisher archive of the older side | none | none verified |
| Third-party access | Transitland, eligible account required | same |
| Common coverage window | **2026-10-27 to 2026-12-31** (66 days; the proposed 2026-11-06 is inside) | 2026-10-20 to 2026-10-31 (12 days, already in the past) |
| Service on the proposed date (newer side) | 319 trips, 15 routes | 1,023 trips, 46 routes |
| Service on the date (older side) | unknown until acquired | unknown until acquired |
| Version adjacency | `b48388a2` is the version immediately before `a710bc7c` (E1) | `d6c37667` is the version immediately before `e9c2899f` (E4) |
| Visible metadata difference | 17 → 15 routes, 423 → 422 stops: GTFS record differences only | 50 → 50 routes, 1,032 → 1,032 stops: summary counts identical, so changes, if any, sit below this level |
| `feed_info.txt` | present (version name, declared validity) | absent |
| Size and interpretability | small (394 trips); a diff is reviewable by hand | about 3,400 trips; a diff is harder to sanity-check |
| Feed stability | URL stable | Transitland tag "Unstable URL" |
| Cost / quota | 1 historical download | 1 historical download |
| Operational risk | low: one small, non-activating import | low, but more data and a less informative date window |

**Recommendation unchanged: ASPO.** No finding changes the ranking. One alternative is worth
recording: `14361d69…` (fetched 2026-05-05, 2026-05-04 to 2026-12-31, E1) also covers 2026-11-06.
It could serve as a second ASPO baseline across a longer gap, but its route and stop counts were not
inspected because the version page needs an account.

---

## 6. Future authorization checklist (**not executed**; each step needs its own explicit approval)

**Phase 7C (code; separate, reviewable, no real data):** implement §3.3:
- the per-snapshot origin record;
- the archive-provenance sidecar and its validation;
- the non-overwriting `feed_sources.download_url`;
- origin-time ordering in History;
- the tests, run against synthetic and SQLite/disposable DuckDB fixtures only.

Commit locally, then have it reviewed. **The current importer must not be used for a Transitland
ZIP before this lands.**

**Phase 7D (data; each step needs the user's explicit approval):**

1. **Acquisition.** The user confirms which account they hold and that they are eligible (e.g. an
   approved Hobbyist/Academic account for non-commercial use, or Transitland+). The user then
   authorizes downloading exactly one file:
   - provider: Transitland Feed Archive;
   - feed: `f-spqb-aspoolbia`;
   - version: `b48388a26eeffbadde634b526dc3cb1acd8606ab`.

   It is downloaded to a new, empty folder outside `data/raw`. Any API key is supplied by the user
   and never written to disk, logs, sidecars or commits.
2. **Checksum.** SHA-1 of the file must equal `b48388a26eeffbadde634b526dc3cb1acd8606ab`; abort
   otherwise. Record the SHA-256 and the byte count.
3. **Provenance.** Write the archive-provenance sidecar (§3.3):
   - provider: Transitland;
   - version SHA-1;
   - `archive_fetched_at`: 2026-08-28 (from E2);
   - publisher URL reported by Transitland;
   - acquisition time;
   - CC BY 4.0 and the attribution text.

   **Never** write a sidecar that claims an official download.
4. **External validation.** Run the MobilityData GTFS validator on the file, read-only, and keep
   the report under `data/validation/…` (git-ignored). Record its errors and warnings. Do not alter
   the feed.
5. **Preflight.**
   - Stop the services.
   - `manage_snapshots.py status --real-database --verify-archives`: confirm the `aspo` selection is
     still `2fdf3861…` (SHA-256 `003278f3…`) and that the database hash is recorded.
   - `manage_snapshots.py import … --real-database` **without** `--apply`: review the plan.
6. **Backup and non-activating import.** With the user's approval of the plan,
   `manage_snapshots.py import … --real-database --apply`. This takes a verified backup in
   `data/backups`, imports with `activate=False`, journals the change and verifies it.
7. **Selection check.** Confirm that `selection_members` is unchanged (`aspo` → `2fdf3861…`) and
   that the atlas default is unaffected.
8. **First comparison.** In History (or through the API), compare baseline `b48388a2` with
   `a710bc7c` using `policy=same_date` on 2026-11-06. If the older feed turns out to have no service
   that day, report that instead of choosing another date silently. Save the JSON and screenshots
   outside git.
9. **Report.** Document the comparison:
   - counts and record-level differences, described as GTFS record changes rather than real-world
     changes;
   - the validator findings;
   - the CC BY 4.0 attribution, naming ASPO S.p.a. / Sardegna Mobilità and the Transitland archive;
   - the limitations: one pair, one date, metadata-level origin for the older side.

   Do not activate the historical snapshot, and do not push without a separate authorization.

---

## 7. Go / no-go gate

| condition | state |
|---|---|
| Newer side present locally, SHA-1 verified against Transitland | **met** (ASPO and ATP) |
| Proposed date inside both ASPO envelopes; newer side has service on it | **met** (envelopes per E2/E3; 319 trips locally) |
| Older side's service on 2026-11-06 | **unknown until acquired** |
| Publisher-hosted historical archive | **not available** |
| User holds an eligible Transitland account and authorizes one specific download | **not given**: blocked |
| Import path that records third-party-archive provenance honestly | **not implemented**: blocked (Phase 7C) |
| Licence | CC BY 4.0, attribution required: compatible with local analysis and the attributed reporting of derived results |

**Decision: NO-GO for downloading or importing now.** It becomes **GO** only when both blocked rows
are resolved:
- the user authorizes acquiring `f-spqb-aspoolbia@b48388a2…` through an account they hold;
- a reviewed Phase 7C change can record its provenance without impersonating the official publisher.

## 8. Unresolved facts

- Whether Transitland's "Download GTFS" is enabled for `b48388a2` and `d6c37667`, given that
  `redistribution_allowed` is unset in the Atlas record.
- The actual service of the older ASPO version on 2026-11-06, and what the 17-versus-15 route
  difference consists of.
- Whether the legacy `sardegnamobilita.it/opendata/dati_aspo.zip` or ATP's
  `atpsassari.it/download/a248a7f4….zip` is live, and what it contains. These were deliberately not
  fetched.
- The exact Transitland download/storage hostnames that a Phase 7C allowlist would need. These must
  be taken from Transitland documentation when implementing, not guessed.
