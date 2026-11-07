"""Read-only, local MobilityData GTFS Schedule validator for selected archives.

Uses the selected immutable GTFS snapshot in DuckDB, not a guessed or current
provider ZIP. Validator JAR is pinned by digest. Never uploads GTFS data.
Reports live only in ignored data/local-verification/phase4-validator/.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import ssl
import subprocess
import sys
import urllib.request
from uuid import uuid4
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = "8.0.1"
JAR_FILE = f"gtfs-validator-{VERSION}-cli.jar"
JAR_SHA256 = "19293ddd9b6f954f216d4f12054bd8a3232921751c4484339e339764a91000e2"
JAR_URL = f"https://github.com/MobilityData/gtfs-validator/releases/download/v{VERSION}/{JAR_FILE}"
MAX_JAR_BYTES = 100 * 1024 * 1024
KNOWN_SOURCES = {"arst", "aspo", "atp_sassari", "trenitalia_sardegna"}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


class _HttpsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    """Refuse a downgrade before the redirected request is sent, not after."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.startswith("https://"):
            raise RuntimeError("Validator asset redirected to non-HTTPS URL")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener() -> urllib.request.OpenerDirector:
    # Same policy as transport_atlas.official: on Windows, the OS chain builder
    # fetches missing intermediates (Python's bundled check fails on GitHub's
    # chain there). Certificate and hostname verification stay required.
    if sys.platform == "win32":
        import truststore
        context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    else:
        context = ssl.create_default_context()
    return urllib.request.build_opener(_HttpsOnlyRedirects(), urllib.request.HTTPSHandler(context=context))


def download_validator(target: Path, *, url: str = JAR_URL) -> Path:
    """Explicit opt-in install; HTTPS, bounded download, published digest."""
    if target.exists():
        verify_jar(target)
        return target
    if url != JAR_URL:
        raise ValueError("Unexpected validator download URL")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + "." + uuid4().hex + ".part")
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "TransportAtlas-LocalValidator/1"})
        with _opener().open(request, timeout=120) as response, tmp.open("wb") as sink:
            if not response.geturl().startswith("https://"):
                raise RuntimeError("Validator asset redirected to non-HTTPS URL")
            total = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_JAR_BYTES:
                    raise RuntimeError("Validator JAR exceeds maximum download size")
                sink.write(chunk)
        verify_jar(tmp)
        tmp.replace(target)
        return target
    finally:
        tmp.unlink(missing_ok=True)


def verify_jar(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"Validator missing: {path}; use --install-validator")
    actual = sha256(path)
    if actual != JAR_SHA256:
        raise ValueError(f"Untrusted validator JAR SHA-256: expected {JAR_SHA256}, found {actual}")


def selected_archives(db: Path, selected: list[str], *, root: Path = ROOT) -> list[dict]:
    """Only current selected, ready snapshot records. DB is opened read-only."""
    if not db.is_file():
        raise FileNotFoundError(f"Selected GTFS database does not exist: {db}")
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError("Install project Python dependencies (duckdb required)") from exc
    connection = duckdb.connect(str(db), read_only=True)
    try:
        result = connection.execute("""SELECT m.source_id,s.snapshot_id,s.raw_zip_path,
                                s.content_sha256,s.retrieved_at
            FROM selection_members m JOIN feed_snapshots s
                ON s.snapshot_id=m.snapshot_id
            WHERE s.import_status='ready' ORDER BY m.source_id""").fetchall()
    finally:
        connection.close()
    by_source = {r[0]: r for r in result}
    selected_items = []
    for source in selected:
        if source not in by_source:
            raise ValueError(f"Source {source!r} has no selected, ready GTFS snapshot")
        _, snapshot_id, raw_path, digest, retrieved_at = by_source[source]
        original = Path(raw_path)
        candidates = [original if original.is_absolute() else root / original,
                      root / "data" / "raw" / source / f"{digest}.zip"]
        candidates = list(dict.fromkeys(candidates))
        existing = [p for p in candidates if p.is_file()]
        if not existing:
            raise FileNotFoundError(f"Archived ZIP missing for {source}; expected one of: {candidates}")
        archive = next((p for p in existing if sha256(p) == digest), None)
        if archive is None:
            raise ValueError(f"No archived ZIP matches selected SHA-256 for {source}; expected {digest}")
        with zipfile.ZipFile(archive) as z:
            if z.testzip() is not None:
                raise ValueError(f"ZIP CRC mismatch for {source}")
        sidecar = archive.with_suffix('.zip.provenance.json')
        provenance = None
        if sidecar.is_file():
            provenance = json.loads(sidecar.read_text(encoding='utf-8'))
            if provenance.get('source_id') != source or provenance.get('sha256') != digest:
                raise ValueError(f"Provenance sidecar mismatch for {source}")
        selected_items.append({"source": source, "snapshot_id": snapshot_id,
                               "archive": str(archive.resolve()), "sha256": digest,
                               "retrieved_at": str(retrieved_at),
                               "download_provenance": provenance})
    return selected_items


