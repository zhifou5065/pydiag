# 第三方组件说明 / Third-Party Notices

pydiag 自身以 MIT 许可发布（见 [LICENSE](LICENSE)）。为了让用户 `pip install` 之后
零配置即可使用真实硬件，本项目在 `src/pydiag/vendor/` 与 `src/pydiag/zlg_runtime/kerneldlls/`
中随包分发了部分厂商提供的预编译驱动库。

**这些文件的版权归各自厂商所有，不适用本项目的 MIT 许可。** 它们仅作为「让 pydiag 能与
对应硬件通信」的运行时依赖被原样附带，未做任何修改、逆向或再授权。若你是厂商方并认为
分发方式不妥，请通过 [SECURITY.md](SECURITY.md) 中的联系方式告知，我们会在下一个版本移除。

---

## 随包分发的组件

| 组件 | 文件 | 版权 / 提供方 | 来源 | 说明 |
|---|---|---|---|---|
| ZLG CAN 驱动（Windows） | `vendor/zlg/zlgcan.dll`<br>`zlg_runtime/kerneldlls/*.dll` | © 广州致远电子股份有限公司（ZLG） | ZLG 官方 USBCANFD 系列 SDK / 驱动安装包 | ZLG USBCAN / USBCANFD 系列适配器的官方运行库。`kerneldlls/` 下为其按设备型号拆分的子驱动（CANFDNET / CANETE / USB** 等），由 `zlgcan.dll` 按需加载 |
| ZLG CAN 驱动（Linux） | `vendor/zlg/linux/libusbcanfd.so`<br>`vendor/zlg/linux/libzuds.so` | © 广州致远电子股份有限公司（ZLG） | ZLG 官方 `usbcanfd_libusb_x64_1.0.15_260827`（[下载页](https://manual.zlg.cn/web/#/146)） | Linux 走 libusb 方案，`libzuds.so` 为官方硬件 UDS 库，pydiag 目前不调用，保留备用 |
| libusb | `vendor/zlg/linux/libusb-1.0.so` | © libusb 项目贡献者 | [libusb.info](https://libusb.info/) | **LGPL-2.1**。作为 ZLG Linux 方案的下游依赖附带副本，避免用户系统未装 libusb 时加载失败。可随时用系统包替换：`apt-get install libusb-1.0-0` |
| PEAK PCAN-Basic | `vendor/pcan/PCANBasic.dll` | © PEAK-System Technik GmbH | [peak-system.com](https://www.peak-system.com/) PCAN-Basic 发行包 | PCAN-USB 系列适配器的官方 API 库（x64） |

## 明确**不**随包分发的组件

| 组件 | 原因 |
|---|---|
| Vector `vxlapi64.dll` / `vxlapi.dll` | Vector 的 EULA 不允许第三方应用再分发 XL Driver Library，用户需自行安装官方驱动。pydiag 在运行时从系统路径加载 |
| PEAK `PCANBasic.framework`（macOS） | 由 PEAK 官方 `.pkg` 安装到 `/Library/Frameworks/`，pydiag 运行时查找 |
| ZLG macOS 版库 | 厂商未发布 macOS 版本 |

## 使用建议

- 生产环境如需严格控制第三方二进制来源，可从厂商官网获取对应版本覆盖 `vendor/` 下同名文件，
  pydiag 按文件名加载，不做版本校验。
- 商业分发前请自行确认所在司法辖区与厂商 EULA 对再分发的具体要求。
- 各厂商驱动自身的许可条款以其安装包内 `LICENSE` / `EULA` 文本为准。
