"""The PDF's data sources, their roles, and reproducible training downloads."""
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timezone
import hashlib
from io import BytesIO, StringIO
import json
from pathlib import Path
import tarfile

import httpx
import numpy as np
import rasterio


ROOT = Path(__file__).resolve().parent.parent
DATASETS = ROOT / "data" / "datasets"
SOURCES = [
    {"id": "sentinel", "name": "Sentinel-1 and Sentinel-2", "role": "prediction inputs",
     "url": "https://dataspace.copernicus.eu/", "license": "Copernicus free, full and open data policy"},
    {"id": "osm", "name": "Pre-event OpenStreetMap", "role": "infrastructure and road network",
     "url": "https://api.ohsome.org/v1", "license": "ODbL"},
    {"id": "copernicus_dem", "name": "Copernicus DEM GLO-30", "role": "terrain correction and downhill trace",
     "url": "https://dataspace.copernicus.eu/", "license": "Copernicus DEM license and WorldDEM attribution"},
    {"id": "kuro_siwo", "name": "Kuro Siwo", "role": "water-model training and held-out evaluation",
     "url": "https://github.com/Orion-AI-Lab/KuroSiwo", "license": "Data CC BY 4.0 per authors' dataset card; repository code MIT",
     "citation": "Bountos et al. (2024). Kuro Siwo: 33 billion m^2 under the water. NeurIPS 37, 38105-38121."},
    {"id": "sen1floods11", "name": "Sen1Floods11", "role": "water-model training and held-out evaluation",
     "url": "https://github.com/cloudtostreet/Sen1Floods11", "license": "Original repository has no license file; retain attribution and use for the educational project",
     "citation": "Bonafilia, Tellman, Anderson and Issenberg (2020). Sen1Floods11. CVPR Workshops, 210-211."},
    {"id": "emsr927", "name": "Copernicus EMSR927", "role": "case-study evaluation only",
     "url": "https://mapping.emergency.copernicus.eu/activations/EMSR927/",
     "license": "European Union, Copernicus Emergency Management Service data"},
]
TRAINING_CITATIONS = [s["citation"] for s in SOURCES if "citation" in s]
KURO_REPO = "https://huggingface.co/datasets/orion-ai-lab/Kuro-Siwo-Webdataset"
SEN_BUCKET = "https://storage.googleapis.com/sen1floods11/v1.1"


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def download(url, path, limit=64 * 1024 * 1024):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    temporary = path.with_suffix(path.suffix + ".part")
    try:
        with httpx.Client(timeout=120, follow_redirects=True) as client, client.stream("GET", url) as response:
            response.raise_for_status()
            written = 0
            with temporary.open("wb") as stream:
                for block in response.iter_bytes():
                    written += len(block)
                    if written > limit:
                        raise ValueError(f"Download exceeds its {limit}-byte limit: {path.name}")
                    stream.write(block)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def select_diverse(rows, limit):
    """Round-robin events within a publisher split; never move chips between splits."""
    groups = {}
    for row in rows:
        groups.setdefault(row[0].split("_")[0], []).append(row)
    ordered = []
    for index in range(max(map(len, groups.values()), default=0)):
        for event in sorted(groups):
            if index < len(groups[event]):
                ordered.append(groups[event][index])
    return ordered[:limit] if limit else ordered


def download_sen1floods11(root=DATASETS, per_split=32, progress=print):
    folder = Path(root) / "training" / "sen1floods11"
    folder.mkdir(parents=True, exist_ok=True)
    records = []
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        for split in ("train", "valid", "test"):
            url = f"{SEN_BUCKET}/splits/flood_handlabeled/flood_{split}_data.csv"
            response = client.get(url); response.raise_for_status()
            (folder / f"{split}.csv").write_bytes(response.content)
            for radar, label in select_diverse(list(csv.reader(StringIO(response.text))), per_split):
                key = radar.removesuffix("_S1Hand.tif")
                records.append({"id": key, "dataset": "sen1floods11", "split": split,
                                "event": key.split("_")[0], "units": "dB", "files": {
                    "radar": f"{split}/{radar}", "label": f"{split}/{label}"}, "urls": {
                    "radar": f"{SEN_BUCKET}/data/flood_events/HandLabeled/S1Hand/{radar}",
                    "label": f"{SEN_BUCKET}/data/flood_events/HandLabeled/LabelHand/{label}"}})
    tasks = [(r["urls"][k], folder / file) for r in records for k, file in r["files"].items()]
    progress(f"Sen1Floods11: downloading {len(records)} real, hand-labeled chips")
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda task: download(*task), tasks))
    for record in records:
        record["sha256"] = {k: sha256(folder / f) for k, f in record["files"].items()}
        with rasterio.open(folder / record["files"]["radar"]) as radar, rasterio.open(folder / record["files"]["label"]) as label:
            aligned = np.allclose(tuple(radar.transform), tuple(label.transform), rtol=0, atol=1e-10)
            if radar.count != 2 or radar.shape != label.shape or radar.crs != label.crs or not aligned:
                raise ValueError("Sen1Floods11 image and label are not aligned.")
            if not set(np.unique(label.read(1))).issubset({-1, 0, 1}):
                raise ValueError("Unexpected Sen1Floods11 label schema.")
    manifest = {"source": next(s for s in SOURCES if s["id"] == "sen1floods11"), "download_location": SEN_BUCKET,
                "selection": "Publisher hand-labeled splits; event-balanced initial subset" if per_split else "All publisher hand-labeled splits",
                "created_at": datetime.now(timezone.utc).isoformat(), "records": records}
    save_json(folder / "manifest.json", manifest)
    return manifest


