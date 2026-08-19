"""Acquire the Phase 5 benchmark corpus from public, citable sources.

Nothing here generates a formula. Every instance is downloaded from a named
public source, verified against a published checksum where one exists, and
recorded in the manifest with its own SHA-256 so the corpus can be reproduced
exactly. Formulas are never edited; the only transformation applied is xz/gzip
decompression, and both the compressed and decompressed digests are recorded.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import lzma
import shutil
import tarfile
import threading
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

UA = {"User-Agent": "lkk-hsat-phase5/1.0 (research benchmark acquisition)"}
SATLIB_ROOT = "https://www.cs.ubc.ca/~hoos/SATLIB/Benchmarks/SAT"
ZENODO_SC2024 = (
    "https://zenodo.org/api/records/15095752/files/"
    "sat-competition-2024-main-benchmarks.zip/content"
)
ZENODO_SC2024_MD5 = "4873f72ffb3a5ae89b39ea08be30a7df"
ZENODO_SC2024_DOI = "10.5281/zenodo.15095752"


@dataclass(frozen=True)
class SatlibSet:
    """One SATLIB archive and the domain it is drawn from.

    `domain` records what the instances actually encode, which is what §4 cares
    about; it is provenance, not a structural classification. Structure is
    decided later from LKK telemetry alone.
    """

    name: str
    archive: str
    domain: str
    note: str
    expected: str = ""


# Chosen to cover the Phase 5 priority domains plus the required negative
# controls. Sizes are small, so the whole corpus is cheap to re-acquire.
SATLIB_SETS: tuple[SatlibSet, ...] = (
    SatlibSet("bmc-ibm", "BMC/bmc.tar.gz", "eda_formal_verification",
              "IBM bounded model checking, circuit verification"),
    SatlibSet("ssa", "DIMACS/SSA/ssa.tar.gz", "eda_circuit_fault",
              "single-stuck-at circuit fault analysis"),
    SatlibSet("bf", "DIMACS/BF/bf.tar.gz", "eda_circuit_fault",
              "circuit fault analysis, bridge faults"),
    SatlibSet("beijing", "Bejing/Bejing.tar.gz", "planning_scheduling",
              "Beijing scheduling benchmark set"),
    SatlibSet("planning-logistics", "PLANNING/logistics.tar.gz", "planning_scheduling",
              "logistics planning, resource routing"),
    SatlibSet("planning-blocksworld", "PLANNING/blocksworld.tar.gz", "planning_scheduling",
              "blocksworld planning"),
    SatlibSet("hanoi", "DIMACS/HANOI/hanoi.tar.gz", "planning_scheduling",
              "towers of hanoi planning"),
    SatlibSet("ais", "AIS/ais.tar.gz", "matching_capacity",
              "all-interval series, permutation/cardinality structure"),
    SatlibSet("pigeon-hole", "DIMACS/PHOLE/pigeon-hole.tar.gz", "matching_capacity",
              "DIMACS pigeonhole, pure cardinality/matching structure",
              "UNSATISFIABLE"),
    SatlibSet("flat200", "GCP/flat200-479.tar.gz", "matching_capacity",
              "flat graph colouring, at-most-one per vertex"),
    SatlibSet("flat100", "GCP/flat100-239.tar.gz", "matching_capacity",
              "flat graph colouring, at-most-one per vertex"),
    SatlibSet("flat75", "GCP/flat75-180.tar.gz", "matching_capacity",
              "flat graph colouring, at-most-one per vertex"),
    SatlibSet("gcp-large", "DIMACS/GCP/gcp-large.tar.gz", "matching_capacity",
              "DIMACS large graph colouring"),
    SatlibSet("parity", "DIMACS/PARITY/parity.tar.gz", "crafted_parity",
              "parity learning instances"),
    SatlibSet("pret", "DIMACS/PRET/pret.tar.gz", "crafted_parity",
              "graph 2-colouring with parity constraints", "UNSATISFIABLE"),
    SatlibSet("dubois", "DIMACS/DUBOIS/dubois.tar.gz", "crafted_parity",
              "Dubois chained xor instances", "UNSATISFIABLE"),
    SatlibSet("aim", "DIMACS/AIM/aim.tar.gz", "crafted_structured",
              "artificially generated 3-SAT with controlled structure"),
    SatlibSet("jnh", "DIMACS/JNH/jnh.tar.gz", "crafted_structured",
              "random instances with variable clause length"),
    SatlibSet("lran", "DIMACS/LRAN/f.tar.gz", "random_control",
              "large random instances"),
    SatlibSet("uf250", "RND3SAT/uf250-1065.tar.gz", "random_control",
              "uniform random 3-SAT at threshold, satisfiable", "SATISFIABLE"),
    SatlibSet("uuf250", "RND3SAT/uuf250-1065.tar.gz", "random_control",
              "uniform random 3-SAT at threshold, unsatisfiable", "UNSATISFIABLE"),
    SatlibSet("uf200", "RND3SAT/uf200-860.tar.gz", "random_control",
              "uniform random 3-SAT at threshold, satisfiable", "SATISFIABLE"),
    SatlibSet("uuf200", "RND3SAT/uuf200-860.tar.gz", "random_control",
              "uniform random 3-SAT at threshold, unsatisfiable", "UNSATISFIABLE"),
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest_file(path: Path, algorithm: str = "sha256") -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _content_length(url: str) -> int | None:
    try:
        request = urllib.request.Request(url, method="HEAD", headers=UA)
        with urllib.request.urlopen(request, timeout=60) as response:
            if response.headers.get("Accept-Ranges") != "bytes":
                return None
            length = response.headers.get("Content-Length")
            return int(length) if length else None
    except Exception:
        return None


def _fetch_segment(url: str, start: int, end: int, part: Path,
                   errors: list[str]) -> None:
    """Fetch one byte range, skipping it if the part file is already complete."""
    want = end - start + 1
    if part.exists() and part.stat().st_size == want:
        return
    try:
        request = urllib.request.Request(
            url, headers={**UA, "Range": f"bytes={start}-{end}"})
        with urllib.request.urlopen(request, timeout=300) as response, \
                part.open("wb") as stream:
            shutil.copyfileobj(response, stream, length=1 << 20)
        if part.stat().st_size != want:
            errors.append(f"segment {start}-{end}: short read")
    except Exception as exc:  # recorded, retried by the caller
        errors.append(f"segment {start}-{end}: {exc}")


def download_parallel(url: str, target: Path, connections: int = 8,
                      attempts: int = 4) -> None:
    """Range-parallel download with resume.

    Zenodo throttles each connection to roughly 1.2 MB/s but allows several at
    once, so a single stream is about five times slower than eight. Completed
    segments survive an interrupted run, so re-invoking acquisition resumes
    rather than restarting.
    """
    total = _content_length(url)
    if total is None:
        request = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(request, timeout=300) as response, \
                target.open("wb") as stream:
            shutil.copyfileobj(response, stream, length=1 << 20)
        return
    parts_dir = target.parent / (target.name + ".parts")
    parts_dir.mkdir(parents=True, exist_ok=True)
    span = (total + connections - 1) // connections
    bounds = [(i * span, min(total - 1, (i + 1) * span - 1)) for i in range(connections)]
    bounds = [(s, e) for s, e in bounds if s <= e]
    for attempt in range(attempts):
        errors: list[str] = []
        threads = []
        for index, (start, end) in enumerate(bounds):
            part = parts_dir / f"{index:03d}.part"
            thread = threading.Thread(target=_fetch_segment,
                                      args=(url, start, end, part, errors))
            thread.start()
            threads.append(thread)
        for thread in threads:
            thread.join()
        if not errors:
            break
        if attempt == attempts - 1:
            raise RuntimeError(f"download failed after {attempts} attempts: {errors[:3]}")
    with target.open("wb") as stream:
        for index in range(len(bounds)):
            part = parts_dir / f"{index:03d}.part"
            with part.open("rb") as chunk:
                shutil.copyfileobj(chunk, stream, length=1 << 20)
    for index in range(len(bounds)):
        (parts_dir / f"{index:03d}.part").unlink(missing_ok=True)
    parts_dir.rmdir()


def download(url: str, target: Path, expected_md5: str | None = None,
             parallel: bool = False) -> dict[str, Any]:
    """Fetch a URL to disk and verify it if a published digest is known."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists() or target.stat().st_size == 0:
        if parallel:
            download_parallel(url, target)
        else:
            request = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(request, timeout=300) as response, \
                    target.open("wb") as stream:
                shutil.copyfileobj(response, stream, length=1 << 20)
    record = {
        "url": url,
        "path": target.as_posix(),
        "bytes": target.stat().st_size,
        "sha256": digest_file(target),
        "md5": digest_file(target, "md5"),
        "published_md5": expected_md5 or "",
    }
    if expected_md5 and record["md5"] != expected_md5:
        raise RuntimeError(f"checksum mismatch for {url}: "
                           f"expected {expected_md5}, got {record['md5']}")
    record["checksum_verified"] = bool(expected_md5)
    return record


