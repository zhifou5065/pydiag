# 各平台连接 CAN 适配器指南

pydiag 支持三大操作系统。各厂商硬件的平台可用性由官方驱动库决定:

| 驱动 | Windows | Linux | macOS | 库文件 |
|---|---|---|---|---|
| `zlg` | ✅ 内置 | ✅ 需官方 libzlgcan.so | ❌ 厂商无 macOS 库 | zlgcan.dll / libzlgcan.so |
| `pcan` | ✅ | ❌ 厂商无 Linux 库 | ✅ 需官方 PCANBasic.framework | PCANBasic.dll / .framework |
| `vector` | ✅ | ❌ 厂商仅 Windows | ❌ 厂商仅 Windows | vxlapi64.dll |
| `socketcan` | ❌ | ✅ 内置 | ❌ 无此子系统 | 内核 AF_CAN |
| `virtual` | ✅ | ✅ | ✅ | 无需硬件 |

macOS 上连接真实 CAN 总线的可行组合:

1. **PEAK PCAN-USB** — 厂商官方支持 macOS（推荐）
2. **MacCAN / Kvaser 等第三方** — 通过 SocketCAN 风格网关或虚拟串口方案（社区方案）
3. **DoIP** — 无需 CAN 硬件，TCP 直连（全平台可用）
4. **virtual:0** — 协议栈开发/CI（全平台）

---

## macOS + PEAK PCAN-USB

PEAK 官方提供 macOS 版 PCAN-Basic（PCANBasic.framework），API 与 Windows 同名
（CAN_Initialize / CAN_Read / CAN_Write / CAN_Uninitialize），pydiag 的 pcan
驱动自动适配。

### 1. 安装官方驱动

从 PEAK-System 官网 (www.peak-system.com) → Downloads → PCAN-Basic，
下载 macOS 版安装包（.pkg），安装后得到:

```
/Library/Frameworks/PCANBasic.framework
```

pydiag 自动在此路径查找（也兼容 Homebrew 的 /opt/homebrew/lib/libpcanbasic.dylib）。

### 2. USB 权限

macOS 对 USB 设备默认放开，无需 udev 规则；首次插拔若系统弹窗请求权限，允许即可。

### 3. 使用

```bash
pydiag devices                       # 枚举: pcan:<USB 序列号>
pydiag smoke --device pcan:<序列号>
pydiag interactive --device pcan:<序列号>
```

---

## macOS + ZLG CAN 盒子?

**不可行**。周立功（ZLG）官方未发布 macOS 版本的开发库（只有 Windows 的
zlgcan.dll 与 Linux 的 libzlgcan.so）。可选替代:

- 用一台 Linux 主机/虚拟机 + USB 透传（USBIPD、VirtualBox USB 直通）跑
  `libzlgcan.so`，pydiag 装在 Linux 侧
- 换用 PEAK PCAN-USB（macOS 官方支持）
- 若 ECU 支持车载以太网诊断，直接用 DoIP（`--protocol doip --host <网关IP>`），
  macOS 无需任何 CAN 硬件

---

## macOS + Vector?

**不可行**。Vector XL Driver Library 官方只提供 Windows 版本（vxlapi64.dll），
macOS/Linux 均无官方库。VN 硬件在 macOS 下没有官方接入路径。替代方案同上。

---

## 各平台驱动放置位置速查

| 平台 | 驱动 | 库文件 | pydiag 查找路径 |
|---|---|---|---|
| Windows | zlg | zlgcan.dll | 包内 zlg_runtime/（pip 装完自带） |
| Linux | zlg | libzlgcan.so | 包内 zlg_runtime/ → $ZLG_RUNTIME → /usr/local/lib |
| macOS | pcan | PCANBasic.framework | /Library/Frameworks/（官方安装包自动放） |
| Windows | pcan | PCANBasic.dll | C:\Windows\System32（官方安装包自动放） |
| Windows | vector | vxlapi64.dll | C:\Windows\System32（官方安装包自动放） |
| Linux | socketcan | 无需库 | ip link 配置 can0 即可 |

## 全平台通用: DoIP（无需 CAN 硬件）

若 ECU 支持车载以太网诊断（ISO 13400），三大系统无需任何额外硬件:

```bash
pydiag --protocol doip --host 192.168.0.1 --port 13400 smoke
pydiag --protocol doip --host 192.168.0.1 interactive
```

## 开发/CI: 虚拟回环（全平台零依赖）

```bash
pydiag selftest        # 6 项自检, 全部跑在 virtual:0
pydiag smoke --device virtual:0
```