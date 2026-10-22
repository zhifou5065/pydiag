# 贡献指南

感谢你愿意改进 pydiag。这个项目的目标很具体：**把 UDS 诊断这件事在命令行里做到可靠、可复现**。
所以贡献门槛不在代码量，而在「能不能被验证」。

## 开发环境

```bash
git clone https://github.com/zhifou5065/pydiag.git
cd pydiag
python -m venv .venv
. .venv/Scripts/activate          # Windows
# . .venv/bin/activate            # Linux / macOS
pip install -e ".[dev]"
pydiag selftest                   # 应输出 6/6 通过
```

要求 Python 3.10+。没有硬件也可以开发 —— 所有自检跑在 `virtual:0` 虚拟设备上。

## 改动前先跑这两条

```bash
pydiag selftest                   # 六项基本链路
python tools/build_dist.py        # 确认还能正常打包
```

自检里有任何一项 `NG`，先在干净环境下复现再改代码。

## 代码结构约定

| 目录 | 职责 | 改动影响面 |
|---|---|---|
| `zuds.py` | UDS 核心：ISO-TP、会话、27 服务、DoLIN / DoIP 传输 | 全局，改前务必跑全量自检 |
| `zuds_security.py` | 安全访问编排、DLL 调用约定、内置算法注册表 | 安全相关 |
| `sec_algo.py` | 命令行 / profile → 算法函数的解析层 | 只影响 27 服务入口 |
| `devices/` | 各厂商驱动实现 | 只影响对应厂商 |
| `autodetect.py` | 多设备多通道探测 | 影响所有裸跑命令 |
| `cli.py` | 参数定义与输出渲染 | 影响用户可见行为 |

**分层原则**：协议层不认识设备，设备层不认识 UDS。新增能力尽量落在对应层，不要跨层写死。

### 新增设备驱动

实现 `BaseDriver`（`is_available` / `list_devices` / `open`）与 `BaseCANDevice.open_channel`，
在 `devices/__init__.py` 注册。CLI、自动探测、配置层不需要任何改动 —— 如果你的改动需要改这三个地方，
说明抽象漏了，先补抽象。

### 新增内置安全算法

```python
from pydiag.zuds_security import register_builtin

@register_builtin("my_algo")
def factory(key: str = "00000000"):
    kb = bytes.fromhex(key)
    def fn(seed: bytes, level: int) -> bytes:
        return bytes((s ^ kb[i % len(kb)]) + level for i, s in enumerate(seed))
    return fn
```

并在 `zuds_security.py` 的算法表与 README 的「内置算法」表格里同步登记。

## 绝对不要提交的东西

- `.pydiag_config/` 或任何含**真实 ECU 解锁密钥、设备序列号、VIN** 的配置与日志
- 抓包文件、`*.log`、`*.jsonl`
- 你所在公司的 DBC / ODX / CDD 文件与诊断规范原文
- 任何厂商 NDA 覆盖的算法 DLL

提 issue 贴输出时同样注意：把序列号和 key 抹掉。需要展示密钥形态时用 `1A2B3C4D` 这类中性值。

## 提交与 PR

- 一个 PR 解决一个问题。重构与功能改动尽量分开。
- commit message 说明**改了什么 + 为什么**，不要求特定格式，但要能读懂。
- PR 描述里请写清：**怎么验证的**（跑了哪些命令、接的什么硬件、输出是什么）。
  只说「已测试」不写过程的 PR 会被要求补充。
- 新增用户可见行为 → 同步更新 `README.md`；新增版本 → 更新 `CHANGELOG.md` 的 `[Unreleased]`。

## 报告问题

- 功能异常 / 用法疑问 → [Issues](https://github.com/zhifou5065/pydiag/issues)
- 安全漏洞 → 不要开 issue，按 [SECURITY.md](SECURITY.md) 私下报告
