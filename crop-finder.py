import sys
import csv
import argparse
from pathlib import Path
import tifffile
import numpy as np
import fnmatch
import re

def find_crop_box(cropped_img_array, crop_color):
    # Ensure crop_color has same dtype as image array
    crop_color = np.array([np.array([c], dtype=cropped_img_array.dtype)[0] for c in crop_color], dtype=cropped_img_array.dtype)
    mask = np.all(cropped_img_array == crop_color, axis=-1)
    if not np.any(mask):
        return None
    ys, xs = np.where(mask)
    top, bottom = ys.min(), ys.max()
    left, right = xs.min(), xs.max()
    width = right - left + 1
    height = bottom - top + 1
    return left, top, width, height

def resolve_path(base: Path, path_str: str) -> Path:
    p = Path(path_str)
    return p if p.is_absolute() else (base / p).resolve()

def match_patterns(filename, patterns):
    return any(fnmatch.fnmatchcase(filename, pattern) for pattern in patterns)

def iter_files(base_dir: Path, patterns, depth):
    for path in base_dir.rglob("*"):
        if path.is_file() and match_patterns(path.name, patterns):
            relative_parts = path.relative_to(base_dir).parts
            if depth >= 0 and len(relative_parts) - 1 > depth:
                continue
            yield path

def process_directory(base_dir, crop_color, depth, check_multiple, file_patterns):
    crop_data = []
    processed_dirs = set()
    for path in iter_files(base_dir, file_patterns, depth):
        relative_parts = path.relative_to(base_dir).parts
        current_dir = Path(*relative_parts[:-1])
        if current_dir not in processed_dirs:
            print(f"Processing directory: {current_dir.as_posix() or '.'}")
            processed_dirs.add(current_dir)

        relative_path = path.relative_to(base_dir).as_posix()
        print(f"{relative_path}: ", end="")
        try:
            cropped_array = tifffile.imread(path)

            #Check image channels and validate crop_color length
            if cropped_array.ndim == 2:
                #grayscale
                if len(crop_color) != 1:
                    status = "error"
                    print(f"Grayscale image, crop-color must be single integer. Skipping {relative_path}")
                    crop_data.append([f"{relative_path}", -1, -1, -1, -1, status])
                    continue
            elif cropped_array.ndim == 3 and cropped_array.shape[2] == 3:
                #RGB
                if len(crop_color) != 3:
                    status = "error"
                    print(f"RGB image, crop-color must be three integers. Skipping {relative_path}")
                    crop_data.append([f"{relative_path}", -1, -1, -1, -1, status])
                    continue
            else:
                #unknown
                status = "error"
                print("not a 3-channel RGB image!")
                crop_data.append([f"{relative_path}", -1, -1, -1, -1, status])
                continue

            #Validate crop_color against dtype range for this image
            dtype = cropped_array.dtype
            if not np.issubdtype(dtype, np.integer):
                status = "error"
                print(f"Unsupported image dtype: {dtype}. Only integer TIFFs (8/16-bit) are supported.")
                crop_data.append([f"{relative_path}", -1, -1, -1, -1, status])
                continue
            max_value = np.iinfo(dtype).max
            if not all(0 <= c <= max_value for c in crop_color):
                status = "error"
                print(f"crop-color {crop_color} out of range for {dtype} (0..{max_value}), error")
                crop_data.append([f"{relative_path}", -1, -1, -1, -1, status])
                continue

            crop_box = find_crop_box(cropped_array, crop_color)
            if not crop_box:
                status = "!found"
                print("no crop area found!")
                crop_data.append([f"{relative_path}", -1, -1, -1, -1, status])
                continue

            left, top, width, height = crop_box
            if width % check_multiple != 0 or height % check_multiple != 0:
                status = f"!mult{check_multiple}"
            else:
                status = "ok"
            print(f"crop area found ({left}, {top}, {width}, {height}), {status}")
            crop_data.append([f"{relative_path}", left, top, width, height, status])
        except Exception as e:
            print(f"error: {e}")
            crop_data.append([f"{relative_path}", -1, -1, -1, -1, "error"])
    return crop_data

def write_csv(csv_path, crop_data):
    with open(csv_path, mode="w", newline="") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_NONNUMERIC)
        writer.writerow(["file", "left", "top", "width", "height", "status"])
        writer.writerows(crop_data)
    print(f"Crop data written to: {csv_path}")