def looks_like_cnf(name: str) -> bool:
    lowered = name.lower()
    return lowered.endswith((".cnf", ".cnf.gz", ".cnf.xz", ".cnf.bz2"))


def write_instance(data: bytes, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)


def acquire_satlib(root: Path, sets: Iterable[SatlibSet]) -> list[dict[str, Any]]:
    """Download and unpack the SATLIB archives, one directory per set."""
    rows: list[dict[str, Any]] = []
    for item in sets:
        url = f"{SATLIB_ROOT}/{item.archive}"
        archive = root / "_archives" / f"{item.name}.tar.gz"
        try:
            archive_record = download(url, archive)
        except Exception as exc:  # a missing SATLIB archive must not abort the corpus
            rows.append({"source": "SATLIB", "set": item.name, "instance": "",
                         "status": f"ARCHIVE_FAILED: {exc}", "domain": item.domain})
            continue
        target_dir = root / "satlib" / item.name
        target_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar.getmembers():
                if not member.isfile() or not looks_like_cnf(member.name):
                    continue
                handle = tar.extractfile(member)
                if handle is None:
                    continue
                raw = handle.read()
                name = Path(member.name).name
                destination = target_dir / name
                write_instance(raw, destination)
                rows.append({
                    "source": "SATLIB",
                    "source_url": url,
                    "source_archive_sha256": archive_record["sha256"],
                    "set": item.name,
                    "domain": item.domain,
                    "provenance_note": item.note,
                    "instance": name,
                    "path": destination.as_posix(),
                    "bytes": len(raw),
                    "cnf_sha256": sha256_bytes(raw),
                    "compressed": False,
                    "transformation": "tar.gz extraction only, formula unmodified",
                    "declared_result": item.expected,
                    "status": "OK",
                })
    return rows


