ZLG USBCANFD Linux 运行库 (libusb 方案)

来源: ZLG 官方 usbcanfd_libusb_x64_1.0.15_260827.zip
下载页: https://manual.zlg.cn/web/#/146 (软件资料 → USBCANFD 系列 Linux)

文件:
  libusbcanfd.so   主库 (VCI_* API)
  libusb-1.0.so    libusb 依赖 (系统一般已装: sudo apt-get install libusb-1.0-0)
  libzuds.so       官方硬件 UDS 库 (pydiag 目前不使用, 保留备用)

安装方式二选一:
  1) 不装: pydiag 自动从本目录 (site-packages/pydiag/vendor/zlg/linux) 加载
  2) 装系统: sudo cp libusbcanfd.so libzuds.so /lib
             sudo ln -sf libusbcanfd.so /lib/libusbcanfd.so.1.0.15

权限 (必须, 否则 OpenDevice 失败):
  sudo cp 50-usbcanfd.rules /etc/udev/rules.d/
  sudo udevadm control --reload
  (或临时: sudo chmod 666 /dev/bus/usb/<bus>/<dev>, lsusb 查 VID:PID=3068:0009)

验证: lsusb 应看到 3068:0009 设备