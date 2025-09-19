from gooey_button import *
from libgooey import *
from gooey_container import *
from gooey_window import *
from gooey_label import *
from gooey_canvas  import *
from gooey_image import *
from gooey_dropdown import *
from gooey_textbox import *
from gooey_progressbar import *
from flash import *


import tkinter as tk
from tkinter import filedialog
import threading
import time

def open_file_dialog():
    root = tk.Tk()
    root.withdraw()  
    filepath = filedialog.askopenfilename(
        title="Select ISO File",
        filetypes=[("ISO files", "*.iso"), ("All files", "*.*")]
    )
    root.destroy()
    return filepath


flasher = SafeISOFlasher(verbose=False)
iso_path = None
selected_device = None
flash_in_progress = False


iso_path_label = None
progress_bar = None
status_label = None
flash_button = None
browse_button = None
device_dropdown = None
refresh_button = None
devices = []  
dropdown_options = ["No device selected"]  

def update_status(message):
    """Update the status label"""
    global status_label
    if status_label:
        GooeyLabel_SetText(status_label, message)
    print(f"Status: {message}")

def update_progress(value, max_value=100):
    """Update the progress bar"""
    global progress_bar
    if progress_bar:
        GooeyProgressBar_Update(progress_bar, int((value / max_value) * 100))

def flash_thread():
    """Run the flash operation in a separate thread"""
    global flash_in_progress, iso_path, selected_device, flash_button, browse_button, device_dropdown, refresh_button

    if iso_path and selected_device:
        flash_in_progress = True

        flasher.set_status_callback(update_status)
        flasher.set_progress_callback(update_progress)
        
        GooeyButton_SetEnabled(refresh_button, False)
        GooeyButton_SetEnabled(browse_button, False)
        GooeyButton_SetEnabled(flash_button, False)

        result = flasher.flash_iso(iso_path, selected_device)
        
        GooeyButton_SetEnabled(browse_button, True)
        GooeyButton_SetEnabled(refresh_button, True)
        GooeyButton_SetEnabled(flash_button, True)

        
        if result["success"]:
            update_status("Flash completed successfully!")
        else:
            update_status(f"Error: {result['message']}")

        flash_in_progress = False
    else:
        if not iso_path:
            update_status("Please select an ISO file first")
        elif not selected_device:
            update_status("Please select a USB device first")
            

@GooeyTextboxCallback
def textbox_placeholder_callback(text):
    pass

@GooeyButtonCallback
def flash_callback() -> None:
    """Callback for the flash button"""
    global flash_in_progress, selected_device
    
    if flash_in_progress:
        update_status("Flash already in progress")
        return
        
    if not iso_path:
        update_status("Please select an ISO file first")
        return
        
    if not selected_device:
        update_status("Please manually select a USB device from the dropdown")
        return
        
    
    thread = threading.Thread(target=flash_thread)
    thread.daemon = True
    thread.start()

@GooeyButtonCallback
def browse_iso() -> None:
    """Callback for the browse ISO button"""
    global iso_path, iso_path_label
    
    filepath = open_file_dialog()
    if filepath:

        if not filepath.lower().endswith('.iso'):
            update_status("Please select a valid ISO file")
            return
            
        filename = filepath.split("/")[-1]
        iso_path = filepath
        
        
        if iso_path_label:
            GooeyLabel_SetText(iso_path_label, f"Selected: {filename}")
            
        update_status(f"Selected ISO: {filename}")
        
        
        validation = flasher.validate_iso(iso_path)
        if validation["valid"]:
            size_gb = validation["size"] / (1024**3)
            update_status(f"ISO validated: {size_gb:.2f} GB")
        else:
            update_status(f"Invalid ISO: {validation['error']}")
            iso_path = None
            GooeyLabel_SetText(iso_path_label, "No ISO Selected")

@GooeyButtonCallback
def refresh_callback() -> None:
    """Callback for the refresh button"""
    refresh_devices()

@GooeyCanvasCallback
def placeholder_callback(x: int, y: int) -> None:
    pass