def family_key(name: str) -> str:
    """Group near-duplicate competition instances by their scaling family.

    Competition sets contain long families that differ only by a trailing scale
    parameter, and they sort adjacently. Taking them all would let one family
    dominate the corpus, so the key strips trailing digits and separators. This
    only governs which instances are selected; it is never used to classify an
    instance or to route a solver.
    """
    stem = name[:-4] if name.endswith(".cnf") else name
    stem = stem.rstrip("0123456789")
    return stem.rstrip("-_.") or name


def acquire_sc2024(root: Path, limit: int, max_bytes: int,
                   budget_bytes: int = 40_000_000_000,
                   max_cnf_bytes: int = 100_000_000,
                   max_per_family: int = 3) -> list[dict[str, Any]]:
    """Unpack a diverse, disk-bounded slice of the SAT Competition 2024 main track.

    The archive holds xz-compressed CNFs. Selection is deterministic: entries are
    considered in name order and skipped if they are too large compressed, too
    large decompressed, would exceed the disk budget, or would be the fourth
    member of a family already represented. Every skip is recorded in the
    manifest with its reason, so the subset is reproducible and auditable.
    Formulas themselves are never modified.
    """
    archive = root / "_archives" / "sat-competition-2024-main-benchmarks.zip"
    record = download(ZENODO_SC2024, archive, ZENODO_SC2024_MD5, parallel=True)
    target_dir = root / "sc2024"
    target_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    # A 40 MB xz entry can expand to several hundred MB, so the corpus is also
    # capped on decompressed bytes rather than on entry count alone.
    used = 0
    per_family: dict[str, int] = {}
    with zipfile.ZipFile(archive) as bundle:
        entries = sorted((i for i in bundle.infolist()
                          if not i.is_dir() and looks_like_cnf(i.filename)),
                         key=lambda i: i.filename)
        for info in entries:
            plain_name = Path(info.filename).name
            if plain_name.endswith(".xz"):
                plain_name = plain_name[:-3]
            skip = None
            if len([r for r in rows if r.get("status") == "OK"]) >= limit:
                break
            if used >= budget_bytes:
                skip = "SKIPPED_DISK_BUDGET"
            elif info.file_size > max_bytes:
                skip = "SKIPPED_TOO_LARGE_COMPRESSED"
            elif per_family.get(family_key(plain_name), 0) >= max_per_family:
                skip = "SKIPPED_FAMILY_CAP"
            if skip:
                rows.append({"source": "SATComp2024", "set": "main2024",
                             "instance": plain_name, "status": skip,
                             "family_key": family_key(plain_name),
                             "bytes_compressed": info.file_size,
                             "domain": "competition_main"})
                continue
            payload = bundle.read(info)
            compressed_sha = sha256_bytes(payload)
            if info.filename.endswith(".xz"):
                plain = lzma.decompress(payload)
                transformation = "xz decompression only, formula unmodified"
            else:
                plain = payload
                transformation = "none"
            if len(plain) > max_cnf_bytes:
                rows.append({"source": "SATComp2024", "set": "main2024",
                             "instance": plain_name,
                             "status": "SKIPPED_TOO_LARGE_DECOMPRESSED",
                             "family_key": family_key(plain_name),
                             "bytes_compressed": info.file_size,
                             "bytes": len(plain), "domain": "competition_main"})
                continue
            name = plain_name
            destination = target_dir / name
            write_instance(plain, destination)
            used += len(plain)
            per_family[family_key(name)] = per_family.get(family_key(name), 0) + 1
            rows.append({
                "source": "SATComp2024",
                "source_url": ZENODO_SC2024,
                "source_doi": ZENODO_SC2024_DOI,
                "source_license": "CC-BY-4.0",
                "source_archive_md5": record["md5"],
                "set": "main2024",
                "domain": "competition_main",
                "provenance_note": "SAT Competition 2024 main track, unmodified",
                "instance": name,
                "family_key": family_key(name),
                "path": destination.as_posix(),
                "bytes": len(plain),
                "cnf_sha256": sha256_bytes(plain),
                "compressed_sha256": compressed_sha,
                "compressed": True,
                "transformation": transformation,
                "declared_result": "",
                "status": "OK",
            })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("benchmarks/phase5"))
    parser.add_argument("--manifest", type=Path, default=Path("benchmarks/phase5/manifest.csv"))
    parser.add_argument("--skip-satlib", action="store_true")
    parser.add_argument("--skip-sc2024", action="store_true")
    parser.add_argument("--sc2024-limit", type=int, default=200)
    parser.add_argument("--sc2024-max-mb", type=int, default=40,
                        help="skip competition entries whose compressed size exceeds this")
    parser.add_argument("--sc2024-budget-gb", type=int, default=40,
                        help="stop extracting once this many decompressed GB are on disk")
    parser.add_argument("--sc2024-max-cnf-mb", type=int, default=100,
                        help="skip instances larger than this once decompressed")
    parser.add_argument("--sc2024-max-per-family", type=int, default=3,
                        help="cap near-duplicate scaling families so none dominates")
    args = parser.parse_args()
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    if not args.skip_satlib:
        rows += acquire_satlib(root, SATLIB_SETS)
        print(json.dumps({"stage": "satlib", "rows": len(rows)}))
    if not args.skip_sc2024:
        before = len(rows)
        rows += acquire_sc2024(root, args.sc2024_limit, args.sc2024_max_mb * 1_000_000,
                               args.sc2024_budget_gb * 1_000_000_000,
                               args.sc2024_max_cnf_mb * 1_000_000,
                               args.sc2024_max_per_family)
        print(json.dumps({"stage": "sc2024", "rows": len(rows) - before}))

    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    with args.manifest.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    ok = [r for r in rows if r.get("status") == "OK"]
    print(json.dumps({"manifest": args.manifest.as_posix(), "total": len(rows),
                      "usable": len(ok),
                      "sources": sorted({r["source"] for r in ok})}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