def java_major(java: str) -> int:
    result = subprocess.run([java, "-version"], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=30, check=False)
    output = result.stderr + "\n" + result.stdout
    # Anchor on the quoted version so JAVA_TOOL_OPTIONS banners (e.g. -Xmx512m)
    # cannot be mistaken for it. Legacy "1.8.0_x" means major 8.
    match = re.search(r'version\s+"(\d+)(?:\.(\d+))?', output)
    if result.returncode != 0 or match is None:
        raise RuntimeError(f"Cannot determine Java version: {output[:300]}")
    major = int(match.group(1))
    if major == 1 and match.group(2):
        major = int(match.group(2))
    if major < 17:
        raise RuntimeError(f"MobilityData validator requires Java 17+, found {major}")
    return major


def evaluate_reports(output: Path, returncode: int) -> tuple[str, str]:
    """Exit zero alone is NOT evidence of valid GTFS or no ERROR notices."""
    if returncode != 0:
        return "validator_failed", f"Process exit code {returncode}"
    try:
        report = json.loads((output / "report.json").read_text(encoding="utf-8"))
        errors_text = (output / "system_errors.json").read_text(encoding="utf-8").strip()
        system_errors = json.loads(errors_text) if errors_text else []
        if not isinstance(report, dict):
            raise ValueError("report.json root must be a JSON object")
        if not (output / "report.html").is_file():
            raise ValueError("report.html missing")
    except (OSError, ValueError) as exc:
        return "unverified", f"Incomplete or invalid validator output: {exc}"
    # v8 writes {"notices": [...]}; an empty notices list means no system errors.
    if isinstance(system_errors, dict) and set(system_errors) <= {"notices"}:
        system_errors = system_errors.get("notices", [])
    if not isinstance(system_errors, list):
        return "unverified", "Unrecognized system_errors.json structure; inspect manually"
    if system_errors:
        return "validator_failed", f"{len(system_errors)} system error notice(s); inspect system_errors.json"
    return "report_generated", "Inspect report.json notices and report.html; no claim of error-free feed"


def run_one(item: dict, *, jar: Path, java: str, date: str, threads: int,
            heap_mb: int, output_root: Path, timeout_seconds: int, force: bool) -> dict:
    source, digest = item["source"], item["sha256"]
    name = f"{source}-{digest[:12]}-{date}-v{VERSION}"
    output = output_root / name
    evidence_file = output / "evidence.json"
    if not force and evidence_file.exists():
        try:
            saved = json.loads(evidence_file.read_text(encoding="utf-8"))
            if saved.get("input_sha256") == digest and saved.get("validator_sha256") == JAR_SHA256 and saved.get("date") == date and saved.get("status") == "report_generated":
                status, _ = evaluate_reports(output, 0)
                if status == "report_generated":
                    return {**saved, "reused_existing_report": True}
        except (ValueError, OSError):
            pass
    if output.exists():
        output = output_root / f"{name}-retry-{uuid4().hex[:8]}"
        # The record belongs with its own report; never overwrite an earlier run's.
        evidence_file = output / "evidence.json"
    output.mkdir(parents=True, exist_ok=False)
    # -svu: no network version check, so validation stays fully offline.
    args = [java, f"-Xmx{heap_mb}m", "-jar", str(jar), "-i", item["archive"],
            "-o", str(output), "-d", date, "-c", "it", "-t", str(threads), "-svu"]
    record = {"source": source, "snapshot_id": item["snapshot_id"],
              "input_archive": item["archive"], "input_sha256": digest,
              "retrieved_at": item["retrieved_at"],
              "download_provenance": item.get("download_provenance"),
              "validator_version": VERSION,
              "validator_sha256": JAR_SHA256, "date": date,
              "report_directory": str(output), "reused_existing_report": False,
              "command": ["java", f"-Xmx{heap_mb}m", "-jar", JAR_FILE,
                          "-i", "<immutable-archive>", "-o", "<local-evidence>",
                          "-d", date, "-c", "it", "-t", str(threads), "-svu"]}
    try:
        # Decode explicitly: the Windows locale code page cannot decode all JVM output.
        result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=timeout_seconds, check=False)
        (output / "validator.stdout.log").write_text(result.stdout, encoding="utf-8")
        (output / "validator.stderr.log").write_text(result.stderr, encoding="utf-8")
        record["exit_code"] = result.returncode
        record["status"], record["reason"] = evaluate_reports(output, result.returncode)
    except (subprocess.TimeoutExpired, OSError) as exc:
        record.update(status="validator_failed", reason=f"{type(exc).__name__}: {exc}")
    record["validated_at_utc"] = datetime.now(timezone.utc).isoformat()
    evidence_file.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return record


