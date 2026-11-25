"""运行 waveformDesign 的 OpenNTN 链路入口并核对返回证据."""

import json
import math
import os
import subprocess
from pathlib import Path

# 上游 openntn 入口使用 128 点 FFT、16 样本 CP 和 96 个活动子载波.
_BLOCK_SAMPLES = 144
_SYMBOLS_PER_BLOCK = 96
_BACKEND_VERSIONS = {"openntn21": "2.1.0", "legacy": "0.1.0"}


def run_waveform(
    root: Path,
    *,
    carrier_hz: float,
    sample_rate_hz: float,
    doppler_hz: float,
    seed: int,
    model: str,
    blocks: int,
) -> dict:
    """将带符号的公共多普勒传入已有波形实验, 核验两种波形的链路输出.

    Args:
        root: waveformDesign 的本地检出目录, 已安装两个锁定 Pixi 环境
        carrier_hz: 载波频率, 单位 Hz
        sample_rate_hz: 发射复基带采样率, 单位 Hz
        doppler_hz: 短帧内的常值公共多普勒, 单位 Hz, 接近为正
        seed: 两种波形和信道复验共用的随机种子
        model: 已验收的 NTN-TDL 类型 A 或 B
        blocks: 包含 CP 的完整 OFDM 块数

    Returns:
        上游入口的完整 JSON 对象, 含有效参数、实现来源、输出时间支持、同种子复验及两种波形的诊断结果.
    """
    return _run_waveform(
        root,
        backend="openntn21",
        backend_args=[f"--satellite-doppler-hz={doppler_hz}"],
        backend_parameters={"backend": "openntn21", "satellite_doppler_hz": doppler_hz},
        carrier_hz=carrier_hz,
        sample_rate_hz=sample_rate_hz,
        seed=seed,
        model=model,
        blocks=blocks,
    )


def run_legacy_waveform(
    root: Path,
    *,
    openntn_root: Path,
    carrier_hz: float,
    sample_rate_hz: float,
    seed: int,
    model: str,
    blocks: int,
) -> dict:
    """使用 WSL 上的 OpenNTN 源码运行历史链路, 复用两种波形和信道复验.

    Args:
        root: waveformDesign 的本地检出目录, 已安装锁定的默认 Pixi 环境
        openntn_root: OpenNTN 0.1.0 源码目录, 已安装其默认 Pixi 环境
        carrier_hz: 载波频率, 单位 Hz
        sample_rate_hz: 发射复基带采样率, 单位 Hz
        seed: 两种波形和信道复验共用的随机种子
        model: 上游支持的 NTN-TDL 类型
        blocks: 包含 CP 的完整 OFDM 块数

    Returns:
        上游入口的完整 JSON 对象. 信道沿用 OpenNTN 0.1.0 内部的多普勒模型.
    """
    return _run_waveform(
        root,
        backend="legacy",
        backend_args=[f"--openntn-root={openntn_root.resolve()}"],
        backend_parameters={"satellite_doppler_hz": None},
        carrier_hz=carrier_hz,
        sample_rate_hz=sample_rate_hz,
        seed=seed,
        model=model,
        blocks=blocks,
    )


def _run_waveform(
    root: Path,
    *,
    backend: str,
    backend_args: list[str],
    backend_parameters: dict,
    carrier_hz: float,
    sample_rate_hz: float,
    seed: int,
    model: str,
    blocks: int,
) -> dict:
    """通过指定的 OpenNTN 后端调用上游入口, 校验后完整返回其 JSON 记录."""
    root = root.resolve()
    command = [
        "pixi",
        "run",
        "--manifest-path",
        str(root / "pixi.toml"),
        "--locked",
        "--no-install",
        "openntn",
        "--backend",
        backend,
        "--check-replay",
        f"--carrier-hz={carrier_hz}",
        f"--sample-rate-hz={sample_rate_hz}",
        f"--seed={seed}",
        f"--model={model}",
        f"--blocks={blocks}",
        *backend_args,
    ]
    environment = {
        **os.environ,
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "CUDA_VISIBLE_DEVICES": "-1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=300,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"waveformDesign failed ({exc.returncode}): {exc.stderr.strip()}"
        ) from exc

    try:
        evidence = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("waveformDesign did not return JSON evidence") from exc

    expected = {
        "carrier_hz": carrier_hz,
        "sample_rate_hz": sample_rate_hz,
        "seed": seed,
        "model": model,
        "num_samples": blocks * _BLOCK_SAMPLES,
        **backend_parameters,
    }
    _validate_waveform(
        evidence, expected, blocks * _SYMBOLS_PER_BLOCK, _BACKEND_VERSIONS[backend]
    )
    return evidence


def _validate_waveform(
    evidence: dict, expected: dict, symbols: int, openntn_version: str
) -> None:
    """核对有效配置、首径时间原点、完整输出长度及配对诊断计数."""
    if not isinstance(evidence, dict):
        raise TypeError("waveformDesign evidence must be a JSON object")
    parameters = evidence.get("parameters")
    if not isinstance(parameters, dict) or any(
        parameters.get(name) != value for name, value in expected.items()
    ):
        raise RuntimeError("waveformDesign returned unexpected effective parameters")

    source = evidence.get("source")
    if not isinstance(source, dict) or source.get("openntn") != openntn_version:
        raise RuntimeError(f"waveformDesign did not report OpenNTN {openntn_version}")
    if evidence.get("same_seed_replay") != "PASS":
        raise RuntimeError("waveformDesign did not pass same-seed channel replay")

    shape = evidence.get("tap_shape")
    lags = evidence.get("time_lags")
    if any(
        not isinstance(pair, list)
        or len(pair) != 2
        or any(not isinstance(value, int) or isinstance(value, bool) for value in pair)
        for pair in (shape, lags)
    ):
        raise RuntimeError("waveformDesign returned invalid tap shape or lag indices")
    output_samples, tap_count = shape
    first_lag, last_lag = lags
    if (
        not first_lag <= 0 <= last_lag
        or tap_count != last_lag - first_lag + 1
        or output_samples != expected["num_samples"] + tap_count - 1
    ):
        raise RuntimeError(
            "waveformDesign output does not cover the full first-path-aligned frame"
        )

    waveforms = evidence.get("waveforms")
    if not isinstance(waveforms, dict) or set(waveforms) != {
        "cp_ofdm",
        "windowed_ofdm",
    }:
        raise RuntimeError("waveformDesign did not report both expected waveforms")
    for name, result in waveforms.items():
        if not isinstance(result, dict):
            raise TypeError(f"waveformDesign returned malformed evidence for {name}")
        errors = result.get("raw_symbol_errors")
        energy = result.get("output_energy")
        if (
            result.get("output_samples") != output_samples
            or result.get("symbols") != symbols
            or not isinstance(errors, int)
            or isinstance(errors, bool)
            or not 0 <= errors <= symbols
            or type(energy) not in (int, float)
            or not math.isfinite(energy)
            or energy <= 0
        ):
            raise RuntimeError(
                f"waveformDesign returned inconsistent frame evidence for {name}"
            )
