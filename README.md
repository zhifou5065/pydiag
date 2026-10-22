# pydiag

[![PyPI version](https://img.shields.io/pypi/v/pydiag.svg)](https://pypi.org/project/pydiag/)
[![Python versions](https://img.shields.io/pypi/pyversions/pydiag.svg)](https://pypi.org/project/pydiag/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey.svg)](#跨平台支持)

> 命令行 UDS (ISO 14229) / OBD-II (SAE J1979) 诊断工具，多厂商 CAN/LIN 设备可插拔。
> 无界面、纯 CLI、零依赖安装即可跑。

**English** — `pydiag` is a command-line UDS (ISO 14229) and OBD-II (SAE J1979) diagnostic
toolkit with pluggable multi-vendor CAN/LIN adapters (ZLG / PEAK PCAN / Vector / SocketCAN /
virtual loopback) and four transport protocols (DoCAN / DoLIN / DoIP / OBD-II).
Pure CLI, no GUI, no third-party Python dependencies in the base install.

支持 ZLG USBCANFD、PEAK PCAN-USB、Vector XL、Linux SocketCAN 与虚拟回环五类设备，
覆盖 DoCAN / DoLIN / DoIP / OBD-II 四种协议。

---

## 特性

- **多设备可插拔** —— ZLG / PEAK PCAN / Vector / SocketCAN / 虚拟回环，统一 `zlg:<序列号>` 形式的设备标识，多盒同时在线不混淆
- **自动探测** —— 裸跑 `pydiag smoke` 自动选设备、扫通道、定位能应答的诊断通道，结果写回配置，下次毫秒级直达
- **四种协议统一入口** —— `--protocol docan|dolin|doip|obd`，命令行与配置文件字段完全一致
- **四种安全算法形态** —— 固定 key / 内置算法 / Python 脚本 / OEM DLL（A/B/C 三种调用约定）
- **交互模式带 S3 心跳** —— 进扩展会话后自动发 `3E 00`，手动打字慢也不会掉会话
- **NRC 中文解释** —— 所有负响应自动带含义与处置建议
- **ZLG 运行时三平台内置** —— Windows DLL / Linux libusb 方案均随包分发，装完即用
- **零运行时依赖** —— 基础安装不需要任何第三方 Python 包（`aes_ecb` 算法可选装 `pycryptodome`）

---

## 安装

```bash
pip install pydiag
pydiag devices          # 验证可运行
pydiag selftest         # 无硬件也能跑的内置自检
```

要求 Python **3.10+**（Windows / Linux / macOS 均可）。

从源码安装（开发模式）：

```bash
git clone https://github.com/zhifou5065/pydiag.git
cd pydiag
pip install -e ".[dev]"
```

> 只想直接跑源码、不装包：`python pydiag.py selftest`（会把 `src/` 加进 `sys.path`）。

### 跨平台支持

| 平台 | 状态 | 可用驱动 |
|---|---|---|
| Windows | 完整支持 | `zlg` / `pcan` / `vector` / `virtual` |
| Linux | 完整支持 | `zlg`（libusb）/ `socketcan` / `virtual` |
| macOS | 完整支持 | `pcan`（PEAK 官方 .pkg）/ `doip` / `virtual` |

### 硬件驱动前置条件

| 设备 | 平台 | 需要安装 | 随包附带 |
|---|---|---|---|
| ZLG USBCANFD-200U | Windows | 无需安装 | `zlgcan.dll` ✅ |
| ZLG USBCANFD 系列 | Windows / Linux | 无需安装（含 libusb 副本） | `zlgcan.dll` / `libusbcanfd.so` ✅ |
| PEAK PCAN-USB | Windows | 无需安装 | `PCANBasic.dll` ✅ |
| PEAK PCAN-USB | macOS | 装 PCAN-Basic for macOS（.pkg） | — |
| Vector VN16xx / VN56xx | Windows | 装 Vector XL Driver Library（EULA 不允许随包分发） | — |
| SocketCAN 接口 | Linux | 内核自带，接口 UP 即可 | — |
| 无硬件 | 全部 | — | `virtual:0` ✅ |

---

## 快速上手

拿到一台新 ECU，标准路径是三步：

```bash
# ① 看本机有哪些设备
pydiag devices

# ② 烟囱测试 —— 自动探测设备/通道，发 10 03 → 3E 00 → 22 F1 90
pydiag smoke

# ③ 交互模式做具体诊断
pydiag interactive
pydiag> 10 03
pydiag> :unlock
pydiag> 22 F1 90
pydiag> q
```

`pydiag smoke` 裸跑（不带任何参数）时全部自动 —— 设备自动选、通道自动探测、请求/应答 ID 取配置。首次运行会看到类似：

```
(自动探测: zlg:0123456789ABCDEF 通道 1)
```

探测结果自动写入配置，之后启动不再扫描。

---

## 子命令

| 子命令 | 作用 |
|---|---|
| `pydiag devices` | 枚举本机诊断设备 |
| `pydiag probe` | 总线监听，统计收发帧 |
| `pydiag smoke` | 烟囱测试 `10 03` → `3E 00` → `22 F1 90` |
| `pydiag request` | 发送任意 UDS 请求 |
| `pydiag security` | 安全访问解锁（27 服务） |
| `pydiag diag-round` | 一轮全流程回归（会话/读数据/DTC/控制/安全访问） |
| `pydiag interactive` | 交互模式 |
| `pydiag selftest` | 内置自检（虚拟设备，无需硬件） |
| `pydiag config` | 查看/设置持久化配置 |
| `pydiag logs` | 查看诊断日志 |

### `devices` — 枚举设备

```bash
pydiag devices                # 全部真实设备
pydiag devices --virtual      # 含虚拟设备
pydiag devices --device zlg   # 只看某厂商
```

输出示例：

```
本机诊断设备:
  [zlg] USBCANFD-200U (zlg:0123456789ABCDEF)  ch=2 serial=...
  [vector] Vector XL 通道 0（应用映射） (vector:app0)  ch=1
```

### `probe` — 总线监听

```bash
pydiag probe --seconds 5      # 监听 5 秒，报 tx/rx 统计
```

### `smoke` — 烟囱测试（最常用）

```bash
pydiag smoke                                   # 全自动
pydiag smoke --device zlg:0123456789ABCDEF --ch 1
```

流程：`10 03` 扩展会话 → `3E 00` TesterPresent → `22 F1 90` 读 VIN。
三项全 `[OK]` 说明设备、通道、ID、波特率、ISO-TP 链路全部正常。

### `request` — 发送任意 UDS 请求

```bash
pydiag request 22 F1 90       # 读 VIN
pydiag request 10 03          # 进扩展会话
pydiag request 19 01 FF       # DTC 总数
pydiag request 2E 03 FF 00    # 写 DID 0x03FF = 0x00
```

- 每个字节空格分隔，全部两位十六进制；首字节是 SID。
- **每条命令是独立进程**：命令结束约 5 秒后 ECU 因 S3 超时自动回默认会话。需要连续多步操作请用 `interactive`。
- 失败自动温和重试 3 次（`--retry N` 可调）。

### `security` — 安全访问解锁（27 服务）

```bash
pydiag security                                    # 按 profile 配置的算法形态解锁
pydiag security --level 1                          # 指定 seed 级别（奇数；填偶数会自动换算）
pydiag security --key 1A2B3C4D                     # 固定 key 形态
pydiag security --algo builtin --builtin xor --builtin-param key=1A2B3C4D
pydiag security --algo py --py-file my_sec.py
pydiag security --algo dll --dll oem_sec.dll --dll-func SecAccess --dll-conv A
```

成功输出：

```
  安全算法形态: fixed_key
  [OK] 10 03 扩展会话: POS 03 00 32 00 C8
  27 01 seed = F4 6C 2D EB (4B)
  27 02 key  = 1A 2B 3C 4D (固定密钥)
  [OK] 解锁成功
  [OK] 解锁确认: seed 全零
```

### `diag-round` — 一轮全流程回归

```bash
pydiag diag-round             # 会话/读数据/DTC/控制/安全访问 五组 13 项
```

适合改完代码或换硬件后做回归。输出 `13 PASS, 0 未通过` + 通道帧统计。

### `config` — 持久化配置

```bash
pydiag config                 # 显示全部 profile
pydiag config --use default   # 切换活动 ECU profile
pydiag config --set channel=1 req_id=0x721 res_id=0x7A1 key=1A2B3C4D
pydiag config --set sec_mode=py sec_py=/opt/tools/my_sec.py
pydiag config --reset         # 活动 profile 重置为默认
```

### `logs` — 日志

```bash
pydiag logs                   # 最后 50 行
pydiag logs --tail 200        # 最后 200 行
pydiag logs --path            # 只打印日志文件路径
```

---

## 设备指定 `--device`

| 形式 | 含义 | 示例 |
|---|---|---|
| `auto` | 自动选第一台可用（默认） | `--device auto` |
| `zlg:<serial>` | ZLG 按硬件序列号（多盒稳定区分） | `--device zlg:0123456789ABCDEF` |
| `zlg:idx<N>` | ZLG 按枚举索引 | `--device zlg:idx0` |
| `pcan:<uid>` | PCAN 按 USB 唯一序列号 | `--device pcan:09022e00-...` |
| `pcan:usb<N>` / `pcan:dev<num>` | PCAN 按槽位/设备号 | `--device pcan:usb1` |
| `vector:app<N>` | Vector 应用映射通道 | `--device vector:app0` |
| `socketcan:<if>` | Linux SocketCAN 接口 | `--device socketcan:can0` |
| `virtual:0` | 虚拟回环（无硬件开发/CI） | `--device virtual:0` |

**多设备（多 ZLG + 多 PCAN + 多 Vector）**：全部同时枚举，自动探测按 厂商 → 设备 → 通道 三层遍历；
某台被占用或打开失败自动跳过，不影响其它设备。指定某台用 `--device zlg:<serial>`，
厂商内任选用 `--device zlg:auto`。

### 通用参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--req` / `--res` | `0x721` / `0x7A1` | 诊断请求/应答 CAN ID（DoCAN） |
| `--ch` | 配置值，否则自动探测 | 物理通道索引 |
| `--baud` | `500000` | 仲裁段波特率 |
| `--nad` | `0x01` | LIN 节点地址（DoLIN） |
| `--host` / `--port` | `192.168.0.1` / `13400` | DoIP 网关地址 |
| `--source-addr` / `--target-addr` | `0x0E00` / `0x0001` | DoIP 逻辑地址 SA/TA |
| `--protocol` | 配置值，否则 docan | `docan` / `dolin` / `doip` / `obd` |
| `--no-pad` | 关（默认补齐） | 关闭 DLC<8 补 `0xAA` |
| `--timeout` | `2.0` | 应答超时秒 |
| `-v` | 关 | 打印 ISO-TP / 传输层帧流 |

---

## 自动探测与配置持久化

### 解析优先级

```
命令行显式参数  >  配置文件 profile  >  自动探测
```

例：`--ch 1` 永远生效；不写 `--ch` 时用配置里的 `channel`；配置里 `channel=null` 时才触发自动探测。

### 自动探测的工作方式

1. **缓存快速校验（约 0.6 秒）**：上次探测到的 `(设备, 通道)` 再发一条 `3E 00` 验证还在线，在则直接用；
2. **缓存失效**（设备拔了 / 换了）→ 回退全扫描：遍历所有厂商的所有设备的所有通道，逐个发 UDS 探测帧，
   **收到 ECU 应答的通道即为诊断通道**（判据是"应答到达"而不是"请求被听到"，同总线多适配器不会误判）；
3. 探测结果写回配置，下次启动命中缓存。

### 配置文件

路径：`~/.pydiag/config.json`，可用环境变量 `PYDIAG_HOME` 重定向到别的目录。

```json
{
  "active": "default",
  "profiles": {
    "default": {
      "name": "default",
      "protocol": "docan",
      "device": "zlg:0123456789ABCDEF",
      "channel": 1,
      "req_id": 1825,
      "res_id": 1953,
      "baud": 500000,
      "timeout": 1.5,
      "pad_to8": true,
      "key": "1A2B3C4D",
      "sec_mode": "fixed_key",
      "note": "example ECU"
    }
  }
}
```

多 ECU 场景建多个 profile（`pydiag config --use gateway` 切换），每个 profile 独立记住设备 / 通道 / ID / 算法。

> ⚠️ 配置文件里可能含解锁密钥，不要提交到版本库。项目自带的 `.gitignore` 已排除 `.pydiag_config/`。

---

## 四种协议

`--protocol`（或 profile 的 `protocol` 字段）切换，命令行 / 配置完全统一。

### DoCAN（默认，ISO 15765-2）

经典 CAN 诊断。相关字段：`req_id`、`res_id`、`baud`、`pad_to8`。

### DoLIN（ISO 17987-2）

```bash
pydiag interactive --protocol dolin --nad 0x05
pydiag config --set protocol=dolin nad=0x05 baud=19200
```

LIN 诊断走 MRF(`0x3C`) / SRF(`0x3D`) 主从帧。虚拟设备完整支持（自测/开发/演示）；
接专用 LIN 盒后 `--device` 指向该设备即可，上层零改动。

### DoIP（ISO 13400，车载以太网）

```bash
pydiag interactive --protocol doip --host 192.168.0.5 --target-addr 0x0002
pydiag config --set protocol=doip host=192.168.0.5 target_addr=0x0002
```

TCP 直连网关 13400 端口，自动路由激活。相关字段：`host`、`port`、`source_addr`（SA 诊断仪地址）、
`target_addr`（TA ECU 地址）。

### OBD-II（SAE J1979）

```bash
pydiag smoke --protocol obd --device zlg:<序列号> --ecu-index 0
pydiag interactive --protocol obd --device pcan:<序列号>
pydiag config --set protocol=obd ecu_index=0
```

使用法规标准 ID：功能寻址 `0x7DF`（广播请求，所有 ECU 应答）、物理寻址
`0x7E0+i` / `0x7E8+i`（`--ecu-index N` 选 ECU 0..7）。服务号用 OBD 模式号：

| 模式 | 含义 | 示例 |
|---|---|---|
| `01` | 读取当前数据 | `01 00`（PID 00 支持掩码） |
| `02` | 读取冻结帧 | `02 02` |
| `03` | 读取存储 DTC | `03` |
| `07` | 读取未决 DTC | `07` |
| `09` | 车辆信息（VIN/CVN） | `09 02` 读 VIN |
| `0A` | 永久 DTC | `0A` |

OBD 模式无会话 / 安全访问概念，`10/22/27/2E` 等 UDS 服务不适用于纯 OBD ECU；
同时支持 OBD + UDS 的 ECU 用 `--protocol docan` 手动指定 ID。

---

## 安全访问：四种 seed→key 算法形态

27 服务（SecurityAccess）的 seed→key 计算支持四种形态，**命令行 > profile 配置 > 默认** 三层优先级：

| 形态 | `--algo` | 关键参数 | 适用 |
|---|---|---|---|
| ① 固定密钥 | `fixed_key`（默认） | `--key 1A2B3C4D` | key 不随 seed 变 |
| ② Python 脚本 | `py` | `--py-file my_sec.py` | 任意算法（可用 hashlib / pycryptodome） |
| ③ OEM DLL | `dll` | `--dll oem.dll --dll-func SecAccess --dll-conv A` | 供应商 Win32 算法库 |
| ④ 内置算法 | `builtin` | `--builtin xor --builtin-param key=1A2B3C4D` | 常见固定套路免写代码 |

### ② Python 脚本形态

脚本约定：模块级 `seed_key(seed, level)` 函数，返回 key 字节。

```python
# my_sec.py
def seed_key(seed: bytes, level: int) -> bytes:
    k = [0x1A, 0x2B, 0x3C, 0x4D]
    return bytes(s ^ k[i % 4] for i, s in enumerate(seed))
```

```bash
pydiag security --algo py --py-file my_sec.py
# 一次性配置后裸跑:
pydiag config --set sec_mode=py sec_py=/opt/tools/my_sec.py
pydiag security
```

### ③ OEM DLL 形态

三种调用约定（`--dll-conv`）：

| 约定 | C 原型 | 常见来源 |
|---|---|---|
| `A`（默认） | `uint32 fn(uint32 seed, uint32 level)` | ZLG / CANoe 风格，最常见 |
| `B` | `void fn(const uint8* seed, int seedLen, uint8* key, int* keyLen, int level)` | 数组指针风格 |
| `C` | `int fn(const uint8* appKey, int appLen, const uint8* seed, int seedLen, uint8* key, int keyLen, int level)` | IMA 成对数组风格 |

```bash
pydiag security --algo dll --dll oem_sec.dll --dll-func SecAccess --dll-conv A
```

注意：32 位 DLL 必须配 32 位 Python（否则加载报错）。找不到导出函数时用 `dumpbin /exports`
或 depends 工具确认导出名。

### ④ 内置算法

| 名称 | 参数 | 计算 |
|---|---|---|
| `xor` | `key=1A2B3C4D` | seed 逐字节 XOR key（循环填充） |
| `const_add` | `const=1` | seed 大端整数值 + const |
| `invert` | — | seed 逐字节取反 |
| `swap` | — | seed 字节倒序 |
| `mask_shift` | `mask=0xFFFFFFFF shift=0` | `(seed & mask) >> shift` |
| `aes_ecb` | `key=<32hex>` | AES-128-ECB（seed 补零到 16B）取前 4B，需 `pip install "pydiag[crypto]"` |

```bash
pydiag security --algo builtin --builtin xor --builtin-param key=1A2B3C4D
```

### profile 一次配置永久生效

```bash
pydiag config --set sec_mode=builtin sec_builtin=xor sec_builtin_params='{"key":"1A2B3C4D"}'
pydiag config --set sec_mode=dll sec_dll=/opt/sec/oem.dll sec_dll_func=ComputeKey sec_dll_conv=B
pydiag config --set sec_mode=py sec_py=/opt/tools/my_sec.py
pydiag config --set sec_mode=fixed_key key=1A2B3C4D     # 回到固定 key
```

交互模式 `:unlock` 自动按当前配置形态执行。

### 失败时的 NRC 含义

| NRC | 含义 | 处置 |
|---|---|---|
| `0x35` | key 错误 | 算法 / 密钥版本不对 |
| `0x36` | 超尝试次数 | **ECU 已锁定，勿再重试**（等待或断电复位） |
| `0x37` | 防爆破延时未到期 | 等待延时后再试 |

---

## 交互模式

```bash
pydiag interactive
```

### 基本用法

```
交互模式: 输入 'SID DD DD..'（如 `10 03`、`22 F1 90`），q 退出。
        扩展会话内自动 3E 00 心跳(S3 保活), 手动输入慢不掉会话。
pydiag> 10 03
  [OK] 10: POS 03 00 32 00 C8
pydiag> 27 01
  [OK] 27: POS 01 D6 F6 A1 A9
pydiag> 27 02 1A 2B 3C 4D
  [OK] 27: POS 02
pydiag> 2E 03 FF 00
  [OK] 2E: POS 03 FF
pydiag> q
```

**语法规则：每个字节空格分隔、两位十六进制**。`27 02 1A2B3C4D` ✗（一个 token 不能是多字节），
`# 注释` ✗。

### 自动心跳（S3 保活）

进入扩展会话（发 `10 03`、`:session` 等）后，后台每 2 秒自动发 `3E 00`（抑制响应），
**打字再慢会话也不会掉**。这解决了手动输入超过 ECU S3 超时（约 5 秒）导致后续请求报 `NRC 0x7F` 的问题。

### 快捷命令

| 命令 | 作用 |
|---|---|
| `:unlock` | 一步解锁：`10 03 → 27 01 → 27 02`（key 按当前算法形态自动算/取） |
| `:unlock 1A2B3C4D` | 同上但指定固定 key |
| `:session 03` | 切会话（自动管心跳启停） |
| `:log debug` | 切日志级别 |
| `q` / `quit` / `exit` | 退出 |

### 典型工作流

```bash
pydiag interactive
pydiag> :unlock              # 一键: 进扩展会话 + 解锁
pydiag> 22 03 FF             # 回读 DID
pydiag> 2E 03 FF 01          # 写 DID (1 字节)
pydiag> 22 03 FF             # 回读确认
pydiag> 10 01                # 回默认会话
pydiag> q
```

---

## NRC 负响应码对照表

所有 `[NEG]` 输出自动带中文描述。常用子集：

| NRC | 英文名 | 中文含义 | 常见原因 |
|---|---|---|---|
| `0x11` | serviceNotSupported | 服务不支持 | ECU 未实现此服务 |
| `0x12` | subFunctionNotSupported | 子功能不支持 | 子功能号不对 |
| `0x13` | incorrectMessageLengthOrInvalidFormat | 长度或格式错误 | 数据长度与 DID 定义不匹配 |
| `0x22` | conditionsNotCorrect | 执行条件不满足 | 车况不允许（行驶中写数据等） |
| `0x31` | requestOutOfRange | 请求值越界 | DID / 参数不存在 |
| `0x33` | securityAccessDenied | 安全拒绝 | 未解锁就调 `27` / `2E` 类服务 |
| `0x35` / `0x36` / `0x37` | key 错 / 已锁定 / 延时未到 | 安全访问三连 | 见上一章 |
| `0x78` | responsePending | 响应挂起 | ECU 在算，工具自动等 |
| `0x7E` | subFunctionNotSupportedInActiveSession | 本会话不支持该子功能 | 会话级别不够 |
| `0x7F` | serviceNotSupportedInActiveSession | 本会话不支持该服务 | **需先 `10 03` 进扩展会话** |

非 UDS 层错误码：`code=101` 超时（ECU 无响应：查接线 / ID / 波特率），
`code=102` 发送失败（总线错误 / 设备异常）。

---

## 日志与排障

所有运行日志自动写入轮转文件（5 MB × 5 份），出错时 CLI 会打印日志路径：

```
$PYDIAG_HOME/logs/pydiag.log      # 默认 ~/.pydiag/logs/pydiag.log
```

```bash
pydiag logs                  # 看最后 50 行
pydiag logs --tail 200       # 看最后 200 行
pydiag logs --path           # 只打印日志文件路径

# 帧级收发日志（ISO-TP 每帧 / UDS 每请求响应）:
PYDIAG_LOG_LEVEL=DEBUG pydiag smoke ...
```

日志级别：`INFO`（默认，设备开关 / 错误 / 负响应码）→ `DEBUG`（每帧收发十六进制）。

| 症状 | 日志中找 |
|---|---|
| 设备打不开 | `OpenDevice failed` / `bind failed` / `CAN_Initialize failed` |
| 请求无响应 | `UDS timeout sid=0x..`（检查接线 / ID / 波特率） |
| 总线发送失败 | `TX failed ... bus-off? device unplugged?` |
| ECU 拒绝服务 | `UDS negative sid=0x.. nrc=0x..`（对照 NRC 表） |
| DoIP 连不上 | `DoIP connect ... failed` |

---

## 常见问题

**打不开设备 / 枚举为空**

1. **进程独占**：ZLG / PCAN 都是独占打开。另一个终端或软件（CANoe 等）占着时枚举不到 —— 关掉它。
   前一个 pydiag 进程刚退出时驱动句柄释放有几秒延迟，稍等重试。
2. **USB 层断开**：OpenDevice 全失败 + 无残留进程 = USB 线松 / Hub 掉电，重新插拔（避免经 Hub）。
3. ZLG 打开后 `InitCAN` 偶发失败：工具已内置自动恢复（设备级重开）；恢复不了就重新插拔。

**自动探测失败 / 全超时**

1. 确认 `req_id` / `res_id` 与 ECU 诊断规范一致。
2. 确认通道：ZLG 诊断接的是**软件通道 1**（丝印 CAN2）。`pydiag config --set channel=1` 固定。
3. ECU 供电与波特率（500k）。

**27 02 发出后 NRC 0x7F**

会话掉了（S3 超时回默认会话）。裸 `pydiag request` 每条是独立进程，`10 03` 不会延续到下一条 ——
用 `interactive`（自带心跳）或 `pydiag security`。

**2E 写入 NRC 0x13**

数据长度和 DID 定义不匹配。`2E 03 FF`（DID `0x03FF`）需要 **1 字节**数据：`2E 03 FF 00`。
每个 DID 的数据长度以 ECU 诊断规范为准。

**Windows 控制台中文乱码**

Windows 传统控制台默认 GBK。先 `chcp 65001` 再运行，或改用 Windows Terminal。
工具内部状态标记（`[OK]` / `[NG]`）是 ASCII，不受影响。

**Linux 上 USB 权限不足**

`lsusb` 确认 VID:PID = `3068:0009`，然后装 udev 规则：

```bash
SO_DIR=$(python -c "import pydiag, os; print(os.path.join(os.path.dirname(pydiag.__file__), 'vendor', 'zlg', 'linux'))")
sudo cp "$SO_DIR/50-usbcanfd.rules" /etc/udev/rules.d/
sudo udevadm control --reload
```

---

## 项目结构

```
pydiag/
├── pyproject.toml            # 打包元数据 + pydiag 命令入口
├── MANIFEST.in               # sdist 附带文件清单
├── README.md
├── CHANGELOG.md
├── CONTRIBUTING.md
├── SECURITY.md
├── THIRD_PARTY_NOTICES.md    # 随包分发的第三方驱动版权归属
├── LICENSE
├── pydiag.py                 # 源码树直接运行入口
├── tools/build_dist.py       # 构建便捷入口（调用 python -m build）
└── src/pydiag/
    ├── cli.py                # 命令行入口（10 个子命令）
    ├── config.py             # EcuProfile 持久化（三协议字段 + 安全算法字段）
    ├── autodetect.py         # 多设备多通道自动探测（缓存校验 + 全扫描回退）
    ├── zuds.py               # UDS 核心: ISO-TP / 会话 / 27 服务 / DoLIN / DoIP 传输
    ├── zuds_security.py      # 安全访问编排 + DllAlgo(A/B/C) + 内置算法注册表
    ├── sec_algo.py           # 算法形态解析层：命令行 / profile → SeedKeyFn
    ├── platform.py           # 跨平台层（驱动文件定位、库加载）
    ├── logutil.py            # 轮转日志
    ├── selftest.py           # 6 项自检（虚拟设备，无需硬件）
    ├── py.typed              # PEP 561 类型标记
    ├── devices/
    │   ├── core.py           # DeviceManager / BaseDriver / BaseCanDevice / BaseChannel
    │   ├── zlg.py            # ZLG：多盒序列号 / cross_watch / InitCAN 自恢复
    │   ├── linux_vci.py      # ZLG Linux libusb 方案
    │   ├── pcan.py           # PEAK：UID 多盒 / pad_to8
    │   ├── vector.py         # Vector XL：通道掩码
    │   ├── socketcan.py      # Linux 内核原生 SocketCAN
    │   ├── lin.py            # LIN 扩展：MRF/SRF / 总线类型隔离
    │   └── virtual.py        # 虚拟回环（开发 / CI）
    ├── zlg_runtime/          # ZLG 运行时加载器 + kerneldlls（随包分发）
    └── vendor/               # 第三方驱动二进制（zlg / pcan / vector）
```

### 新增设备驱动

实现 `BaseDriver`（`is_available` / `list_devices` / `open`）与 `BaseCANDevice.open_channel`，
在 `devices/__init__.py` 注册即可 —— CLI、自动探测、配置层零改动。

### 新增内置算法

```python
from pydiag.zuds_security import register_builtin

@register_builtin("my_algo")
def factory(key: str = "00000000"):
    kb = bytes.fromhex(key)
    def fn(seed: bytes, level: int) -> bytes:
        return bytes((s ^ kb[i % len(kb)]) + level for i, s in enumerate(seed))
    return fn
```

---

## 开发

```bash
git clone https://github.com/zhifou5065/pydiag.git
cd pydiag
python -m venv .venv && . .venv/Scripts/activate    # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pydiag selftest                                     # 6 项自检，无需硬件
python -m build                                     # 产出 dist/*.whl 与 *.tar.gz
twine check dist/*                                  # 上传前校验能否被 PyPI 正常渲染
```

贡献流程见 [CONTRIBUTING.md](CONTRIBUTING.md)，安全问题披露见 [SECURITY.md](SECURITY.md)，
版本历史见 [CHANGELOG.md](CHANGELOG.md)。

---

## 第三方组件

`src/pydiag/vendor/` 与 `src/pydiag/zlg_runtime/kerneldlls/` 内含 ZLG、PEAK-System 等厂商的
预编译驱动库，版权归各厂商所有，仅为「开箱即用」而随包分发第三方应用使用。
来源、版本与许可说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
Vector 的 `vxlapi64.dll` 因 EULA 限制**不随包分发**，需自行安装官方驱动。

---

## License

[MIT](LICENSE) © pydiag contributors