def main(argv: list[str] | None = None) -> int:
    # A console in a legacy code page (e.g. cp949) must not abort a batch midway.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", default="arst,aspo,atp_sassari,trenitalia_sardegna")
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "transport_atlas.duckdb")
    parser.add_argument("--jar", type=Path, default=ROOT / "data" / "tools" / JAR_FILE)
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "local-verification" / "phase4-validator")
    parser.add_argument("--java", default="java")
    parser.add_argument("--date", default="2026-11-06")
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--heap-mb", type=int, default=4096)
    parser.add_argument("--timeout-seconds", type=int, default=2400)
    parser.add_argument("--install-validator", action="store_true", help="Download pinned JAR (never GTFS feeds)")
    parser.add_argument("--dry-run", action="store_true", help="Verify selected local ZIPs; do not invoke Java")
    parser.add_argument("--force", action="store_true", help="Re-run into a new output folder, preserving old reports")
    args = parser.parse_args(argv)
    sources = [s.strip() for s in args.sources.split(",")]
    if not sources or any(s not in KNOWN_SOURCES for s in sources) or len(set(sources)) != len(sources):
        parser.error("--sources must be unique known source IDs")
    try:
        from datetime import date as Date
        Date.fromisoformat(args.date)
        if args.threads < 1 or args.heap_mb < 512 or args.timeout_seconds < 60:
            parser.error("threads >= 1; heap-mb >= 512; timeout-seconds >= 60 required")
        items = selected_archives(args.db.resolve(), sources)
        if args.dry_run:
            print(json.dumps({"dry_run": True, "source_archives_verified": items}, indent=2))
            return 0
        if args.install_validator:
            download_validator(args.jar.resolve())
        verify_jar(args.jar.resolve())
        version = java_major(args.java)
        print(f"Verified MobilityData v{VERSION} and Java {version}. Checking archived feeds, not modifying DB.", flush=True)
        output_root = args.output.resolve()
        # Reports and logs must remain in the ignored data/ tree; fail closed.
        try:
            output_root.relative_to((ROOT / "data").resolve())
        except ValueError as exc:
            raise ValueError("--output must remain inside the repository's ignored data/ directory") from exc
        output_root.mkdir(parents=True, exist_ok=True)
        results = []
        for item in items:
            result = run_one(item, jar=args.jar.resolve(), java=args.java, date=args.date,
                             threads=args.threads, heap_mb=args.heap_mb,
                             output_root=output_root, timeout_seconds=args.timeout_seconds,
                             force=args.force)
            results.append(result)
            print(f"{item['source']}: {result['status']} - {result.get('reason', '')}", flush=True)
        summary = {"validator_version": VERSION, "date": args.date,
                   "results": results,
                   "note": "Validation reports need interpretation. ERROR notices do not automatically mean the importer altered the input."}
        index = output_root / f"summary-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid4().hex[:6]}.json"
        index.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Local summary: {index}")
        return 0 if all(r["status"] == "report_generated" for r in results) else 2
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile) as exc:
        print(f"External validation could not proceed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
