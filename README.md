# Experiment Platform

复用现有通信仿真项目来运行、验证和记录实验。首个实验从 NTN 动态星历提取一个可见时刻的公共多普勒，交给现有 OpenNTN/Sionna 链路，在同一信道上运行两种 OFDM。验证范围是 SISO 接入和同种子信道复验。

```text
Experiment_Platform/
├── AGENTS.md                             # 项目约定和文档入口
├── README.md                             # 安装前提和运行命令
├── doc/
│   ├── EXPERIMENT_PLATFORM.md            # 平台边界、实验契约和验证
│   └── ARCHITECTURE.md                   # 每个模块的职责和数据接口
├── src/experiment_platform/experiments/
│   ├── siso_link.py                      # 参数、实验编排和运行记录
│   ├── ntn_geometry.py                   # 星历快照与多普勒核验
│   └── waveform_link.py                  # OpenNTN 链路调用与输出校验
└── runs/<run_id>/result.json             # 运行时生成，留在本地
```

运行入口只使用 Python 3.11 及以上版本的标准库。需要 Git、Pixi、可用的 `ntn_channel_sim` 本地检出及其默认 Pixi 环境，以及支持 `openntn21` 的 `waveformDesign` 本地检出及其 `default` 与 `openntn21` 环境。各上游的 Python 库和锁文件由各自的 Pixi 项目管理。调用方需要自行取得这些上游，核对实际 checkout 与对应锁定环境。

在本平台根目录执行：

```sh
PYTHONPATH=src python3 -m experiment_platform.experiments.siso_link \
  --ntn-root ../ntn_channel_sim \
  --waveform-root ../waveformDesign
```

默认查询 `ntn_channel_sim/config.yaml` 在第 1 秒的快照，以 2 GHz 载频、1.92 MS/s 波形采样率和固定种子运行 NTN-TDL-A。使用 `--help` 查看全部参数。相对 `--ntn-config` 路径以 NTN 检出目录为准。命令打印 `PASS` 或 `FAIL` 及运行记录路径。记录含几何输入哈希、源码和依赖身份、有效链路配置、输出时间范围及两种波形的均衡前诊断。实验假设与判据见 [平台说明](doc/EXPERIMENT_PLATFORM.md)。

如果要复用已经装在 WSL 的 OpenNTN 0.1 源码，指定它的源码目录。该路径需要有可用的默认 Pixi 环境。这个模式用 OpenNTN 自带的卫星模型，适合复现旧链路。

```sh
PYTHONPATH=src python3 -m experiment_platform.experiments.siso_link \
  --openntn-root ../project/OpenNTN \
  --waveform-root ../waveformDesign
```

模块接口、依赖方向和扩展方式见 [架构说明](doc/ARCHITECTURE.md)。
