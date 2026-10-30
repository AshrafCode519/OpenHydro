"""
Creates a Windows Desktop Shortcut for OpenHydro with the official icon.
"""

import os
import sys
import subprocess

def create_desktop_shortcut():
    desktop = os.path.join(os.environ["USERPROFILE"], "Desktop")
    shortcut_path = os.path.join(desktop, "OpenHydro.lnk")
    
    script_dir = os.path.abspath(os.path.dirname(__file__))
    run_py = os.path.join(script_dir, "run.py")
    icon_ico = os.path.join(script_dir, "assets", "icon.ico")
    python_exe = sys.executable

    # Use PowerShell to create the shortcut via WScript.Shell
    ps_cmd = f"""
    $WshShell = New-Object -ComObject WScript.Shell
    $Shortcut = $WshShell.CreateShortcut('{shortcut_path}')
    $Shortcut.TargetPath = '{python_exe}'
    $Shortcut.Arguments = '"{run_py}"'
    $Shortcut.WorkingDirectory = '{script_dir}'
    $Shortcut.IconLocation = '{icon_ico},0'
    $Shortcut.Description = 'OpenHydro - Dynamic Download Manager'
    $Shortcut.Save()
    """
    
    res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, text=True)
    if res.returncode == 0 and os.path.exists(shortcut_path):
        print(f"SUCCESS: Desktop shortcut created at: {shortcut_path}")
        return True
    else:
        print(f"Error: {res.stderr}")
        return False

if __name__ == "__main__":
    create_desktop_shortcut()

