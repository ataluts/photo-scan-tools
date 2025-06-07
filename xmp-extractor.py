import sys, argparse
import os, shutil, fnmatch
from pathlib import Path
import subprocess, exiftool
import time

#Exiftool path
exiftool_exe = None

#Get exiftool executable name
def exiftool_getname():
    return "exiftool.exe" if sys.platform.startswith("win") else "exiftool"

#Find exiftool
def exiftool_find(search_list: Path = None):
    global exiftool_exe
    #if already resolved -> do nothing
    if exiftool_exe is not None: return

    if isinstance(search_list, (tuple, list)): pass
    elif isinstance(search_list, Path): search_list = [search_list]
    else: search_list = []

    #add script directory path to search list
    search_list.append(Path(sys.argv[0]).resolve().parent) 

    #search in the search list
    for path in search_list:
        if path.name != exiftool_getname():
            path = path / exiftool_getname()
        if path.is_file():
            exiftool_exe = str(path)
            return
    
    #search in system PATH
    path = shutil.which("exiftool")
    if path:
        exiftool_exe = path
        return

    #not found
    raise FileNotFoundError("ExifTool executable not found in manual path, script directory or system PATH.")

#Extract XMP tags into a file
def xmp_extract(input_path: Path, output_path: Path = None):
    if output_path is None:
        #output path is not provided -> use the source file path with xmp extension
        output_path = input_path.with_suffix(os.path.extsep + 'xmp')

    #save xmp data into a file
    with exiftool.ExifTool(executable = exiftool_exe) as exif:
        xmp_data = exif.execute(*["-XMP", "-b", str(input_path)], raw_bytes = True)  #extract XMP data from image file
    if len(xmp_data) > 0:
        output_path.parent.mkdir(parents = True, exist_ok = True)
        with open(output_path, 'wb') as f:
            f.write(xmp_data)                                                        #save XMP data to a file

        #remove excess tags from file
        xmp_lines_in = output_path.read_text(encoding = 'utf-8').splitlines()
        xmp_lines_out = []
        for line in xmp_lines_in:
            if line.startswith("<?xpacket"): continue
            if len(line.strip()) == 0: continue
            xmp_lines_out.append(line)
        output_path.write_text('\n'.join(xmp_lines_out) + '\n', encoding = 'utf-8')
        return output_path
    else:
        return "No XMP data!"

#Delete XMP tags
def xmp_delete(input_path: Path):
    try:
        with exiftool.ExifTool(executable=exiftool_exe) as exif:
            exif.execute('-overwrite_original', '-XMP=', str(input_path))
            return "tag deleted"
    except subprocess.CalledProcessError as e:
        return f"{e.returncode} - {e.output.decode(errors='ignore')}"
    except Exception as e:
        return f"Unexpected error - {e}"

def main():
    #parse call arguments
    parser = argparse.ArgumentParser(description="Extract xmp tags into a sidecar file.")
    parser.add_argument("base_dir", type=Path, help="Base directory of image files.")
    parser.add_argument('--extract', nargs='?', const=True, help='Extract XMP data into a file. Follow by a directory path to copy directory structure. Use standalone to save xmp files next to source files.')
    parser.add_argument('--delete', action='store_true', help='Delete XMP tag from image files.')
    parser.add_argument("--exiftool", type=Path, default=None, help="Path to exiftool.")
    parser.add_argument("--dirdepth", type=int, default=-1, help="Max directory depth (-1 for no limit) [default: -1].")
    parser.add_argument("--wildcards", type=str, default="*.tif,*.tiff", help="Comma-separated list of file patterns [default: '*.tif,*.tiff'].")
    args = parser.parse_args()

    wildcards = [w.strip() for w in args.wildcards.split(",")]
    base_dir = args.base_dir.resolve()
    dir_depth = args.dirdepth
    exiftool_find(args.exiftool)
    if args.extract is None:
        extract = False
        output_dir = None
    elif args.extract is True:
        extract = True
        output_dir = None
    else:
        extract = True
        output_dir = Path(args.extract).resolve()
    delete = args.delete

    #displaying parameters
    print("XMP Extractor by Alexander Taluts.")
    print(f"    Exiftool        : {exiftool_exe}")
    print(f"    Base directory  : {base_dir}")
    print(f"    Directory depth : {dir_depth}")
    print(f"    Wildcards       : {wildcards}")
    if extract:
        if output_dir is None: extract_str = "<source directory>"
        else: extract_str = f"{output_dir}"
    else:
        extract_str = "False"
    print(f"    Extract         : {extract_str}")
    print(f"    Delete          : {delete}")
    print("")

    print("Processing files...")
    file_counter = 0
    time_start = time.monotonic()
    #iterate through directories
    for root, dirs, files in os.walk(base_dir):
        current_path = Path(root)
        relative_path = current_path.relative_to(base_dir)
        depth = len(relative_path.parts)
        if dir_depth >= 0 and depth > dir_depth:
            continue    #skip if depth is deeper than being set

        #iterate throught files in current directory
        for filename in files:
            if any(fnmatch.fnmatch(filename, pat) for pat in wildcards):
                input_path = current_path / filename
                file_counter += 1
                print(f"{file_counter}. {input_path}", end="")
                if extract:
                    #extract XMP data intop a file
                    if output_dir is None: output_path = None
                    else: output_path = (output_dir / relative_path / filename).with_suffix(os.path.extsep + 'xmp')
                    result = xmp_extract(input_path, output_path)
                    print(f" >> {result}", end="")
                if delete:
                    #delete XMP data from source file
                    result = xmp_delete(input_path)
                    print(f", {result}", end="")
                print("")

    duration = int(time.monotonic() - time_start)
    hours, remainder = divmod(duration, 3600)
    minutes, seconds = divmod(remainder, 60)
    print(f"Finished. Processed {file_counter} files in {hours:02}:{minutes:02}:{seconds:02}.")

if __name__ == "__main__":
    main()