@GooeyImageCallback
def img_placeholder_callback() -> None:
    pass


@GooeyDropdownCallback
def dropdown_callback(index) -> None:
    """Callback for device dropdown selection"""
    global selected_device, devices, dropdown_options
    
    
    adjusted_index = index - 1
    
    if adjusted_index >= 0 and adjusted_index < len(devices):
        selected_device = devices[adjusted_index].device
        device_name = dropdown_options[index]  
        update_status(f"Selected device: {device_name}")
    else:
        selected_device = None
        update_status("No device selected")

def refresh_devices():
    """Refresh the list of USB devices"""
    global device_dropdown, selected_device, devices, dropdown_options
    
    update_status("Refreshing devices...")
    devices = flasher.list_usb_devices()
    
    
    dropdown_options = ["No device selected"]
    device_names = [
        f"{dev.device} ({dev.vendor} {dev.model}) - {dev.total_size / (1024**3):.1f} GB" 
        for dev in devices
    ]
    dropdown_options.extend(device_names)
    
    GooeyDropdown_Update(device_dropdown, dropdown_options, len(dropdown_options))
    
    
    selected_device = None
    
    if devices:
        update_status(f"Found {len(devices)} device(s). Please select one from the dropdown")
    else:
        update_status("No USB devices found. Please connect a USB device and click Refresh")

