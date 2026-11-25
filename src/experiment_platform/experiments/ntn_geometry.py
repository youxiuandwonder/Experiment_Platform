"""通过 NTN 项目的锁定环境查询星历几何并核验公共多普勒。"""

import json
import math
import subprocess
from pathlib import Path

_SNAPSHOT_SCRIPT = """
import hashlib
import json
from pathlib import Path
import sys

import ntn_channel_sim

source = Path(ntn_channel_sim.__file__).resolve()
if not source.is_relative_to(Path.cwd() / "src" / "ntn_channel_sim"):
    raise RuntimeError("NTN Pixi environment must import the requested checkout")

config_path = Path(sys.argv[1])
time_s = float(sys.argv[2])
step_s = 0.01
config_sha256 = hashlib.sha256(config_path.read_bytes()).hexdigest()
simulator = ntn_channel_sim.NTNChannelSimulator.from_yaml(config_path)
params = simulator.params
if params.ephemeris.mode != "dynamic":
    raise ValueError("The experiment requires dynamic NTN ephemeris")
ephemeris_path = Path(params.ephemeris.ephemeris_file)
ephemeris_sha256 = hashlib.sha256(ephemeris_path.read_bytes()).hexdigest()
snapshot = simulator.query_snapshot(time_s)
if not snapshot.visible:
    raise ValueError(f"Satellite is not visible at simulation time {time_s:g} s")

delay_0 = snapshot.propagation_delay_s
delay_plus = simulator.query_snapshot(time_s + step_s).propagation_delay_s
if time_s >= step_s:
    delay_minus = simulator.query_snapshot(time_s - step_s).propagation_delay_s
    delay_derivative = (delay_plus - delay_minus) / (2 * step_s)
else:
    delay_plus_2 = simulator.query_snapshot(time_s + 2 * step_s).propagation_delay_s
    delay_derivative = (
        4 * (delay_plus - delay_0) - (delay_plus_2 - delay_0)
    ) / (2 * step_s)

carrier_hz = params.carrier.carrier_frequency_hz
print(json.dumps({
    "time_s": time_s,
    "carrier_hz": carrier_hz,
    "doppler_hz": snapshot.doppler_shift_hz,
    "propagation_delay_s": delay_0,
    "distance_m": snapshot.distance_m,
    "elevation_deg": snapshot.elevation_deg,
    "visible": snapshot.visible,
    "delay_derivative_doppler_hz": -carrier_hz * delay_derivative,
    "config_sha256": config_sha256,
    "ephemeris_sha256": ephemeris_sha256,
    "ephemeris_metadata": snapshot.ephemeris_metadata,
}, allow_nan=False))
"""


def query_geometry(
    root: Path, config: Path, *, time_s: float, carrier_hz: float
) -> dict:
    """查询可见星历快照，并验证载频和带符号的公共多普勒。

    Args:
        root: 带已安装 Pixi 环境的 NTN 代码检出目录
        config: NTN YAML 路径，相对路径以 root 为基准
        time_s: 非负仿真时间 [s]
        carrier_hz: 波形配置中的载频 [Hz]，必须与 NTN 配置一致

    Returns:
        可写入 JSON 的几何摘要，包含时延、多普勒、差分核验结果及两份输入的 SHA-256。

    Raises:
        ValueError: 时间、载频、输出 JSON 或多普勒一致性校验不符合要求
        RuntimeError: NTN 的 Pixi 命令或快照查询失败
    """
    if not math.isfinite(time_s) or time_s < 0:
        raise ValueError("NTN simulation time must be finite and nonnegative")
    if not math.isfinite(carrier_hz) or carrier_hz <= 0:
        raise ValueError("Waveform carrier frequency must be finite and positive")

    root = root.expanduser().resolve()
    config = (root / config.expanduser()).resolve()
    command = [
        "pixi",
        "run",
        "--manifest-path",
        str(root / "pixi.toml"),
        "--locked",
        "--no-install",
        "--executable",
        "python",
        "-I",
        "-B",
        "-c",
        _SNAPSHOT_SCRIPT,
        str(config),
        str(time_s),
    ]
    result = subprocess.run(
        command, cwd=root, capture_output=True, text=True, check=False
    )
    if result.returncode:
        detail = result.stderr.strip().splitlines()
        reason = detail[-1] if detail else f"exit status {result.returncode}"
        raise RuntimeError(f"NTN geometry query failed: {reason}")
    geometry = json.loads(result.stdout)

    if not math.isclose(geometry["carrier_hz"], carrier_hz, rel_tol=1e-12):
        raise ValueError(
            f"Carrier mismatch: NTN={geometry['carrier_hz']} Hz, waveform={carrier_hz} Hz"
        )
    predicted_hz = geometry["delay_derivative_doppler_hz"]
    doppler_hz = geometry["doppler_hz"]
    error_hz = abs(predicted_hz - doppler_hz)
    # 二阶 10 ms 差分在参考星历的误差远低于 1 mHz。0.05 Hz 留出轨道
    # 数值差分余量，在 2 GHz 仍对应约 7.5 mm/s 的径向速度误差。
    if error_hz > 0.05:
        raise ValueError(
            f"Doppler/propagation-delay mismatch: snapshot={doppler_hz:.9f} Hz, "
            f"derivative={predicted_hz:.9f} Hz"
        )
    geometry["doppler_consistency_error_hz"] = error_hz
    return geometry