def read_kuro_prefix(content, sample_limit):
    """Read complete NPY samples without extracting archive paths or loading pickle."""
    samples = {}
    try:
        with tarfile.open(fileobj=BytesIO(content), mode="r|") as archive:
            for member in archive:
                if not member.isfile() or member.size > 4 * 1024 * 1024:
                    continue
                name = Path(member.name).name
                sample, _, field = name.partition(".")
                if not sample.isdecimal() or field not in {
                    "flood_vv.npy", "flood_vh.npy", "sec1_vv.npy", "sec1_vh.npy",
                    "sec2_vv.npy", "sec2_vh.npy", "dem.npy", "info.json", "mask.npy", "valid_mask.npy",
                }:
                    continue
                if sample not in samples and len(samples) >= sample_limit:
                    break
                payload = archive.extractfile(member).read()
                samples.setdefault(sample, {})[field] = payload
    except tarfile.ReadError:
        # The byte range intentionally ends before the multi-GB archive ends.
        pass
    required = {"flood_vv.npy", "flood_vh.npy", "mask.npy", "valid_mask.npy", "info.json"}
    return {key: fields for key, fields in samples.items() if required <= fields.keys()}


def download_kuro_siwo(root=DATASETS, samples_per_shard=12, progress=print):
    folder = Path(root) / "training" / "kuro_siwo"
    records = []
    for split, shards in (("train", range(4)), ("test", range(2))):
        for shard in shards:
            archive_path = f"{split}_GRD/shard-{shard:05}.tar"
            url = f"{KURO_REPO}/resolve/main/{archive_path}"
            prefix_bytes = (samples_per_shard + 1) * 2100000
            progress(f"Kuro Siwo: {split} shard {shard}, first {samples_per_shard} labeled samples")
            cache = folder / "archive-prefixes" / f"{split}-{shard}-{prefix_bytes}.tar"
            if not cache.exists():
                with httpx.Client(timeout=180, follow_redirects=True) as client:
                    response = client.get(url, params={"download": "true", "prefix_bytes": prefix_bytes},
                                          headers={"Range": f"bytes=0-{prefix_bytes - 1}"})
                    response.raise_for_status()
                    if response.status_code != 206 or not response.headers.get("content-range", "").startswith(f"bytes 0-{prefix_bytes-1}/") or len(response.content) != prefix_bytes:
                        raise ValueError("Kuro Siwo archive server did not honor the bounded byte-range download.")
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_bytes(response.content)
            samples = read_kuro_prefix(cache.read_bytes(), samples_per_shard)
            if len(samples) != samples_per_shard:
                raise ValueError("Kuro Siwo prefix did not contain the requested number of complete labeled samples.")
            for key, fields in samples.items():
                sample_id = f"{split}-{shard:05}-{key}"
                sample_folder = folder / split / sample_id
                sample_folder.mkdir(parents=True, exist_ok=True)
                for field, content in fields.items():
                    (sample_folder / field).write_bytes(content)
                info = json.loads(fields["info.json"])
                event = str(info.get("actid", info.get("activation", next(iter(info.get("datasets", {})), "unknown"))))
                files = {k.removesuffix(".npy"): f"{split}/{sample_id}/{k}" for k in fields if k.endswith(".npy")}
                record = {"id": sample_id, "dataset": "kuro_siwo", "split": split, "event": event, "units": "linear intensity",
                          "files": files, "info": info, "archive_url": url, "archive_path": archive_path,
                          "downloaded_range": [0, prefix_bytes - 1], "sha256": {k: sha256(folder / f) for k, f in files.items()}}
                records.append(record)
    manifest = {"source": next(s for s in SOURCES if s["id"] == "kuro_siwo"), "download_location": KURO_REPO,
                "selection": "Initial prefixes of four publisher train shards and two publisher test shards; not the full dataset",
                "created_at": datetime.now(timezone.utc).isoformat(), "records": records}
    save_json(folder / "manifest.json", manifest)
    return manifest


