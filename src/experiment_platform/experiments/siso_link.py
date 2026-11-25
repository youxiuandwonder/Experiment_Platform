"""运行星历驱动的 SISO 接入检查或旧版 OpenNTN 复验, 保存原始记录."""

import argparse
import hashlib
import json
import math
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .ntn_geometry import query_geometry
from .waveform_link import run_legacy_waveform, run_waveform

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def git_identity(root: Path) -> dict:
    """记录实际检出及其锁文件身份, 供复跑时核对."""

    def git(*args: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(root), *args], text=True
        ).strip()

    checkout = Path(git("rev-parse", "--show-toplevel")).resolve()
    relative_path = root.relative_to(checkout)
    metadata = {
        "commit": git("rev-parse", "HEAD"),
        "path_in_checkout": relative_path.as_posix(),
        "dirty": bool(
            git("status", "--porcelain=v1", "--untracked-files=normal", "--", ".")
        ),
    }
    if relative_path != Path("."):
        index_entry = subprocess.check_output(
            [
                "git",
                "-C",
                str(checkout),
                "ls-files",
                "--stage",
                "--",
                str(relative_path),
            ],
            text=True,
        ).split()
        if index_entry and index_entry[0] == "160000":
            metadata.update(
                parent_commit=metadata["commit"],
                indexed_gitlink=index_entry[1],
                commit=None,
                dirty=None,
            )
    lock = root / "pixi.lock"
    if lock.is_file():
        with lock.open("rb") as source:
            metadata["pixi_lock_sha256"] = hashlib.file_digest(
                source, "sha256"
            ).hexdigest()
    return metadata


def main() -> int:
    """读取实验参数, 调用两端锁定环境, 保存 JSON 并返回验收状态."""
    parser = argparse.ArgumentParser(description=__doc__)
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument("--ntn-root", type=Path, help="动态星历项目的本地检出")
    sources.add_argument(
        "--openntn-root", type=Path, help="旧版 OpenNTN 的本地源码目录"
    )
    parser.add_argument("--waveform-root", type=Path, required=True)
    parser.add_argument("--ntn-config", type=Path)
    parser.add_argument("--time-s", type=float)
    parser.add_argument("--carrier-hz", type=float, default=2e9)
    parser.add_argument("--sample-rate-hz", type=float, default=1.92e6)
    parser.add_argument("--seed", type=int, default=20261027)
    parser.add_argument("--blocks", type=int, default=3)
    parser.add_argument("--model", choices=("A", "B"), default="A")
    args = parser.parse_args()
    geometry_mode = args.ntn_root is not None
    if geometry_mode:
        args.time_s = 1.0 if args.time_s is None else args.time_s
        if not math.isfinite(args.time_s) or args.time_s < 0:
            parser.error("--time-s must be finite and nonnegative")
        args.ntn_config = args.ntn_config or Path("config.yaml")
    elif args.time_s is not None or args.ntn_config is not None:
        parser.error("--time-s and --ntn-config require --ntn-root")
    for field in ("carrier_hz", "sample_rate_hz"):
        value = getattr(args, field)
        if not math.isfinite(value) or value <= 0:
            parser.error(f"--{field.replace('_', '-')} must be finite and positive")
    if args.blocks < 1 or not 0 <= args.seed < 2**31:
        parser.error("--blocks must be positive and --seed must be in [0, 2**31)")

    waveform_root = args.waveform_root.expanduser().resolve()
    source_root = (
        (args.ntn_root if geometry_mode else args.openntn_root).expanduser().resolve()
    )
    config = vars(args).copy()
    config["waveform_root"] = str(waveform_root)
    if geometry_mode:
        ntn_config = (source_root / args.ntn_config.expanduser()).resolve()
        config.update(ntn_root=str(source_root), ntn_config=str(ntn_config))
    else:
        config["openntn_root"] = str(source_root)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = PROJECT_ROOT / "runs" / f"{stamp}_{uuid4().hex[:10]}"
    run_dir.mkdir(parents=True)
    result_path = run_dir / "result.json"
    record = {
        "experiment": "siso_ephemeris_openntn_link"
        if geometry_mode
        else "siso_legacy_openntn_link",
        "scope": "interface_validation",
        "requested_config": config,
        "propagation_assumptions": {
            "satellite_doppler": (
                "one geometry snapshot, constant over frame, applied by OpenNTN"
                if geometry_mode
                else "legacy OpenNTN satellite model"
            ),
            "absolute_propagation_delay": "metadata only"
            if geometry_mode
            else "not queried",
            "receiver": "raw diagnostic before channel estimation and equalization",
        },
    }
    try:
        record["source"] = {
            "platform": git_identity(PROJECT_ROOT),
            ("ntn" if geometry_mode else "openntn_checkout"): git_identity(source_root),
            "waveform": git_identity(waveform_root),
        }
        record["source_has_local_changes"] = any(
            item["dirty"] is True for item in record["source"].values()
        )
        record["source_revisions_known"] = all(
            item["commit"] is not None for item in record["source"].values()
        )
        waveform_parameters = {
            "carrier_hz": args.carrier_hz,
            "sample_rate_hz": args.sample_rate_hz,
            "seed": args.seed,
            "model": args.model,
            "blocks": args.blocks,
        }
        if geometry_mode:
            record["geometry"] = query_geometry(
                source_root,
                ntn_config,
                time_s=args.time_s,
                carrier_hz=args.carrier_hz,
            )
            record["waveform"] = run_waveform(
                waveform_root,
                doppler_hz=record["geometry"]["doppler_hz"],
                **waveform_parameters,
            )
        else:
            record["waveform"] = run_legacy_waveform(
                waveform_root,
                openntn_root=source_root,
                **waveform_parameters,
            )
    except (
        KeyError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
        subprocess.SubprocessError,
    ) as exc:
        record["status"] = "FAIL"
        record["error"] = str(exc)
    else:
        record["status"] = "PASS"

    with result_path.open("x", encoding="utf-8") as output:
        json.dump(record, output, ensure_ascii=False, indent=2, allow_nan=False)
        output.write("\n")
    print(f"{record['status']}: {result_path}")
    if record["status"] == "FAIL":
        print(record["error"])
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
