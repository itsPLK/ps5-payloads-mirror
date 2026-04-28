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

JSON_FILE = "payloads.json"
PAYLOADS_DIR = "payloads"
BASE_URL = "https://github.com/itsPLK/ps5-payloads-mirror/releases/download/payloads-mirror"

def get_repo_info(url):
    # Extract owner and repo from various GitHub URL formats
    match = re.search(r"github\.com/([^/]+)/([^/]+)", url)
    if match:
        owner = match.group(1)
        repo = match.group(2).rstrip('/')
        if repo.endswith('.git'):
            repo = repo[:-4]
        if repo == 'releases':
            parts = url.split('/')
            idx = parts.index('github.com')
            owner = parts[idx+1]
            repo = parts[idx+2]
        return owner, repo
    return None, None

def get_latest_release(owner, repo):
    try:
        cmd = ["gh", "api", f"repos/{owner}/{repo}/releases/latest"]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return json.loads(result.stdout)
    except Exception as e:
        print(f"Error fetching {owner}/{repo}: {e}")
        return None

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

def update_payloads():
    try:
        with open(JSON_FILE, "r") as f:
            payloads = json.load(f)
    except FileNotFoundError:
        print(f"Error: {JSON_FILE} not found.")
        return

    updated = False
    for item in payloads:
        source = item.get("source")
        if not source or "github.com" not in source:
            # Handle ps5debug case
            if item.get("name") == "ps5debug":
                if not item["url"].startswith(BASE_URL):
                     item["url"] = f"{BASE_URL}/{item['filename']}"
                     updated = True
            continue
            
        owner, repo_name = get_repo_info(source)
        if not owner:
            continue
            
        print(f"Checking {owner}/{repo_name}...")
        release = get_latest_release(owner, repo_name)
        if not release:
            continue
            
        assets = release.get("assets", [])
        if not assets:
            continue
            
        has_extract = "extract_file" in item
        preferred_ext = ".bin" if "etaHEN" in repo_name else ".elf"
        
        def score_asset(name):
            name_lower = name.lower()
            
            # If we already have extract_file, we might be looking for a zip
            if has_extract and name.endswith(".zip"):
                return 20
                
            if not (name.endswith(".elf") or name.endswith(".bin") or (has_extract and name.endswith(".zip"))):
                if not name.endswith(preferred_ext):
                    return -1
            
            score = 0
            if name.endswith(preferred_ext):
                score += 5
            if "ps5" in name_lower:
                score += 10
            if "ps4" in name_lower:
                score -= 10
            if "install" in name_lower:
                score -= 5
            score -= len(name) / 100.0 
            return score

        selected_asset = None
        best_score = -2
        for asset in assets:
            score = score_asset(asset["name"])
            if score > best_score:
                best_score = score
                selected_asset = asset
        
        if selected_asset and best_score > -1:
            gh_url = selected_asset["browser_download_url"]
            original_filename = selected_asset["name"]
            new_version = release["tag_name"]
            new_date = release["published_at"][:10]
            is_zip = original_filename.endswith(".zip")
            
            # Format: repo_name_version.ext
            if is_zip:
                ext = "elf"
            else:
                ext = original_filename.rsplit('.', 1)[1] if '.' in original_filename else "bin"
            
            new_filename = f"{repo_name}_{new_version}.{ext}"
            
            proposed_name = repo_name
            final_name = item.get("name", proposed_name)
            
            filepath = os.path.join(PAYLOADS_DIR, new_filename)
            needs_download = (
                item.get("version") != new_version or 
                item.get("filename") != new_filename or
                not os.path.exists(filepath)
            )
            
            if needs_download:
                print(f"  Update found: {item.get('version', 'none')} -> {new_version}")
                
                # Delete old file
                if item.get("filename") and item["filename"] != new_filename:
                    old_path = os.path.join(PAYLOADS_DIR, item["filename"])
                    if os.path.exists(old_path):
                        print(f"  Removing old file {item['filename']}...")
                        os.remove(old_path)

                success = False
                extract_file = item.get("extract_file")
                
                if is_zip:
                    print(f"  Processing ZIP update: {original_filename}")
                    with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
                        tmp_path = tmp_file.name
                    
                    try:
                        req = urllib.request.Request(gh_url, headers={'User-Agent': 'Mozilla/5.0'})
                        with urllib.request.urlopen(req) as response:
                            with open(tmp_path, 'wb') as f:
                                f.write(response.read())
                        
                        with zipfile.ZipFile(tmp_path, 'r') as z:
                            if not extract_file:
                                elf_files = [f for f in z.namelist() if f.lower().endswith('.elf')]
                                if len(elf_files) == 1:
                                    extract_file = elf_files[0]
                                    print(f"  Auto-detected internal file: {extract_file}")
                                elif len(elf_files) > 1:
                                    print(f"  Error: Multiple .elf files in zip and no extract_file in JSON.")
                                    os.remove(tmp_path)
                                    continue
                                else:
                                    print(f"  Error: No .elf files found in zip.")
                                    os.remove(tmp_path)
                                    continue
                            
                            print(f"  Extracting {extract_file} to {new_filename}...")
                            with z.open(extract_file) as source_f, open(filepath, 'wb') as target_f:
                                shutil.copyfileobj(source_f, target_f)
                        os.remove(tmp_path)
                        success = True
                    except Exception as e:
                        print(f"  Error processing zip update: {e}")
                        if os.path.exists(tmp_path):
                            os.remove(tmp_path)
                else:
                    success = download_file(gh_url, new_filename)
                    # If we switched from zip to direct file, remove extract_file
                    if success and "extract_file" in item:
                        del item["extract_file"]

                if success:
                    item["name"] = final_name
                    item["version"] = new_version
                    item["filename"] = new_filename
                    item["url"] = f"{BASE_URL}/{new_filename}"
                    item["source_direct"] = gh_url
                    item["last_update"] = new_date
                    item["checksum"] = calculate_checksum(filepath)
                    if extract_file and is_zip:
                        item["extract_file"] = extract_file
                    updated = True
                else:
                    print(f"  Skipping update due to download failure.")
            else:
                print(f"  Already up to date ({new_version})")
        else:
            print(f"  No suitable asset found for {source}")
                
    for item in payloads:
        if item.get("filename"):
            item["url"] = f"{BASE_URL}/{item['filename']}"
            
    payloads.sort(key=lambda x: x.get("last_update", ""), reverse=True)
    payloads = [reorder_item(p) for p in payloads]
    
    with open(JSON_FILE, "w") as f:
        json.dump(payloads, f, indent=2)
    
    if updated:
        print(f"\nSuccessfully updated files and sorted {JSON_FILE}")
    else:
        print(f"\nSorted {JSON_FILE} (no new files downloaded).")

if __name__ == "__main__":
    update_payloads()