def validate_training_splits(records):
    """Reject chip-content leakage and Kuro Siwo event leakage across splits."""
    seen, events = {}, {}
    for record in records:
        dataset, split = record["dataset"], record["split"]
        radar_key, label_key = ("flood_vv", "mask") if dataset == "kuro_siwo" else ("radar", "label")
        fingerprint = (dataset, record["sha256"][radar_key], record["sha256"][label_key])
        if fingerprint in seen and seen[fingerprint] != split:
            raise ValueError("Identical labeled chip content occurs in multiple publisher splits.")
        seen[fingerprint] = split
        if dataset == "kuro_siwo":
            key = (dataset, record["event"])
            if key in events and events[key] != split:
                raise ValueError("Kuro Siwo training and evaluation share a flood event.")
            events[key] = split


def load_sample(record, folder):
    folder = Path(folder)
    if record["dataset"] == "sen1floods11":
        with rasterio.open(folder / record["files"]["radar"]) as source:
            vv = source.read(1).astype("float32")
            radar_valid = source.read_masks(1) > 0
        with rasterio.open(folder / record["files"]["label"]) as source:
            label = source.read(1)
        valid = radar_valid & np.isin(label, [0, 1]) & np.isfinite(vv)
        water = label == 1
    elif record["dataset"] == "kuro_siwo":
        def read(name):
            return np.load(folder / record["files"][name], allow_pickle=False).squeeze()
        intensity, label = read("flood_vv"), read("mask")
        observed = read("valid_mask") > 0
        # Publisher archives also contain class 3, the ignored label used by
        # their segmentation trainer. The separate SAR validity mask can still
        # be 1 there, so both masks must be checked.
        if not set(np.unique(label)).issubset({0, 1, 2, 3}):
            raise ValueError("Unexpected Kuro Siwo label schema.")
        valid = observed & np.isin(label, [0, 1, 2]) & np.isfinite(intensity) & (intensity > 0)
        vv = 10 * np.log10(np.maximum(intensity, 1e-10))
        # Unified water task: permanent water (1) OR flood water (2).
        water = np.isin(label, [1, 2])
    else:
        raise ValueError("Only the two listed training datasets may train the model.")
    if vv.shape != water.shape or vv.shape != valid.shape or vv.ndim != 2:
        raise ValueError("Training image, label and validity mask are not aligned.")
    return vv, water, valid


def load_radar_chip(record, folder):
    """Dual-polarization observations, with label validity kept separate."""
    folder = Path(folder)
    if record["dataset"] == "sen1floods11":
        with rasterio.open(folder / record["files"]["radar"]) as source:
            radar = source.read().astype("float32")
            observed = np.all(source.read_masks() > 0, axis=0) & np.all(np.isfinite(radar), axis=0)
        with rasterio.open(folder / record["files"]["label"]) as source:
            labels = source.read(1)
        label_valid, water = np.isin(labels, [0, 1]), labels == 1
    elif record["dataset"] == "kuro_siwo":
        def read(key):
            return np.load(folder / record["files"][key], allow_pickle=False).squeeze()
        linear = np.stack([read("flood_vv"), read("flood_vh")])
        observed = (read("valid_mask") > 0) & np.all(np.isfinite(linear) & (linear > 0), axis=0)
        radar = 10 * np.log10(np.maximum(linear, 1e-10))
        labels = read("mask")
        label_valid, water = np.isin(labels, [0, 1, 2]), np.isin(labels, [1, 2])
    else:
        raise ValueError("Only listed training datasets may supply labeled radar chips.")
    if radar.shape[0] != 2 or radar.shape[1:] != water.shape or observed.shape != water.shape:
        raise ValueError("Dual-polarization radar and labels are not aligned.")
    return {"vv": radar[0], "vh": radar[1], "water": water, "observed": observed,
            "label_valid": label_valid & observed}
