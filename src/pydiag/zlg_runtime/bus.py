import time
from can.bus import BusABC
from can import Message
from typing import List
import logging
from . import zlgcan


class ZlgCanBus(BusABC):

    dev_type = zlgcan.ZCAN_USBCANFD_200U        # 设备类型
    opening_status = dict()                     # 打开状态表

    @staticmethod
    def set_dev_type(dev_type):
        ZlgCanBus.dev_type = dev_type

    @staticmethod
    def is_open(dev_index) -> bool:
        if dev_index in ZlgCanBus.opening_status:
            return ZlgCanBus.opening_status.get(dev_index)
        return False

    def _zlgcan_init(self, baud, channel=0, dev_index=0):
        logger = self.logger
        dev_type = ZlgCanBus.dev_type
        dev_handle = zlgcan.INVALID_DEVICE_HANDLE
        chn_index = channel
        chn_handle = zlgcan.INVALID_CHANNEL_HANDLE

        baudrate = {
            50: 12696558,
            100: 4307950,
            125: 4304830,
            250: 110526,
            500: 104286,
            800: 101946,
            1000: 101166
        }

        data_baudrate = {
            1000: 8487694,
            2000: 4260362,
            4000: 66058,
            5000: 66055
        }
        zcan = zlgcan.ZCAN()
        dev_handle = ZlgCanBus.opening_status.get(dev_index)
        if not dev_handle:
            dev_handle = zcan.OpenDevice(dev_type, dev_index, 0)
            logger.debug(f"OpenDevice {dev_type} {dev_index} = {dev_handle}")
            if dev_handle == zlgcan.INVALID_DEVICE_HANDLE:
                return -1
            ZlgCanBus.opening_status[dev_index] = dev_handle

        info = zcan.GetDeviceInf(dev_handle)
        logger.debug(f"GetDeviceInf {info}")
        if info == None:
            return -2

        chn_cfg = zlgcan.ZCAN_CHANNEL_INIT_CONFIG()
        chn_cfg.can_type = zlgcan.ZCAN_TYPE_CANFD
        chn_cfg.config.canfd.mode = 0
        chn_cfg.config.canfd.abit_timing = baudrate[baud]
        chn_cfg.config.canfd.dbit_timing = data_baudrate[1000]
        chn_handle = zcan.InitCAN(dev_handle, chn_index, chn_cfg)
        logger.debug(f"InitCAN {dev_handle} {chn_index} = {chn_handle}")
        if chn_handle == zlgcan.INVALID_CHANNEL_HANDLE:
            return -3

        ret = zcan.StartCAN(chn_handle)
        logger.debug(f"StartCAN {chn_handle} = {ret}")

        self.zcan = zcan
        self.dev = dev_handle
        self.dev_index = dev_index
        self.baud = baud
        self.channel = chn_handle
        self.init_timestamp = time.time()       # 初始化时间
        logger.debug(f"_zlgcan_init done at {self.init_timestamp}")

        return 0

    def __init__(self, channel=0, baud=500, dev_index=0, **kwargs):
        channel = int(channel)
        super(ZlgCanBus, self).__init__(channel, **kwargs)
        self.channel_info = f"zlg_[{dev_index}]_channel_[{channel}]"
        self.logger = logging.getLogger(self.channel_info)
        ret = self._zlgcan_init(baud, channel, dev_index)
        if ret != 0:
            raise Exception(f"init error code={ret}")

    def __del__(self):
        self.close()

    def send(self, msg: List[Message]):
        if type(msg) is not list:
            msg = [msg]

        buflen = len(msg)
        frameinfo = (zlgcan.ZCAN_Transmit_Data * buflen)()

        for i, c in enumerate(msg):
            frameinfo[i].frame.can_id = c.arbitration_id
            for j, v in enumerate(c.data):
                frameinfo[i].frame.data[j] = v
            frameinfo[i].frame.can_dlc = c.dlc

        rc = self.zcan.Transmit(self.channel, frameinfo, buflen)
        if rc != buflen:
            self.logger.warn("Transmit %d" % rc)
        return rc

    def _recv_internal(self, timeout):
        if timeout is None:
            timeout = -1
        else:
            timeout = int(timeout*1000)

        frameinfo, len = self.zcan.Receive(self.channel, 1, timeout)

        if len > 0:
            msg = frameinfo[0]
            frame = frameinfo[0].frame

            rx = Message(
                # timestamp=float(msg.timestamp),
                timestamp=time.time(),
                is_remote_frame=frame.rtr,
                is_extended_id=frame.eff,
                is_error_frame=frame.err,
                arbitration_id=frame.can_id,
                dlc=frame.can_dlc,
                data=frame.data)

            return rx, False
        return None, False

    def shutdown(self):
        self.close()

    def close(self):
        if self.dev is not None:
            self.zcan.CloseDevice(self.dev)
            ZlgCanBus.opening_status[self.dev_index] = False
