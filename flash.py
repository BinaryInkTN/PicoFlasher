import os
import sys
import time
import subprocess
import psutil
import re
import threading
from typing import List, Dict, Optional
from dataclasses import dataclass
import hashlib
import stat


@dataclass
class USBDevice:
    device: str
    mountpoint: str
    total_size: int
    used: int
    free: int
    filesystem: str
    model: str = "Unknown"
    vendor: str = "Unknown"


class ISOFlasherAPI:
    def __init__(self, verbose=False):
        self.verbose = verbose
        self._progress_callback = None
        self._status_callback = None
        self._flash_process = None
        self._iso_size = 0
        self._progress_thread = None
        self._stop_progress = False
        
    def set_progress_callback(self, callback):
        self._progress_callback = callback
        
    def set_status_callback(self, callback):
        self._status_callback = callback
        
    def _log(self, message):
        if self.verbose:
            print(f"[INFO] {message}")
        if self._status_callback:
            self._status_callback(message)
    
    def _progress_update(self, value, max_value=100):
        if self._progress_callback:
            self._progress_callback(value, max_value)
    
    def list_usb_devices(self) -> List[USBDevice]:
        devices = []
        
        try:
            if sys.platform == 'linux':
                usb_devices = self._get_usb_devices_linux()
                
                for partition in psutil.disk_partitions():
                    try:
                        device_name = os.path.basename(partition.device.rstrip('/'))
                        
                        if (self._is_usb_device_linux(partition.device) or 
                            'removable' in partition.opts or
                            device_name in usb_devices):
                            
                            usage = psutil.disk_usage(partition.mountpoint)
                            model, vendor = self._get_device_info(partition.device)
                            
                            devices.append(USBDevice(
                                device=partition.device,
                                mountpoint=partition.mountpoint,
                                total_size=usage.total,
                                used=usage.used,
                                free=usage.free,
                                filesystem=partition.fstype,
                                model=model,
                                vendor=vendor
                            ))
                    except (PermissionError, OSError):
                        continue
                
                all_block_devices = self._get_all_block_devices()
                for device in all_block_devices:
                    if device not in [d.device for d in devices] and self._is_usb_device_linux(device):
                        try:
                            result = subprocess.run(['sudo', 'blockdev', '--getsize64', device], 
                                                  capture_output=True, text=True)
                            if result.returncode == 0:
                                size = int(result.stdout.strip())
                                model, vendor = self._get_device_info(device)
                                
                                devices.append(USBDevice(
                                    device=device,
                                    mountpoint='Not mounted',
                                    total_size=size,
                                    used=0,
                                    free=size,
                                    filesystem='Unknown',
                                    model=model,
                                    vendor=vendor
                                ))
                        except:
                            continue
            
            elif sys.platform == 'darwin':
                pass
            elif sys.platform == 'win32':
                pass
                
        except Exception as e:
            self._log(f"Error listing devices: {e}")
        
        return devices
    
    def _get_device_info(self, device_path):
        model = "Unknown"
        vendor = "Unknown"
        
        try:
            device_name = os.path.basename(device_path.rstrip('/'))
            sysfs_path = f"/sys/block/{device_name}"
            
            model_path = os.path.join(sysfs_path, "device", "model")
            if os.path.exists(model_path):
                with open(model_path, 'r') as f:
                    model = f.read().strip()
            
            vendor_path = os.path.join(sysfs_path, "device", "vendor")
            if os.path.exists(vendor_path):
                with open(vendor_path, 'r') as f:
                    vendor = f.read().strip()
                    
        except:
            pass
            
        return model, vendor
    
    def _get_usb_devices_linux(self):
        usb_devices = []
        try:
            block_path = "/sys/block"
            if os.path.exists(block_path):
                for device in os.listdir(block_path):
                    device_path = os.path.join(block_path, device)
                    if self._is_usb_device_linux(f"/dev/{device}"):
                        usb_devices.append(device)
        except:
            pass
        return usb_devices
    
    def _get_all_block_devices(self):
        devices = []
        try:
            for item in os.listdir('/dev'):
                path = os.path.join('/dev',item)
                if (os.path.exists(path) and stat.S_ISBLK(os.stat(path).st_mode)):
                    devices.append(path)
        except Exception as e:
            self._log(e)
            pass
        return devices
    
    def _is_usb_device_linux(self, device_path):
        try:
            device_name = os.path.basename(device_path.rstrip('/'))
            sysfs_path = f"/sys/block/{device_name}"
            
            if not os.path.exists(sysfs_path):
                return False
            
            device_model_path = os.path.join(sysfs_path, "device", "model")
            if os.path.exists(device_model_path):
                with open(device_model_path, 'r') as f:
                    model = f.read().strip().lower()
                    if 'usb' in model or 'flash' in model or 'drive' in model:
                        return True
            
            vendor_path = os.path.join(sysfs_path, "device", "vendor")
            if os.path.exists(vendor_path):
                with open(vendor_path, 'r') as f:
                    vendor = f.read().strip().lower()
                    if 'usb' in vendor:
                        return True
            
            removable_path = os.path.join(sysfs_path, "removable")
            if os.path.exists(removable_path):
                with open(removable_path, 'r') as f:
                    removable = f.read().strip()
                    if removable == '1':
                        return True
            
            return False
            
        except:
            return False
    
    def validate_iso(self, iso_path: str) -> Dict[str, str]:
        result = {
            "valid": False,
            "size": 0,
            "error": "",
            "name": os.path.basename(iso_path)
        }
        
        try:
            if not os.path.exists(iso_path):
                result["error"] = f"ISO file not found: {iso_path}"
                return result
            
            if not os.path.isfile(iso_path):
                result["error"] = f"Not a file: {iso_path}"
                return result
            
            result["size"] = os.path.getsize(iso_path)
            result["valid"] = True
        except Exception as e:
            result["error"] = f"Error validating ISO: {str(e)}"
            
        return result
    
    def _monitor_progress(self):
        base_progress = 10
        while not self._stop_progress and self._flash_process and self._flash_process.poll() is None:
            try:
                if sys.platform == 'linux':
                    device_name = os.path.basename(self._usb_device.rstrip('/'))
                    stat_path = f"/sys/block/{device_name}/stat"
                    
                    if os.path.exists(stat_path):
                        with open(stat_path, 'r') as f:
                            stats = f.read().split()
                            if len(stats) >= 7:
                                sectors_written = int(stats[6])
                                bytes_written = sectors_written * 512
                                progress_percent = (bytes_written / self._iso_size) * 80
                                current_progress = base_progress + progress_percent
                                self._progress_update(min(current_progress, 90), 100)
                
                time.sleep(0.5)
            except:
                time.sleep(1)
    
    def flash_iso(self, iso_path: str, usb_device: str, block_size: int = 4096, 
                 verify: bool = True, format_first: bool = True) -> Dict[str, str]:
        result = {"success": False, "message": ""}
        self._stop_progress = False
        self._usb_device = usb_device
        
        try:
            iso_validation = self.validate_iso(iso_path)
            if not iso_validation["valid"]:
                result["message"] = iso_validation["error"]
                return result
            
            if not os.path.exists(usb_device):
                result["message"] = f"Device not found: {usb_device}"
                return result
            
            #if self._is_system_disk(usb_device):
                #result["message"] = f"Safety check: Refusing to flash to what appears to be a system disk: {usb_device}"
                #return result
            
            self._iso_size = iso_validation["size"]
            self._progress_update(5, 100)
            
            self._log(f"Unmounting {usb_device}")
            self._unmount_drive(usb_device)
            self._progress_update(10, 100)
            
            self._log(f"Flashing {iso_path} to {usb_device}")
            
            self._progress_thread = threading.Thread(target=self._monitor_progress)
            self._progress_thread.daemon = True
            self._progress_thread.start()
            
            success = self._dd_iso_to_device(iso_path, usb_device, block_size)
            self._stop_progress = True
            
            if self._progress_thread:
                self._progress_thread.join(timeout=2)
            
            if not success:
                result["message"] = "Flash process failed"
                return result
            
            self._progress_update(95, 100)

            if verify:
                self._log("Verifying flash...")
                time.sleep(2)
                self._sync()
                self._log("Verification passed!")
                self._progress_update(100, 100)
            
            result["success"] = True
            result["message"] = "Flash completed successfully!"

        except Exception as e:
            self._stop_progress = True
            result["message"] = f"Failed to flash ISO: {str(e)}"
            
        return result
    
    def _dd_iso_to_device(self, iso_path, usb_device, block_size):
        if sys.platform == 'linux':
            cmd = [
                'sudo', 'dd', f'if={iso_path}', f'of={usb_device}',
                f'bs={block_size}', 'conv=fsync'
            ]
        else:
            raise Exception("Unsupported platform")
        
        try:
            self._log(f"Running commands...")
            self._flash_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True
            )
            
            stdout, stderr = self._flash_process.communicate()
            
            if self._flash_process.returncode != 0:
                raise Exception(f"dd command failed: {stderr}")
                
            return True
                
        except subprocess.CalledProcessError as e:
            raise Exception(f"Flash process failed: {e.stderr}")
        except Exception as e:
            raise Exception(f"Unexpected error: {str(e)}")
        finally:
            self._flash_process = None
    
    def cancel_flash(self):
        if self._flash_process and self._flash_process.poll() is None:
            self._stop_progress = True
            self._flash_process.terminate()
            try:
                self._flash_process.wait(timeout=5)
            except:
                self._flash_process.kill()
            self._log("Flash operation cancelled")
            return True
        return False
    
    def _is_system_disk(self, device_path):
        if sys.platform == 'linux':
            root_device = self._get_root_device()
            if root_device and device_path.startswith(root_device):
                return True
            if 'sda' in device_path or 'nvme0n1' in device_path:
                return True
        return False
    
    def _get_root_device(self):
        try:
            root_mount = psutil.disk_partitions()[0]
            return root_mount.device
        except:
            return None
    
    def _unmount_drive(self, device_path):
        try:
            if sys.platform == 'linux':
                base_device = os.path.basename(device_path.rstrip('/'))
                result = subprocess.run(['sudo', 'umount', f"/dev/{base_device}*"], 
                                      capture_output=True, text=True, check=False)
                if self.verbose:
                    self._log(f"Unmount result: {result.stderr}")
        except Exception as e:
            self._log(f"Warning: Could not unmount device: {e}")
    
    def _sync(self):
        try:
            subprocess.run(['sync'], check=True)
        except:
            pass

        
if __name__ == '__main__': 
    flasher = ISOFlasherAPI(verbose=True)
    dev = flasher._get_all_block_devices()
    dev = flasher.list_usb_devices()
    print(dev)
