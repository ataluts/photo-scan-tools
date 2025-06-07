import sys, argparse
import os, shutil, fnmatch
import csv
import subprocess, exiftool
import time
from pathlib import Path

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

#Format gain value
def format_gain(value):
    if value == 0: return " 0.00"
    return format(value, "+.2f")

#Get metadata
def get_metadata(file_path: Path):
    result = {}
    with exiftool.ExifToolHelper(executable = exiftool_exe) as exif:
        #EXIF tags
        output = exif.get_tags(str(file_path), ['EXIF:all'])[0]
        date = output.get('EXIF:ModifyDate', '').split()
        if len(date) > 1: date = date[0].replace('.', '-') + ' ' + date[1].replace('.', ':')
        result['Date'] = date
        result['Scanner'] = output['EXIF:Model']
        result['Software'] = output['EXIF:Software']
        result['Width'] = output['EXIF:ImageWidth']
        result['Height'] = output['EXIF:ImageHeight']
        result['Resolution'] = output['EXIF:XResolution']

        #NikonScanIFD from Nikon MakerNotes
        output = exif.get_tags(file_path, 'NikonScan:all')[0]
        for tag_name, value in output.items():
            if tag_name == 'SourceFile': continue  #skip this key entirely
            if tag_name.startswith('MakerNotes:'): tag_name = tag_name[len('MakerNotes:'):]  #remove prefix
            result[tag_name] = value

        #fix NikonScan bug for negative gain values (negative values higher than they set in GUI by 0.01 )
        if "Nikon Scan" in result['Software']:
            if 'MasterGain' in result:
                value = result['MasterGain']
                if value < 0: value -= 0.01
                result['MasterGain'] = format_gain(value)
            if 'ColorGain' in result:
                try:
                    color_gain = [float(part) for part in result['ColorGain'].split()]
                    tmp = []
                    for value in color_gain:
                        if value < 0: value -= 0.01
                        tmp.append(value)
                    color_gain = [format_gain(tmp[0]), format_gain(tmp[1]), format_gain(tmp[2])]
                except ValueError:
                    color_gain = ['', '', '']
                
                #split ColorGain into separate values while maintaining order
                tmp = {}
                for key, value in result.items():
                    if key == 'ColorGain':
                        tmp['ColorGainR'] = color_gain[0]
                        tmp['ColorGainG'] = color_gain[1]
                        tmp['ColorGainB'] = color_gain[2]
                    else:
                        tmp[key] = value
                result = tmp
    return result

def write_csv(csv_path, data):
    #find all possible keys (tags) in dictionaries
    columns = []
    for item in data:
        for key in item.keys():
            if key in columns: continue
            columns.append(key)

    #bring filename to the first column
    if 'File' in columns: columns.insert(0, columns.pop(columns.index('File')))
    
    try:
        with open(csv_path, mode="w", newline="") as f:
            writer = csv.writer(f, quoting=csv.QUOTE_NONNUMERIC)
            #write CSV header
            writer.writerow(columns)
            #drite rows
            for item in data:
                row = []
                for column in columns:
                    row.append(item.get(column, ''))
                writer.writerow(row)
        return "done."
    except Exception as e:
        return f"error: {e}"

def main():
    #parse call arguments
    parser = argparse.ArgumentParser(description="List scan data into CSV file.")
    parser.add_argument("base_dir", type=Path, help="Base directory of image files.")
    parser.add_argument('--output', type=Path, default=None, help="CSV-file path.")
    parser.add_argument("--exiftool", type=Path, default=None, help="Path to exiftool.")
    parser.add_argument("--dirdepth", type=int, default=-1, help="Max directory depth (-1 for no limit) [default: -1].")
    parser.add_argument("--wildcards", type=str, default="*.tif,*.tiff", help="Comma-separated list of file patterns [default: '*.tif,*.tiff'].")
    parser.add_argument('--omitdir', action='store_true', help='Omit directory in a file path.')
    parser.add_argument('--cleanname', action='store_true', help='Write only basename as a filename (strip any additional metadata in it)')
    args = parser.parse_args()

    wildcards = [w.strip() for w in args.wildcards.split(",")]
    base_dir = args.base_dir.resolve()
    dir_depth = args.dirdepth
    clean_name = args.cleanname
    omit_dir = args.omitdir
    exiftool_find(args.exiftool)
    if args.output is None:
        output_path = base_dir / 'scandata.csv'
    else:
        output_path = args.output

    #displaying parameters
    print("Scan data lister by Alexander Taluts.")
    print(f"    Exiftool        : {exiftool_exe}")
    print(f"    Base directory  : {base_dir}")
    print(f"    Directory depth : {dir_depth}")
    print(f"    Wildcards       : {wildcards}")
    print(f"    Output          : {output_path}")
    print(f"    Omit directory  : {omit_dir}")
    print(f"    Clean name      : {clean_name}")
    print("")

    print("Processing files...")
    file_counter = 0
    time_start = time.monotonic()
    data = []
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
                print(f"{file_counter}. {input_path}:")
                result = get_metadata(input_path)
                print(f"    {result}")
                print("")
                if isinstance(result, dict):
                    if clean_name:
                        result['File'] = input_path.stem.split('_')[0] + input_path.suffix
                    else:
                        result['File'] = input_path.name
                    if not omit_dir:
                        result['File'] = relative_path / result['File']
                    data.append(result)

    duration = int(time.monotonic() - time_start)
    hours, remainder = divmod(duration, 3600)
    minutes, seconds = divmod(remainder, 60)
    print(f"Processed {file_counter} files in {hours:02}:{minutes:02}:{seconds:02}.")
    print(f"Writing data into {output_path}: ", end = "")
    result = write_csv(output_path, data)
    print(result)


if __name__ == "__main__":
    main()