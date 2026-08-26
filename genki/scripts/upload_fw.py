# /// script
# dependencies = [
#     "bs4",
#     "coloredlogs",
#     "lxml",
#     "requests",
#     "websockets",
# ]
# ///

import glob
import logging
import os
import sys
import time
import urllib.request

import bs4
import coloredlogs

from vendor.core import packettools as wpt
from vendor.interface import VendorHTTP, VendorSocket

log_fmt = "%(asctime)s %(name)s %(levelname)s: %(message)s"
coloredlogs.install(fmt=log_fmt, level=logging.INFO)
log = logging.getLogger(os.path.basename(__file__))

TR_URL = sys.argv[1]
FW_BRANCH = sys.argv[2]
FW_FILTER = sys.argv[3] if len(sys.argv) >= 4 else None
# DO_FORCE = (len(sys.argv) >= 4) and (sys.argv[3] == '-f')

BASE_DIR = os.path.expanduser('~/Downloads/')
BRANCH_URL = f"http://nidavellir.internal.vendor.com/builds/{FW_BRANCH}"

class Main:
    def __init__(self):
        fw_file = self.get_fw_file()
        self.upgrade_systems(fw_file)

    def get_fw_file(self):
        log.info(f"Looking up all builds of branch: {FW_BRANCH}...")
        try:
            page = urllib.request.urlopen(BRANCH_URL).read().decode('utf-8')
        except urllib.error.HTTPError:
            log.info(f"Branch doesn't exist online, looking locally...")
            files = glob.glob(f"{BASE_DIR}/*_{FW_BRANCH}_*")
            files.sort(key=os.path.getmtime)
            fw_file = files[-1]
            log.info(f"Using {fw_file}...")
            return fw_file
        soup = bs4.BeautifulSoup(page, "lxml")
        refs = soup.find_all('a', href=True)
        if FW_FILTER is None:
            log.info(f"Filtering to latest build...")
        else:
            log.info(f"Filtering to {FW_FILTER}...")
            refs = [ref for ref in refs if FW_FILTER in ref['href']]
        build = refs[-1]['href']
        hash_offset = len("20240910_204845_generate_linux_3951_")
        hash = build[hash_offset:hash_offset+8]
        fw_file = f"{BASE_DIR}/vendor_firmware_all_{FW_BRANCH}_{hash}.zip"
        if os.path.isfile(fw_file):
            log.info(f"Using existing {fw_file}")
        else:
            fw_url = f"{BRANCH_URL}/{build}vendor_firmware_all.zip"
            log.info(f"  {fw_url}")
            log.info(f"Downloading to {fw_file}...")
            urllib.request.urlretrieve(fw_url, fw_file)
        return fw_file

    def upgrade_systems(self, fw_file):
        vendor_system = VendorSocket(f"ws://{TR_URL}/ws")
        def progress_callback(progress: float):
            PROGRESS_BAR_LEN = 50
            normalized_progress = (PROGRESS_BAR_LEN * int(progress)) // 100
            full_bar = normalized_progress * "\u2588"
            empty_bar = (PROGRESS_BAR_LEN - normalized_progress) * " "
            print(f"|{full_bar}{empty_bar}| {progress:4.1f}%", end="\r", flush=True)
        for device in [wpt.DeviceID.TX, wpt.DeviceID.RX_1]:
            if device == wpt.DeviceID.RX_1:
                while not (wpt.DeviceID.RX_1 in vendor_system.get_devices()):
                    log.info("Waiting for RX to connect...")
                    time.sleep(2)
            log.info(f"Upgrading DeviceID={device}...")
            VendorHTTP(TR_URL, vendor_system).upload_and_upgrade_firmware(
                device, fw_file, progress_callback=progress_callback)
                # device, fw_file, progress_callback=progress_callback, force=DO_FORCE)

if __name__ == '__main__':
    Main()
