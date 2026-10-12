"""Fetch real training data from download locations linked by the PDF repositories."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.datasets import DATASETS, download_kuro_siwo, download_sen1floods11

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--dataset", choices=["all", "kuro_siwo", "sen1floods11"], default="all")
parser.add_argument("--root", type=Path, default=DATASETS)
parser.add_argument("--sen-per-split", type=int, default=0, help="0 downloads all 431 official train/valid/test hand-labeled pairs")
parser.add_argument("--kuro-per-shard", type=int, default=128, help="Samples from each of four train and two test shards")
args = parser.parse_args()
if args.sen_per_split < 0 or not 1 <= args.kuro_per_shard <= 1000:
    parser.error("Use a nonnegative Sen limit and 1–1000 Kuro samples per shard.")
if args.dataset in ("all", "sen1floods11"):
    manifest = download_sen1floods11(args.root, args.sen_per_split)
    print(f"Sen1Floods11 verified: {len(manifest['records'])} real chips", flush=True)
if args.dataset in ("all", "kuro_siwo"):
    manifest = download_kuro_siwo(args.root, args.kuro_per_shard)
    print(f"Kuro Siwo verified: {len(manifest['records'])} real samples", flush=True)