def main():
    global iso_path_label, progress_bar, status_label, flash_button, browse_button, device_dropdown, refresh_button, win
    
    Gooey_Init()

    win = GooeyWindow_Create("PicoFlasher", 600, 650, True)
    GooeyWindow_MakeResizable(win, False)

    
    main_container = GooeyCanvas_Create(0, 0, 600, 650, placeholder_callback)
    GooeyCanvas_DrawRectangle(main_container, 0, 0, 600, 650, 0xF5F5F5, True, 1.0, False, 0.0)
    GooeyWindow_RegisterWidget(win, main_container)
    
    
    header = GooeyCanvas_Create(10, 10, 580, 100, placeholder_callback)
    GooeyCanvas_DrawRectangle(header, 0, 0, 580, 100,  0x2196F3, True, 1.0, False, 0.0)
    GooeyCanvas_DrawRectangle(header, 0, 0, 580, 100,  0x1565C0, False, 1.0, True, 2.0)
    GooeyWindow_RegisterWidget(win, header)

    
    content = GooeyCanvas_Create(10, 120, 580, 520, placeholder_callback)
    GooeyCanvas_DrawRectangle(content, 0, 0, 580, 520,  0xFFFFFF, True, 1.0, False, 0.0)
    GooeyCanvas_DrawRectangle(content, 0, 0, 580, 520,  0xE0E0E0, False, 1.0, True, 1.0)
    GooeyWindow_RegisterWidget(win, content)

    
    title = GooeyLabel_Create("PicoFlasher", 0.7, 35, 70)
    GooeyLabel_SetColor(title, 0xFFFFFF)
    
    GooeyWindow_RegisterWidget(win, title)

    subtitle = GooeyLabel_Create("Simple. Straightforward.", 0.5, 300, 68)
    GooeyLabel_SetColor(subtitle, 0xE3F2FD)
    GooeyWindow_RegisterWidget(win, subtitle)
    
    
    device_section = GooeyCanvas_Create(20, 140, 560, 120, placeholder_callback)
    GooeyCanvas_DrawRectangle(device_section, 0, 0, 560, 120,  0xF5F5F5, True, 1.0, False, 0.0)
    GooeyCanvas_DrawRectangle(device_section, 0, 0, 560, 120,  0xE0E0E0, False, 1.0, True, 1.0)
    GooeyWindow_RegisterWidget(win, device_section)
    
    device_label = GooeyLabel_Create("Select USB Device", 0.35, 30, 180)
    GooeyLabel_SetColor(device_label, 0x424242)
    
    GooeyWindow_RegisterWidget(win, device_label)
    
    refresh_button = GooeyButton_Create("Refresh", 470, 200, 100, 35, refresh_callback)
    GooeyWindow_RegisterWidget(win, refresh_button)
    
    device_dropdown = GooeyDropdown_Create(30, 200, 430, 35, ["No device selected"], dropdown_callback)
    GooeyWindow_RegisterWidget(win, device_dropdown)
    
    
    iso_section = GooeyCanvas_Create(20, 270, 560, 180, placeholder_callback)
    GooeyCanvas_DrawRectangle(iso_section, 0, 0, 560, 180,  0xF5F5F5, True, 1.0, False, 0.0)
    GooeyCanvas_DrawRectangle(iso_section, 0, 0, 560, 180,  0xE0E0E0, False, 1.0, True, 1.0)
    
    iso_label = GooeyLabel_Create("Select ISO File", 0.35, 30, 300)
    GooeyLabel_SetColor(iso_label, 0x424242)
    
    GooeyWindow_RegisterWidget(win, iso_label)
    
    browse_button = GooeyButton_Create("Browse ISO", 30, 315, 100, 35, browse_iso)
    GooeyWindow_RegisterWidget(win, browse_button)
    
    iso_path_label = GooeyLabel_Create("No ISO Selected", 140, 318, 16)
    GooeyLabel_SetColor(iso_path_label, 0x616161)
    GooeyWindow_RegisterWidget(win, iso_path_label)
    
    
    url_label = GooeyLabel_Create("Or enter ISO URL:", 0.35, 30, 382)
    GooeyLabel_SetColor(url_label, 0x616161)
    GooeyWindow_RegisterWidget(win, url_label)
    
    
    textbox_bg = GooeyCanvas_Create(30, 380, 540, 45, placeholder_callback)
    GooeyCanvas_DrawRectangle(textbox_bg, 0, 0, 540, 45,  0xFFFFFF, True, 1.0, False, 0.0)
    GooeyCanvas_DrawRectangle(textbox_bg, 0, 0, 540, 45,  0xBDBDBD, False, 1.0, True, 1.0)
    GooeyWindow_RegisterWidget(win, textbox_bg)
    
    iso_url_textbox = GooeyTextBox_Create(30, 395, 500, 35, "Enter ISO URL (http:// or https://)", False, textbox_placeholder_callback)
    GooeyWindow_RegisterWidget(win, iso_url_textbox)
    
    
    action_section = GooeyCanvas_Create(20, 470, 560, 80, placeholder_callback)
    GooeyCanvas_DrawRectangle(action_section, 0, 0, 560, 80,  0xF5F5F5, True, 1.0, False, 0.0)
    GooeyCanvas_DrawRectangle(action_section, 0, 0, 560, 80,  0xE0E0E0, False, 1.0, True, 1.0)
    GooeyWindow_RegisterWidget(win, action_section)
    
    progress_bar = GooeyProgressBar_Create(30, 485, 400, 40, 0)
    GooeyWindow_RegisterWidget(win, progress_bar)
    
    flash_button = GooeyButton_Create("Flash", 450, 485, 100, 40, flash_callback)
    GooeyWindow_RegisterWidget(win, flash_button)
    
    
    status_bg = GooeyCanvas_Create(10, 610, 580, 30, placeholder_callback)
    GooeyCanvas_DrawRectangle(status_bg, 0, 0, 580, 30,  0xEEEEEE, True, 1.0, False, 0.0)
    GooeyCanvas_DrawRectangle(status_bg, 0, 0, 580, 30, 0xBDBDBD, False, 1.0, True, 1.0)
    GooeyWindow_RegisterWidget(win, status_bg)
    
    status_label = GooeyLabel_Create("Ready. Click Refresh to find devices", 0.3, 20, 630)
    GooeyLabel_SetColor(status_label, 0x424242)
    GooeyWindow_RegisterWidget(win, status_label)
    GooeyWindow_RegisterWidget(win, iso_section)

    refresh_devices()
    
    GooeyWindow_Run(1, win)
    GooeyWindow_Cleanup(1, win)

if __name__ == "__main__":
    main()