def rename_files_from_data(rename_dir: Path, crop_data, file_patterns, depth):
    all_files = [f for f in iter_files(rename_dir, file_patterns, depth)]
    crop_dict = {row[0]: row for row in crop_data}
    for file_path in all_files:
        relative_path = file_path.relative_to(rename_dir).as_posix()
        if relative_path not in crop_dict:
            print(f"{relative_path} : no crop data found!")
            continue
        row = crop_dict[relative_path]
        try:
            filename, left, top, width, height, status = row
            if not all([left, top, width, height]):
                print(f"{relative_path} : crop data incomplete!")
                continue

            try:
                left = int(left)
                top = int(top)
                width = int(width)
                height = int(height)
            except ValueError:
                print(f"{relative_path} : invalid numeric data!")
                continue

            if left < 0 or top < 0 or width <= 0 or height <= 0:
                print(f"{relative_path} : invalid crop data! ({left},{top},{width},{height})")
                continue

            suffix = f"_C{left}-{top}-{width}-{height}"
            if '__' not in file_path.stem:
                suffix = '_' + suffix
            new_name = file_path.stem + suffix + file_path.suffix
            new_path = file_path.with_name(new_name)
            file_path.rename(new_path)
            msg = f"{file_path.name} → {new_name}"
            if status != "ok":
                msg += f" [warning: status = {status}]"
            print(msg)
        except Exception as e:
            print(f"{relative_path} : error! {e}")

def read_csv(csv_path):
    with open(csv_path, newline="") as f:
        reader = csv.reader(f)
        headers = next(reader)
        return list(reader)

def unname_files(rename_dir: Path, file_patterns, depth):
    pattern = re.compile(r"_C\d+-\d+-\d+-\d+")
    for file_path in iter_files(rename_dir, file_patterns, depth):
        new_stem = pattern.sub("", file_path.stem)
        if new_stem == file_path.stem:
            continue
        if new_stem.endswith("_"):
            new_stem = new_stem[:-1]
        new_path = file_path.with_name(new_stem + file_path.suffix)
        file_path.rename(new_path)
        print(f"{file_path.name} → {new_path.name}")

def main():
    parser = argparse.ArgumentParser(description="Crop mask tool with optional rename and CSV export.")
    parser.add_argument("--search", help="Directory with cropped (masked) images to search for crop area")
    parser.add_argument("--rename", nargs="?", const=True, help="Rename files using detected or loaded crop data. Provide path to base directory")
    parser.add_argument("--unname", nargs="?", const=True, help="Revert crop-data-based renaming of files. Provide path to base directory")
    parser.add_argument("--to-csv", nargs="?", const=True, help="Write crop data to CSV file. Provide filename or use default 'crop.csv' in search dir")
    parser.add_argument("--from-csv", help="Use previously saved crop data in CSV file for renaming")
    parser.add_argument("--dirdepth", type=int, default=-1, help="Depth of folder structure to search (-1 means unlimited, default: -1)")
    parser.add_argument("--crop-color", type=str, default="0,0,0", help="Color used for crop mask. Single integer for grayscale, comma-separated for RGB (default: 0,0,0). Use values consistent with image color depth")
    parser.add_argument("--check-multiple", type=int, default=8, help="Check that crop dimensions are multiple of this value (default: 8)")
    parser.add_argument("--wildcards", type=str, default="*.tif,*.tiff", help="Comma-separated list of file patterns to process (default: *.tif,*.tiff)")

    args = parser.parse_args()
    script_dir = Path(__file__).resolve().parent
    file_patterns = [pat.strip() for pat in args.wildcards.split(",") if pat.strip()]

    if args.unname:
        unname_dir = Path(args.unname) if args.unname is not True else Path.cwd()
        unname_files(unname_dir, file_patterns, args.dirdepth)
        return

    if args.search:
        search_dir = resolve_path(script_dir, args.search)
        if not search_dir.is_dir():
            print("Error: --search directory does not exist.")
            sys.exit(1)

        try:
            crop_color = tuple(int(c, 0) for c in args.crop_color.split(","))
            if (len(crop_color) != 3 and len(crop_color) != 1) or not all(c >= 0 for c in crop_color):
                raise ValueError
        except ValueError:
            print("Error: --crop-color value must be positive integer(s) (decimal or 0x hex).")
            sys.exit(1)

        crop_data = process_directory(search_dir, crop_color, args.dirdepth, args.check_multiple, file_patterns)

        if args.to_csv:
            csv_path = (search_dir / "crop.csv") if args.to_csv is True else resolve_path(search_dir, args.to_csv)
            write_csv(csv_path, crop_data)

        if args.rename:
            rename_dir = Path(args.rename) if args.rename is not True else search_dir
            rename_files_from_data(rename_dir, crop_data, file_patterns, args.dirdepth)

    elif args.from_csv:
        if not args.rename:
            print("Error: --rename is required when using --from-csv.")
            sys.exit(1)

        rename_dir = Path(args.rename) if args.rename is not True else Path.cwd()
        csv_path = resolve_path(rename_dir, args.from_csv)
        crop_data = read_csv(csv_path)
        print(f"Loaded crop data from: {csv_path}")
        rename_files_from_data(rename_dir, crop_data, file_patterns, args.dirdepth)
    else:
        print("Error: --search or --from-csv is required.")
        sys.exit(1)

if __name__ == "__main__":
    main()
