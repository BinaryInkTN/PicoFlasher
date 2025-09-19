import os
import sys
import time
import subprocess
import psutil
import re
import threading
import logging
from typing import List, Dict, Optional, Callable, Tuple, Any
from dataclasses import dataclass
import hashlib
import signal
import fcntl
import struct
import json
from pathlib import Path
from enum import Enum, auto
import tempfile
from datetime import datetime


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('/var/log/isoflasher.log')
    ]
)
logger = logging.getLogger('ISOFlasher')


class FlashStatus(Enum):
    IDLE = auto()
    VALIDATING = auto()
    UNMOUNTING = auto()
    FLASHING = auto()
    VERIFYING = auto()
    COMPLETED = auto()
    ERROR = auto()
    CANCELLED = auto()


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
    serial: str = "Unknown"
    read_only: bool = False


class FlashError(Exception):
    """Custom exception for flash operations"""
    pass


class SafeISOFlasher:
    def __init__(self, verbose: bool = False, use_sudo: bool = True):
        self.verbose = verbose
        self.use_sudo = use_sudo
        self._progress_callback = None
        self._status_callback = None
        self._flash_process = None
        self._iso_size = 0
        self._progress_thread = None
        self._stop_progress = threading.Event()
        self._cancelled = False
        self._bytes_written = 0
        self._usb_device = None
        self._status = FlashStatus.IDLE
        self._lock = threading.RLock()
        
    def set_progress_callback(self, callback: Callable[[int, int], None]) -> None:
        """Set callback for progress updates"""
        self._progress_callback = callback
        
    def set_status_callback(self, callback: Callable[[str], None]) -> None:
        """Set callback for status updates"""
        self._status_callback = callback
        
    def _log(self, message: str, level: str = "INFO") -> None:
        """Thread-safe logging"""
        with self._lock:
            log_level = getattr(logging, level.upper(), logging.INFO)
            logger.log(log_level, message)
            
            if self._status_callback:
                self._status_callback(message)
    
    def _progress_update(self, value: int, max_value: int = 100) -> None:
        """Thread-safe progress update"""
        with self._lock:
            if self._progress_callback:
                self._progress_callback(value, max_value)
    
    def _set_status(self, status: FlashStatus) -> None:
        """Set current flash status"""
        with self._lock:
            self._status = status
    
    def get_status(self) -> FlashStatus:
        """Get current flash status"""
        with self._lock:
            return self._status
    
    def _run_command(self, cmd: List[str], check: bool = True, **kwargs) -> subprocess.CompletedProcess:
        """Run a command with proper error handling"""
        if self.use_sudo and not any(arg.startswith('sudo') for arg in cmd):
            cmd = ['sudo'] + cmd
            
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=check,
                **kwargs
            )
            return result
        except subprocess.CalledProcessError as e:
            self._log(f"Command failed: {' '.join(cmd)} - {e.stderr}", "ERROR")
            raise FlashError(f"Command execution failed: {e.stderr}")
        except FileNotFoundError as e:
            self._log(f"Command not found: {' '.join(cmd)}", "ERROR")
            raise FlashError(f"Command not found: {cmd[0]}")
    
    def list_usb_devices(self) -> List[USBDevice]:
        """Safely list all USB devices with comprehensive safety checks"""
        devices = []
        
        try:
            if sys.platform == 'linux':
                devices = self._list_usb_devices_linux()
            elif sys.platform == 'darwin':
                devices = self._list_usb_devices_darwin()
            elif sys.platform == 'win32':
                devices = self._list_usb_devices_windows()
            else:
                self._log(f"Unsupported platform: {sys.platform}", "WARNING")
                
        except Exception as e:
            self._log(f"Error listing devices: {e}", "ERROR")
        
        devices.sort(key=lambda x: x.device)
        return devices
    
    def _list_usb_devices_linux(self) -> List[USBDevice]:
        """List USB devices on Linux"""
        devices = []
        all_devices = self._get_all_block_devices()
        
        for device_path in all_devices:
            try:
                
                if self._is_system_disk(device_path):
                    continue
                    
                
                if not self._is_usb_device_linux(device_path):
                    continue
                
                
                size = self._get_device_size(device_path)
                if size == 0:
                    continue
                    
                model, vendor, serial = self._get_device_info(device_path)
                mountpoint = self._get_mountpoint(device_path)
                filesystem = "Unknown"
                used = 0
                free = size
                read_only = self._is_read_only(device_path)
                
                if mountpoint != "Not mounted":
                    try:
                        usage = psutil.disk_usage(mountpoint)
                        used = usage.used
                        free = usage.free
                        
                        for part in psutil.disk_partitions():
                            if part.device == device_path:
                                filesystem = part.fstype
                                break
                    except Exception as e:
                        self._log(f"Error getting mount info for {device_path}: {e}", "DEBUG")
                
                devices.append(USBDevice(
                    device=device_path,
                    mountpoint=mountpoint,
                    total_size=size,
                    used=used,
                    free=free,
                    filesystem=filesystem,
                    model=model,
                    vendor=vendor,
                    serial=serial,
                    read_only=read_only
                ))
                
            except (PermissionError, OSError, Exception) as e:
                self._log(f"Error processing device {device_path}: {e}", "DEBUG")
                continue
        
        return devices
    
    def _list_usb_devices_darwin(self) -> List[USBDevice]:
        """List USB devices on macOS - placeholder implementation"""
        self._log("macOS support not fully implemented", "WARNING")
        return []
    
    def _list_usb_devices_windows(self) -> List[USBDevice]:
        """List USB devices on Windows - placeholder implementation"""
        self._log("Windows support not fully implemented", "WARNING")
        return []
    
    def _get_all_block_devices(self) -> List[str]:
        """Get all block devices using lsblk"""
        devices = []
        try:
            result = self._run_command(["lsblk", "-ndo", "NAME,TYPE", "-J"])
            data = json.loads(result.stdout)
            for device in data.get('blockdevices', []):
                if device.get('type') == 'disk':
                    devices.append(f"/dev/{device['name']}")
        except (json.JSONDecodeError, FlashError):
            
            try:
                result = self._run_command(["lsblk", "-ndo", "NAME,TYPE"])
                for line in result.stdout.splitlines():
                    parts = line.strip().split()
                    if len(parts) >= 2 and parts[1] == "disk":
                        devices.append(f"/dev/{parts[0]}")
            except FlashError:
                self._log("Failed to list block devices", "ERROR")
        
        return devices
    
    def _get_device_size(self, device_path: str) -> int:
        """Get the size of a block device in bytes"""
        try:
            
            result = self._run_command(["blockdev", "--getsize64", device_path])
            return int(result.stdout.strip())
        except FlashError:
            try:
                
                with open(device_path, 'rb') as f:
                    BLKGETSIZE64 = 0x80081272
                    buf = fcntl.ioctl(f.fileno(), BLKGETSIZE64, struct.pack('L', 0))
                    return struct.unpack('L', buf)[0]
            except (OSError, IOError) as e:
                self._log(f"Error getting device size for {device_path}: {e}", "DEBUG")
                return 0
    
    def _get_device_info(self, device_path: str) -> Tuple[str, str, str]:
        """Get device model, vendor, and serial information"""
        model, vendor, serial = "Unknown", "Unknown", "Unknown"
        
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
            
            
            serial_path = os.path.join(sysfs_path, "device", "serial")
            if os.path.exists(serial_path):
                with open(serial_path, 'r') as f:
                    serial = f.read().strip()
                    
        except Exception as e:
            self._log(f"Error getting device info: {e}", "DEBUG")
            
        return model, vendor, serial
    
    def _get_mountpoint(self, device_path: str) -> str:
        """Get the mountpoint of a device if mounted"""
        try:
            for partition in psutil.disk_partitions():
                if partition.device == device_path:
                    return partition.mountpoint
            return "Not mounted"
        except Exception:
            return "Not mounted"
    
    def _is_read_only(self, device_path: str) -> bool:
        """Check if device is read-only"""
        try:
            device_name = os.path.basename(device_path.rstrip('/'))
            ro_path = f"/sys/block/{device_name}/ro"
            if os.path.exists(ro_path):
                with open(ro_path, 'r') as f:
                    return f.read().strip() == '1'
            return False
        except Exception:
            return False
    
    def _is_usb_device_linux(self, device_path: str) -> bool:
        """Check if a device is a USB device"""
        try:
            device_name = os.path.basename(device_path.rstrip('/'))
            sysfs_path = f"/sys/block/{device_name}"
            
            if not os.path.exists(sysfs_path):
                return False
            
            
            removable_path = os.path.join(sysfs_path, "removable")
            if os.path.exists(removable_path):
                with open(removable_path, 'r') as f:
                    if f.read().strip() == '1':
                        return True
            
            
            device_path_path = os.path.join(sysfs_path, "device", "path")
            if os.path.exists(device_path_path):
                with open(device_path_path, 'r') as f:
                    if 'usb' in f.read().lower():
                        return True
            
            
            subsystem_path = os.path.join(sysfs_path, "device", "subsystem")
            if os.path.exists(subsystem_path):
                target = os.readlink(subsystem_path)
                if 'usb' in target.lower():
                    return True
            
            
            if device_name.startswith('sd'):
                try:
                    
                    parent_path = os.path.join(sysfs_path, "device")
                    if os.path.exists(parent_path):
                        for root, dirs, files in os.walk(parent_path):
                            for file in files:
                                if file == "modalias" and "usb" in file:
                                    return True
                            for dir_name in dirs:
                                if "usb" in dir_name.lower():
                                    return True
                except Exception:
                    pass
                    
            return False
            
        except Exception as e:
            self._log(f"Error checking USB device: {e}", "DEBUG")
            return False
    
    def _is_system_disk(self, device_path: str) -> bool:
        """Check if device is a system disk"""
        try:
            
            root_device = os.stat('/').st_dev
            root_major = os.major(root_device)
            
            
            device_name = os.path.basename(device_path.rstrip('/'))
            with open(f"/sys/block/{device_name}/dev", 'r') as f:
                dev_major, _ = map(int, f.read().strip().split(':'))
                
            
            return dev_major == root_major
        except Exception:
            return False

    def validate_iso(self, iso_path: str) -> Dict[str, Any]:
        """Validate ISO file with comprehensive checks"""
        result = {
            "valid": False,
            "size": 0,
            "error": "",
            "name": os.path.basename(iso_path),
            "checksum": "",
            "is_hybrid": False
        }
        
        try:
            iso_path_obj = Path(iso_path)
            
            
            if not iso_path_obj.exists():
                result["error"] = f"ISO file not found: {iso_path}"
                return result
            
            if not iso_path_obj.is_file():
                result["error"] = f"Not a regular file: {iso_path}"
                return result
            
            
            if not os.access(iso_path, os.R_OK):
                result["error"] = f"No read permission for ISO file: {iso_path}"
                return result
            
            
            result["size"] = iso_path_obj.stat().st_size
            if result["size"] == 0:
                result["error"] = "ISO file is empty"
                return result
            
            
            if result["size"] > 10 * 1024 * 1024 * 1024:  
                result["error"] = "ISO file is too large (>10GB)"
                return result
            
            
            try:
                with open(iso_path, 'rb') as f:
                    header = f.read(32768)
                    if len(header) < 32768:
                        result["error"] = "ISO file is too small"
                        return result
                    
                    
                    if b'CD001' in header:
                        result["valid"] = True
                    
                    
                    elif self._is_hybrid_iso(header):
                        result["valid"] = True
                        result["is_hybrid"] = True
                    else:
                        
                        
                        result["valid"] = True
                    
                    
                    f.seek(0)
                    sha256_hash = hashlib.sha256()
                    for chunk in iter(lambda: f.read(65536), b""):
                        sha256_hash.update(chunk)
                    result["checksum"] = sha256_hash.hexdigest()
                    
            except IOError as e:
                result["error"] = f"Cannot read ISO file: {e}"
                return result
            
            result["valid"] = True
            
        except Exception as e:
            result["error"] = f"Error validating ISO: {str(e)}"
            
        return result
    
    def _is_hybrid_iso(self, header: bytes) -> bool:
        """Check if this is a hybrid ISO"""
        if len(header) >= 512:
            
            if header[510:512] == b'\x55\xAA':
                return True
            
            
            if len(header) >= 520 and header[512:520] == b'EFI PART':
                return True
        
        
        hybrid_patterns = [
            b'isolinux',
            b'syslinux',
            b'grub',
            b'boot.catalog'
        ]
        
        for pattern in hybrid_patterns:
            if pattern in header:
                return True
                
        return False
    
    def flash_iso(self, iso_path: str, usb_device: str, block_size: int = 4096, 
                 verify: bool = True, sync_after: bool = True) -> Dict[str, Any]:
        """Safely flash ISO to USB device with comprehensive error handling"""
        result = {
            "success": False, 
            "message": "", 
            "checksum_verified": False,
            "duration": 0,
            "bytes_written": 0
        }
        
        start_time = time.time()
        self._stop_progress.clear()
        self._cancelled = False
        self._bytes_written = 0
        self._usb_device = usb_device
        
        try:
            self._set_status(FlashStatus.VALIDATING)
            self._log("Validating ISO file...")
            iso_validation = self.validate_iso(iso_path)
            if not iso_validation["valid"]:
                result["message"] = f"Invalid ISO: {iso_validation['error']}"
                self._set_status(FlashStatus.ERROR)
                return result
            
            self._iso_size = iso_validation["size"]
            self._progress_update(5, 100)
            
            
            self._log("Validating target device...")
            if not self._validate_target_device(usb_device):
                result["message"] = f"Invalid target device: {usb_device}"
                self._set_status(FlashStatus.ERROR)
                return result
            
            device_size = self._get_device_size(usb_device)
            if device_size < self._iso_size:
                result["message"] = f"Target device is too small ({device_size} bytes < {self._iso_size} bytes)"
                self._set_status(FlashStatus.ERROR)
                return result
            
            
            if self._is_read_only(usb_device):
                result["message"] = f"Target device is read-only: {usb_device}"
                self._set_status(FlashStatus.ERROR)
                return result
            
            
            self._set_status(FlashStatus.UNMOUNTING)
            self._log(f"Unmounting {usb_device}...")
            if not self._safe_unmount(usb_device):
                result["message"] = f"Failed to unmount device: {usb_device}"
                self._set_status(FlashStatus.ERROR)
                return result
            
            self._progress_update(10, 100)
            
            
            self._progress_thread = threading.Thread(target=self._monitor_progress)
            self._progress_thread.daemon = True
            self._progress_thread.start()
            
            
            self._set_status(FlashStatus.FLASHING)
            self._log(f"Flashing {iso_path} to {usb_device}...")
            flash_result = self._safe_dd_write(iso_path, usb_device, block_size)
            
            self._stop_progress.set()
            if self._progress_thread and self._progress_thread.is_alive():
                self._progress_thread.join(timeout=5.0)
            
            if not flash_result["success"] or self._cancelled:
                result["message"] = flash_result.get("message", "Flash process cancelled or failed")
                self._set_status(FlashStatus.CANCELLED if self._cancelled else FlashStatus.ERROR)
                return result
            
            result["bytes_written"] = flash_result["bytes_written"]
            self._progress_update(95, 100)
            
            
            if verify:
                self._set_status(FlashStatus.VERIFYING)
                self._log("Verifying flash...")
                verify_success = self._verify_flash(iso_path, usb_device, iso_validation["checksum"])
                result["checksum_verified"] = verify_success
                if not verify_success:
                    result["message"] = "Flash verification failed"
                    self._set_status(FlashStatus.ERROR)
                    return result
            
            
            if sync_after:
                self._log("Synchronizing writes...")
                self._safe_sync()
            
            self._progress_update(100, 100)
            result["success"] = True
            result["message"] = "Flash completed successfully!"
            result["duration"] = time.time() - start_time
            self._set_status(FlashStatus.COMPLETED)
            
        except Exception as e:
            self._stop_progress.set()
            result["message"] = f"Failed to flash ISO: {str(e)}"
            result["duration"] = time.time() - start_time
            self._set_status(FlashStatus.ERROR)
            self._log(f"Exception during flash: {e}", "ERROR")
            if self.verbose:
                import traceback
                self._log(f"Exception details: {traceback.format_exc()}", "DEBUG")
            
        return result
    
    def _validate_target_device(self, device_path: str) -> bool:
        """Validate that the target device is safe to write to"""
        try:
            
            if not os.path.exists(device_path):
                return False
            
            
            if not os.path.exists(f"/sys/block/{os.path.basename(device_path)}"):
                return False
            
            
            if self._is_system_disk(device_path):
                return False
            
            
            
            device_name = os.path.basename(device_path)
            if device_name.startswith('sd'):
                
                
                if not self._is_usb_device_linux(device_path):
                    self._log(f"Warning: {device_path} may not be a USB device", "WARNING")
                    
                    if self._get_mountpoint(device_path) != "Not mounted":
                        self._log(f"Warning: {device_path} is mounted but not detected as USB", "WARNING")
                        return False
                
            return True
            
        except Exception:
            return False
    
    def _safe_unmount(self, device_path: str) -> bool:
        """Safely unmount a device and all its partitions"""
        try:
            device_name = os.path.basename(device_path.rstrip('/'))
            unmounted_any = False

            
            for partition in psutil.disk_partitions(all=True):
                if partition.device.startswith(device_path):
                    self._log(f"Unmounting {partition.device} (mounted at {partition.mountpoint})")
                    try:
                        self._run_command(["umount", partition.device], check=False)
                        unmounted_any = True
                    except FlashError:
                        
                        try:
                            self._run_command(["umount", "-f", partition.device], check=False)
                            unmounted_any = True
                        except FlashError:
                            self._log(f"Failed to unmount {partition.device}", "WARNING")

            
            time.sleep(2)

            
            still_mounted = [
                p for p in psutil.disk_partitions(all=True)
                if p.device.startswith(device_path)
            ]
            
            if still_mounted:
                for p in still_mounted:
                    self._log(f"Warning: {p.device} is still mounted at {p.mountpoint}", "WARNING")
                return False

            if unmounted_any:
                self._log(f"Successfully unmounted all partitions of {device_name}")
            else:
                self._log(f"No partitions were mounted for {device_name}")
                
            return True

        except Exception as e:
            self._log(f"Error while unmounting {device_path}: {e}", "ERROR")
            return False

    def _safe_dd_write(self, input_file: str, output_device: str, block_size: int) -> Dict[str, Any]:
        """Custom safe implementation of dd with progress tracking"""
        result = {"success": False, "message": "", "bytes_written": 0}
        
        try:
            self._log(f"Starting safe write operation (block size: {block_size})")
            
            input_size = os.path.getsize(input_file)
            bytes_written = 0
            
            
            if not os.access(output_device, os.W_OK):
                if self.use_sudo:
                    
                    try:
                        test_cmd = ["sudo", "test", "-w", output_device]
                        subprocess.run(test_cmd, check=True, capture_output=True)
                    except subprocess.CalledProcessError:
                        result["message"] = f"No write permission for device: {output_device}"
                        return result
                else:
                    result["message"] = f"No write permission for device: {output_device}"
                    return result
            
            with open(input_file, 'rb') as src:
                
                if self.use_sudo:
                    
                    dd_process = subprocess.Popen(
                        ["sudo", "dd", f"if={input_file}", f"of={output_device}", 
                         f"bs={block_size}", "status=none"],
                        stderr=subprocess.PIPE,
                        stdout=subprocess.PIPE
                    )
                    
                    
                    while dd_process.poll() is None and not self._stop_progress.is_set():
                        time.sleep(0.5)
                        
                        try:
                            current_pos = src.tell()
                            self._bytes_written = current_pos
                            progress = 10 + (current_pos / input_size) * 80
                            self._progress_update(min(progress, 90), 100)
                        except:
                            pass
                    
                    if self._stop_progress.is_set():
                        dd_process.terminate()
                        try:
                            dd_process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            dd_process.kill()
                        result["message"] = "Write operation cancelled"
                        return result
                    
                    
                    if dd_process.returncode != 0:
                        stderr = dd_process.stderr.read().decode() if dd_process.stderr else "Unknown error"
                        result["message"] = f"dd command failed: {stderr}"
                        return result
                    
                    result["bytes_written"] = input_size
                    result["success"] = True
                else:
                    
                    with open(output_device, 'wb') as dest:
                        while not self._stop_progress.is_set():
                            chunk = src.read(block_size)
                            if not chunk:
                                break
                            
                            try:
                                dest.write(chunk)
                                bytes_written += len(chunk)
                                self._bytes_written = bytes_written
                                
                                
                                if bytes_written % (10 * 1024 * 1024) == 0:  
                                    progress = 10 + (bytes_written / input_size) * 80
                                    self._progress_update(min(progress, 90), 100)
                                    
                            except IOError as e:
                                if e.errno == 28:  
                                    result["message"] = "No space left on device"
                                    return result
                                raise
                        
                        
                        if self._stop_progress.is_set():
                            result["message"] = "Write operation cancelled"
                            return result
                        
                        
                        dest.flush()
                        os.fsync(dest.fileno())
                        
                        result["bytes_written"] = bytes_written
                        result["success"] = True
                
                self._log(f"Write completed: {result['bytes_written']} bytes written")
                
        except PermissionError:
            result["message"] = "Permission denied for writing to device"
        except IOError as e:
            result["message"] = f"I/O error during write: {e}"
        except Exception as e:
            result["message"] = f"Error during write operation: {e}"
            
        return result
    
    def _monitor_progress(self):
        """Monitor progress through alternative means"""
        last_bytes = 0
        stall_count = 0
        
        while not self._stop_progress.is_set():
            try:
                current_bytes = self._bytes_written
                
                
                if current_bytes == last_bytes:
                    stall_count += 1
                    if stall_count > 20:  
                        self._log("Warning: Write operation may be stalled", "WARNING")
                        stall_count = 0
                else:
                    stall_count = 0
                
                last_bytes = current_bytes
                time.sleep(0.5)
                
            except Exception:
                time.sleep(1)
    
    def _verify_flash(self, iso_path: str, device_path: str, expected_checksum: str) -> bool:
        """Verify that the ISO was correctly flashed to the device"""
        try:
            self._log("Starting verification...")
            
            iso_size = os.path.getsize(iso_path)
            bytes_compared = 0
            block_size = 65536  
            last_reported = 0
            
            device_size = self._get_device_size(device_path)
            if device_size < iso_size:
                self._log(f"Device smaller than ISO! (Device={device_size}, ISO={iso_size})", "ERROR")
                return False
            
            
            if self.use_sudo and not os.access(device_path, os.R_OK):
                
                iso_hash = hashlib.sha256()
                device_hash = hashlib.sha256()
                
                
                with open(iso_path, "rb") as iso_file:
                    while True:
                        chunk = iso_file.read(block_size)
                        if not chunk:
                            break
                        iso_hash.update(chunk)
                
                
                dd_process = subprocess.Popen(
                    ["sudo", "dd", f"if={device_path}", "bs=4k", "count=1", f"skip={iso_size//4096}", "status=none"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                stdout, stderr = dd_process.communicate()
                
                if dd_process.returncode != 0:
                    self._log(f"Failed to read device for verification: {stderr.decode()}", "ERROR")
                    return False
                
                
                
                iso_final = iso_hash.hexdigest()
                
                
                dd_process = subprocess.Popen(
                    ["sudo", "dd", f"if={device_path}", f"bs={block_size}", f"count={iso_size//block_size}", "status=none"],
                    stdout=subprocess.PIPE
                )
                sha_process = subprocess.Popen(
                    ["sha256sum"],
                    stdin=dd_process.stdout,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                dd_process.stdout.close()
                stdout, stderr = sha_process.communicate()
                
                if sha_process.returncode != 0:
                    self._log(f"Failed to calculate device checksum: {stderr.decode()}", "ERROR")
                    return False
                
                device_final = stdout.decode().split()[0]
                
            else:
                
                with open(iso_path, "rb") as iso_file, open(device_path, "rb") as device_file:
                    iso_hash = hashlib.sha256()
                    device_hash = hashlib.sha256()
            
                    while bytes_compared < iso_size:
                        bytes_to_read = min(block_size, iso_size - bytes_compared)
                        iso_chunk = iso_file.read(bytes_to_read)
                        device_chunk = device_file.read(bytes_to_read)
                        
                        if not iso_chunk or not device_chunk:
                            break
                        
                        if iso_chunk != device_chunk:
                            for i in range(len(iso_chunk)):
                                if i >= len(device_chunk) or iso_chunk[i] != device_chunk[i]:
                                    position = bytes_compared + i
                                    self._log(
                                        f"Data mismatch at byte {position}: "
                                        f"ISO=0x{iso_chunk[i]:02x}, Device=0x{device_chunk[i]:02x}",
                                        "ERROR"
                                    )
                                    return False
                            self._log("Data mismatch detected (different chunk sizes).", "ERROR")
                            return False
                        
                        iso_hash.update(iso_chunk)
                        device_hash.update(device_chunk)
                        bytes_compared += len(iso_chunk)
                        
                        if bytes_compared - last_reported >= 10 * 1024 * 1024:
                            progress = 95 + (bytes_compared / iso_size) * 5
                            self._progress_update(min(progress, 100), 100)
                            last_reported = bytes_compared
                    
                    iso_final = iso_hash.hexdigest()
                    device_final = device_hash.hexdigest()
            
            self._log(f"ISO checksum: {iso_final}")
            self._log(f"Device checksum: {device_final}")
            
            if iso_final != device_final:
                self._log(
                    f"Verification failed: checksum mismatch "
                    f"(ISO: {iso_final[:16]}..., Device: {device_final[:16]}...)", "ERROR"
                )
                return False
            
            if expected_checksum and device_final != expected_checksum:
                self._log(
                    f"Verification failed: expected checksum mismatch "
                    f"(Expected: {expected_checksum[:16]}..., Got: {device_final[:16]}...)", "ERROR"
                )
                return False
            
            self._log("Verification passed!")
            return True
                
        except Exception as e:
            self._log(f"Verification error: {e}", "ERROR")
            import traceback
            self._log(f"Traceback: {traceback.format_exc()}", "DEBUG")
            return False
    
    def _safe_sync(self):
        """Safely sync all writes to disk"""
        try:
            for _ in range(2):  
                self._run_command(["sync"], check=False)
                time.sleep(1)
        except Exception as e:
            self._log(f"Error during sync: {e}", "WARNING")
    
    def cancel_flash(self) -> bool:
        """Cancel the current flash operation"""
        self._stop_progress.set()
        self._cancelled = True
        self._set_status(FlashStatus.CANCELLED)
        self._log("Flash operation cancelled by user")
        return True