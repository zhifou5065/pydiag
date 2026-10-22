# Changelog

本文件记录 pydiag 的所有重要变更。
格式参照 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [1.0.2] - 2026-10-22

### Fixed

- `pydiag devices` 在没有 PCAN 硬件、且未安装 PCAN 驱动的机器上，会列出 8 个
  `pcan:usbN`、状态 `busy(0x200)` 的幻影设备。
  根因是把 `CAN_Initialize` 的返回码按"除 `0x1400`(ILLHW) 以外都算在位"处理，
  而 `0x200` 实际是 `PCAN_ERROR_NODRIVER`（驱动未安装）。
  现在只有 `0x400`(HWINUSE) / `0x800`(NETINUSE) 才标记为 busy，
  其余状态码一律视为该槽位无可用设备；驱动缺失时记一条 warning 便于排查。

### Documentation

- README 排查章节补充 PCAN 状态码含义（`0x200` 驱动未装 / `0x400` `0x800` 被占用）。

## [1.0.1] - 2026-10-22

### Added

- 新增 `src/pydiag/__main__.py`，支持 `python -m pydiag ...` 入口。
  `pip install --user` 时启动器落在用户级 Scripts 目录（默认不在 `PATH`），
  此前这种环境下用户没有任何可用的调用方式。

### Documentation

- README 安装章节补充 `PATH` 相关排查与 `python -m pydiag` 替代入口。

## [1.0.0] - 2026-10-22

首个公开发布版本（公开版本号自 1.0.0 起算）。

### Added

- **多设备自动探测**：遍历所有厂商 → 设备 → 通道，以「收到 ECU 应答」为判据定位诊断通道；
  探测结果写回配置，下次启动走缓存快速校验（约 0.6 秒）。
- **多厂商设备驱动**：ZLG（Windows DLL / Linux libusb 两套）、PEAK PCAN、Vector XL、Linux SocketCAN、虚拟回环。
  多盒同时在线按序列号 / UID 稳定区分。
- **四种协议**：DoCAN（ISO 15765-2）、DoLIN（ISO 17987-2）、DoIP（ISO 13400）、OBD-II（SAE J1979），
  统一 `--protocol` 入口。
- **四种安全访问算法形态**：固定 key、内置算法（`xor` / `const_add` / `invert` / `swap` / `mask_shift` / `aes_ecb`）、
  Python 脚本、OEM DLL（A/B/C 三种调用约定）。
- **交互模式 S3 心跳保活**：进入扩展会话后后台每 2 秒发 `3E 00`（抑制响应），手动输入慢不掉会话。
- **NRC 负响应码中文描述**：所有 `[NEG]` 输出自动附带含义与处置建议。
- **可插拔驱动的四层配置**：命令行显式参数 > profile 配置 > 自动探测 > 内置默认。
- **多 profile 持久化**：每个 ECU 独立记住设备 / 通道 / ID / 波特率 / 算法。
- **轮转日志**（5 MB × 5 份）+ `pydiag logs` 子命令 + `PYDIAG_LOG_LEVEL=DEBUG` 帧级日志。
- **内置自检** `pydiag selftest`：6 项检查全部跑在虚拟设备上，无需硬件。
- 10 个子命令：`devices` / `probe` / `smoke` / `request` / `security` / `diag-round` /
  `interactive` / `selftest` / `config` / `logs`。

### Notes

- 基础安装零第三方 Python 依赖；`aes_ecb` 算法需 `pip install "pydiag[crypto]"`。
- 随包分发 ZLG / PEAK 的厂商驱动二进制，版权与来源见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
- Vector `vxlapi64.dll` 因 EULA 限制不随包分发。
