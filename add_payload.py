import json
import subprocess
import re
import hashlib
import urllib.request
import sys
import os
import zipfile
import tempfile
import shutil
from datetime import datetime
from update_payloads import update_readme

JSON_FILE = "payloads.json"
PAYLOADS_DIR = "payloads"
BASE_URL = "https://github.com/itsPLK/ps5-payloads-mirror/releases/download/payloads-mirror"

def get_repo_info(url):
    match = re.search(r"github\.com/([^/]+)/([^/]+)", url)
    if match:
        owner = match.group(1)
        repo = match.group(2).rstrip('/')
        return owner, repo
    return None, None

def download_file(url, filename):
    if not os.path.exists(PAYLOADS_DIR):
        os.makedirs(PAYLOADS_DIR)
    
    filepath = os.path.join(PAYLOADS_DIR, filename)
    print(f"  Downloading {filename}...")
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            with open(filepath, 'wb') as f:
                f.write(response.read())
        return True
    except Exception as e:
        print(f"  Error downloading {filename}: {e}")
        return False

def calculate_checksum(filepath):
    sha256 = hashlib.sha256()
    try:
        with open(filepath, 'rb') as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                sha256.update(chunk)
        return sha256.hexdigest()
    except Exception as e:
        print(f"  Error calculating checksum: {e}")
        return None

def reorder_item(item):
    order = ["name", "filename", "url", "source", "source_direct", "extract_file", "description", "last_update", "version", "checksum"]
    new_item = {}
    for key in order:
        if key in item:
            new_item[key] = item[key]
    for key in item:
        if key not in new_item:
            new_item[key] = item[key]
    return new_item

def add_payload():
    print("Add New PS5 Payload (Auto-download)")
    print("-" * 20)
    
    url = input("GitHub Download URL: ").strip()
    if not url:
        print("Error: URL is required.")
        return
        
    owner, repo = get_repo_info(url)
    if not owner:
        print("Error: Could not parse GitHub owner/repo from URL.")
        return
        
    description = input("Description (optional): ").strip()
    
    print(f"Fetching latest release info for {owner}/{repo}...")
    try:
        cmd = ["gh", "api", f"repos/{owner}/{repo}/releases/latest"]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        release = json.loads(result.stdout)
    except Exception as e:
        print(f"Error fetching release info: {e}")
        return

    filename_match = re.search(r"/([^/]+\.(elf|bin|zip))$", url)
    original_filename = filename_match.group(1) if filename_match else None
    
    assets = release.get("assets", [])
    selected_asset = None
    if original_filename:
        for asset in assets:
            if asset["name"] == original_filename:
                selected_asset = asset
                break
                
    if not selected_asset and assets:
        for asset in assets:
            if asset["name"].endswith((".elf", ".bin")):
                selected_asset = asset
                break
        if not selected_asset:
            for asset in assets:
                if asset["name"].endswith(".zip"):
                    selected_asset = asset
                    break
                
    if not selected_asset:
        print("Error: Could not find a suitable .elf, .bin or .zip asset in the latest release.")
        return

    try:
        with open(JSON_FILE, "r") as f:
            payloads = json.load(f)
    except FileNotFoundError:
        payloads = []

    source_url = f"https://github.com/{owner}/{repo}/releases"
    if any(p.get("source") == source_url for p in payloads):
        print(f"Error: A payload from {source_url} already exists in the JSON.")
        return

    gh_url = selected_asset["browser_download_url"]
    new_version = release["tag_name"]
    is_zip = selected_asset["name"].endswith(".zip")
    
    if is_zip:
        ext = "elf" # Default for zip extraction
    else:
        ext = selected_asset["name"].rsplit('.', 1)[1] if '.' in selected_asset["name"] else "bin"
        
    # Format: repo_name_version.ext
    filename = f"{repo}_{new_version}.{ext}"
    filepath = os.path.join(PAYLOADS_DIR, filename)
    
    extract_file = None
    if is_zip:
        print(f"  Detected ZIP archive: {selected_asset['name']}")
        with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
            tmp_path = tmp_file.name
            
        print(f"  Downloading zip to temporary file...")
        try:
            req = urllib.request.Request(gh_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response:
                with open(tmp_path, 'wb') as f:
                    f.write(response.read())
            
            with zipfile.ZipFile(tmp_path, 'r') as z:
                elf_files = [f for f in z.namelist() if f.lower().endswith('.elf')]
                print(f"  Files inside zip: {', '.join(z.namelist())}")
                
                extract_file = input(f"  Path inside zip to extract the .elf (found: {', '.join(elf_files)}): ").strip()
                if not extract_file:
                    if len(elf_files) == 1:
                        extract_path = elf_files[0]
                        print(f"  Auto-selecting: {extract_path}")
                        extract_file = extract_path
                    elif len(elf_files) > 1:
                        print(f"  Error: Multiple .elf files found in zip, please specify one.")
                        os.remove(tmp_path)
                        return
                    else:
                        print(f"  Error: No .elf files found in zip.")
                        os.remove(tmp_path)
                        return
                
                if not os.path.exists(PAYLOADS_DIR):
                    os.makedirs(PAYLOADS_DIR)
                
                print(f"  Extracting {extract_file} to {filename}...")
                with z.open(extract_file) as source, open(filepath, 'wb') as target:
                    shutil.copyfileobj(source, target)
            os.remove(tmp_path)
            success = True
        except Exception as e:
            print(f"  Error processing zip: {e}")
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            return
    else:
        success = download_file(gh_url, filename)

    if success:
        new_item = {
            "name": repo,
            "filename": filename,
            "url": f"{BASE_URL}/{filename}",
            "source": source_url,
            "source_direct": gh_url,
            "description": description,
            "last_update": release["published_at"][:10],
            "version": new_version,
            "checksum": calculate_checksum(filepath)
        }
        if extract_file:
            new_item["extract_file"] = extract_file
        
        payloads.append(new_item)
        payloads.sort(key=lambda x: x.get("last_update", ""), reverse=True)
        payloads = [reorder_item(p) for p in payloads]
        
        with open(JSON_FILE, "w") as f:
            json.dump(payloads, f, indent=2)
            
        print(f"\nSuccessfully added and downloaded {repo} to {JSON_FILE}")
        update_readme()
    else:
        print("\nFailed to download the payload.")

if __name__ == "__main__":
    add_payload()
