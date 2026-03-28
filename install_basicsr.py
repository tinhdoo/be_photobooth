import os
import subprocess
import sys

def run_command(command):
    print(f"Running: {command}")
    try:
        subprocess.check_call(command, shell=True)
    except subprocess.CalledProcessError as e:
        print(f"Error running command: {command}")
        print(e)
        sys.exit(1)

def main():
    # 1. Clone BasicSR if not exists
    if not os.path.exists("BasicSR"):
        print("Cloning BasicSR...")
        run_command("git clone https://github.com/XPixelGroup/BasicSR.git")
    
    os.chdir("BasicSR")
    
    # 2. Patch setup.py
    # The error is usually in `get_version` function in setup.py or basicsr/__init__.py usage.
    # We will try to replace the dynamic version fetching with a hardcoded version.
    
    setup_file = "setup.py"
    if os.path.exists(setup_file):
        with open(setup_file, "r", encoding="utf-8") as f:
            content = f.read()
        
        # This is a known replacement for the BasicSR setup.py issue
        # We replace the problematic get_version call with a fixed string
        if "def get_version():" in content:
            print("Patching setup.py...")
            # We'll just bypass the get_version logic and hardcode version '1.4.2'
            # Find the line `version=get_version(),` and replace it
            content = content.replace("version=get_version(),", "version='1.4.2',")
            
            with open(setup_file, "w", encoding="utf-8") as f:
                f.write(content)
                
    # 3. Install
    print("Installing BasicSR...")
    run_command(f'"{sys.executable}" -m pip install .')
    
    print("BasicSR installed successfully!")
    
    # 4. Install other dependencies
    print("Installing facexlib and gfpgan...")
    run_command(f'"{sys.executable}" -m pip install facexlib gfpgan')
    
    print("All AI dependencies installed!")

if __name__ == "__main__":
    main()
