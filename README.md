# photo-scan-tools

## Description
This repo contains a set of scripts developed by me (and ChatGPT 😄) during the process of scanning my family’s old photo archive stored on film. The main goal of these scripts was to automate things like EXIF metadata insertion and cropping out excess margins of the scanner.

Since this was an archival task, all these scripts are tailored for 48-bit TIFF images. 24-bit TIFFs should work fine as well. Other image formats may partially work (if image transformations are not used) but not tested at all. As such archives usually contain many photos, all scripts work on a <ins>per-directory</ins> basis - <ins>not</ins> per-file.

The scanner I used was _"Nikon COOLSCAN IV ED"_ with its _"Nikon Scan 4.0.2 W"_ software so all metadata processing related to the scanner is adjusted for that particular set. Other combinations may still work but code alterations may be required.
- [**_exif-writer.py_**](#exifwriter) - <ins>non-destructively</ins> crops/rotates/flips images and inserts EXIF metadata in them.
- [**_crop-finder.py_**](#cropfinder) - finds masked area (which contains actual image, leaving excess margins out) in the image, lists it into csv file and adds crop data into original images filenames.
- **_scandata-lister.py_** - lists metadata related to the scanner (scan date, scanner model, image size, resolution, analog gain, etc.) into single csv file.
- **_xmp-extractor.py_** - extracts XMP tag contents (which contains ACR develop settings) into sidecar xmp file. Usefull to fix shitty Adobe policy of storing develop settings exclusively inside original TIFF files wrecking EXIF data in the process.

## Disclaimer
These scripts were developed for a one-off task and are tailored to specific input data and conditions. The code has not been thoroughly tested for broader use cases. While basic safeguards are in place, error handling and edge case coverage are limited, and unexpected input may not be handled gracefully. Use with caution and adapt as needed for your environment.

---

<a name="exifwriter"></a>
## exif-writer.py
The main script from the set. **Non-destructively** crops/rotates/flips images and inserts EXIF metadata in them.

### Metadata:
Script processes metadata in a python dictionary form. Keys of the metadata dictionary are equal to tag names. Those which are intended for exiftool are provided to it as-is at the step of command build-up. Also there are extra tags added to this dictionary (for unification of workflow) containing settings for script workflow, image transformations and other service data. All these extra tags are stripped out of this dictionary before exiftool command build-up.

#### Build-up concept:
Script gathers metadata for the particular image file from various sources on a step-by-step basis appending (or updating) values on every step. Here is a flow of this process:
1. Initial tag values are set in the script code itself (`metadata_default` dictionary);
2. Hierarchically updated from metafiles in each directory level (`--metafile` argument, `metadata_get_file` function);
3. Updated from the data embedded in the image filename (`metadata_get_path` function);
4. Updated from the scanner metadata embedded in the image itself (`metadata_get_scanner` function);
5. Filled by the script based on predefined conditions and values of other tags; (`metadata_update_conditional` function);
6. Automatically filled by the script, for example file modify date of GPS reference tags (`metadata_autofill` function).

<ins>For example:</ins><br/>
You have scanned images from multiple film rolls from several cameras. You put images of each film roll in its separate directory and all these directories are in a respectful directory corresponding to a camera. And all these directories are inside a base directory containing all the scans. So you have a hierarchical structure. Now you can do the following:
1. Set default tag values in the script itself like `FileSource`, `Orientation`, etc. but mostly `Marker` for tag placeholders - these values will apply to all images processed by the script;
2. Update metadata hierarchically:
    1. Put metafile into base directory with `ColorSpace`, `Artist`, `Copyright`, etc. tags - these values will be applied to all images in that base directory;
    2. Put metafile into each camera directory with `Make`, `Model`, etc. tags - these values will be applied to images of that particular camera;
    3. Put metafile into each film roll directory with `ISO`, `ImageHistory:Film`, etc. tags - these values will be applied to images from that particular film roll;
3. Add information about crop area, date, orientation, GPS coordinates, etc. into filename - these values will be applied to that particular image;
4. Information about the scanner and its settings will be taken from already existing tags in the image file;
5. In case of fixed point and shoot camera some information can be filled based on camera model and it's settings - fixed `ExposureTime` and `FocalLength`, `FNumber` based on the `EXIF:Flash` state and so on;
6. For tags which value was set to `Marker.AUTO` actual value will be defined by the script logic - `ShutterSpeedValue` from `ExposureTime`, `ApertureValue` from `FNumber`, `ModifyDate` from current datetime and so on.

##### Image History
EXIF tag `0x9213` `ImageHistory` is treated in a special way. It is built by the script from other tags with `ImageHistory:` prefix in their names and from scanner metadata. The resulting string format is similar to JSON but simplified a bit for more human-readable form.

#### Tag value markers
While looking into EXIF specification tag list and observing all that zoo of tags I decided to define a list of meaningful tags in the script and ability to lock that list with `Script:LockTagList` option - meaning any tags outside that list specified anywhere in metadata build-up process will be simply ignored. To assign values to the tags in that list a `Marker` concept was introduced. If there is no default predefined value for a tag its value can be assigned to a Marker value which will tell the script about tag behaviour.
|  Value  | Description |
| -------- | -------- | 
| `Marker.MANDATORY` | Error will be raised if tag value won't be assigned |
| `Marker.OPTIONAL`  | Tag will not be added if its value wasn't assigned |
| `Marker.AUTO`      | Tag value will be acquired by the script automatically |
| `Marker.SKIP`      | Tag will not be added but will remain if already existed in the source file |
| `Marker.DELETE`    | Tag will be deleted from the result |

#### Assignation format
##### in the script:
Ordinary python dictionary.

##### in metafile:
Tag name and value are set by `<tag_name> = <tag_value>` pair on a dedicated line. All lines starting with `#` are ignored. Values are parsed using `ast.literal_eval()` so data types are recognized and complex structures can be used. For example `ImageTransform:Compression = "jpeg", {'level': 90}` will be parsed correctly as a tuple containing a string and a dictionary. Also Markers can be used here by assigning their enum values. For example `MakerNotes:All = <DELETE>` will set `MakerNotes:All` tag to `Marker.DELETE` value which will delete it from the resulting file.

##### in filename:
Tag/block names are encoded into single letter prefix followed immediately by tag/block value in its dedicated format. Different tags/blocks are separated by `_` (underscore) character.
|  Prefix  | Format | Tags | Description |
| :--------: | -------- |  -------- |  -------- | 
|  | `<FILM_ID>-<FRAME_NUM>` | `ReelName`, `ImageNumber`, `Extra:FilmID`, `Extra:FilmFrameNumber` | film identifier and frame number in film (**mandatory** with **no prefix**!) |
| `S` | `<STRIP_ID>-<FRAME_NUM_IN_STRIP>` | `Extra:StripID`, `Extra:StripFrameNumber` | film strip identifier and frame number on that strip |
| `C` | `<LEFT>[-<TOP>[-<WIDTH>[-<HEIGHT>]]]` | `ImageTransform:Enabled`, `ImageTransform:Crop` | image transformation is enabled and crop area defined |
| `R` | `<ROTATION_CW{ANGLE\|90CW\|90CCW}>[<FLIP{H\|V}>]` | `ImageTransform:Enabled`, `ImageTransform:Rotate`, `ImageTransform:Flip` | image transformation is enabled, rotation angle and/or flip axis are defined |
| `Z` | `<COMPRESSION_NAME>` | `ImageTransform:Enabled`, `ImageTransform:Compression` | image transformation is enabled and image compression defined |
| `O` | `<VALUE{<CODE>\|90CW\|90CCW\|180}>` | `Orientation` | image orientation, either numeric code from corresponding dictionary or predefined human-friendly values |
| `D` | `<YYYY>[-<MM>[-<DD>[-<hh>[-<mm>[-<ss>[@<tzo_hh>[-<tzo_mm>]]]]]]]` | `DateTimeOriginal`, `OffsetTimeOriginal` | datetime of original image being taken + timezone offset |
| `T` | `<EXPOSURE_TIME{<TIME_IN_SECONDS>\|'<DENOMINATOR>}>` | `ExposureTime`, `ShutterSpeedValue` | exposure-time/shutter-speed in seconds (float) or 1/denominator prefixed by `'` character |
| `A` | `<F-NUMBER>` | `FNumber`, `ApertureValue` | f-number/aperture value (float) |
| `I` | `<ISO_VALUE>` | `ISO` | ISO value |
| `F` | `<EXIF_FLASH_VALUE_NUMBER>` | `EXIF:Flash` | flash state, numeric code from corresponding dictionary |
| `M` | `[<MODEL>][@<MAKER>]` | `Make`, `Model` | camera model and/or maker |
| `L` | `<FOCAL_LENGTH>` | `FocalLength` | lens focal length |
| `G` | `<{+\|-\|N\|S}LATITUDE_DEG>,<{+\|-\|E\|W}LONGITUDE_DEG>[,<ALTITUDE_M>]` | `GPSLatitude`, `GPSLongitude`, `GPSAltitude` | GNSS coordinates and altitude, either signed or prefixed value in degrees |
| `U` | `<USER_COMMENT>` | `UserComment` | user comment (use `&#95;` as underscore in a value) |
| `N` | `<IMAGE_DESCRIPTION>` | `ImageDescription` | image description (use `&#95;` as underscore in a value) |
| `H` | `<IMAGE_TITLE>` | `ImageTitle` | image title (use `&#95;` as underscore in a value) |

<ins>Example:</ins> `1337-01_S1-1_C82-126-4096-2656_F25_O8_D1985-10-26-01-21@-7_G34.006100,-117.930600.tif`

### Command line arguments
`exif-writer.py <base_dir> <output_path> [options ...]`

| &nbsp;&nbsp;&nbsp;Argument&nbsp;&nbsp;&nbsp; | Data | Default value | Description |
| --------------- | :----: | :--------------------: | -------- |
| `base_dir`    | `dir`  |  | base directory of image files |
| `output_path` | `path` |  | output files path (use template). If set to existing directory copies the structure of base directory. |
| `--tempdir`   | `dir`  | _deepest already existing directory in output path before template variable resolution_ | directory to store temporary files |
| `--exiftool`  | `path` | _<script_dir> -> PATH_ | path to exiftool |
| `--dirdepth`  | `int`  | `-1` | max directory depth (-1 for no limit) |
| `--metafile`  | `path` | `metadata.txt` | metafile path  |
| `--wildcards` | `str`  | `*.tif,*.tiff` | comma-separated list of file patterns |

#### Output path template
Template variables can (and should) be used as an output path. You can use any existing tag value with the following syntax: `{<tag_name>?<format>}`. Tag values are sanitized before substitution to safely eliminate characters forbidden in path.

<ins>Example:</ins> `C:\scan\output\{Make} {Model}\{Extra:FilmID}\{Extra:FilmID}-{Extra:FilmFrameNumber?02d}.{Extra:FileNameExtension}`

---

<a name="cropfinder"></a>
## crop-finder.py
My original plan during the scanning process was to crop out the scanner's extra margins using some third-party software with a convenient GUI. But in reality, I ran into several issues — some software doesn’t fully support 48-bit images, and some re-encodes the image data entirely during the process. So, I created this script to assist with that task.

Now, you can mask the useful image area with a solid color (leaving excess margins unchanged) in any image editor of your choice and save the masked file. Since this is just a temporary copy for extracting crop data, bit depth and data re-encoding are no longer a concern.

For example, in Adobe Photoshop: select the rectangular marquee tool (set to fixed size for consistency), select the area you want to keep in the final image, delete its contents (with a solid color selected), then save the file. It’s best to use **black (`#000000`)** as the fill color, since it works identically across all bit depths — 0 is 0, after all.

Repeat this process for all image files, and then run this script on them. It will generate crop area coordinates for each image, which can then be used by `exif-writer.py` to apply actual non-destructive image cropping.

### Command line arguments
`crop-finder.py [options ...]`

| &nbsp;&nbsp;&nbsp;Argument&nbsp;&nbsp;&nbsp; | Data | Default value | Description |
| --------------- | :----: | :--------------------: | -------- |
| `--search`    | `dir`  |  | Base directory with cropped (masked) images to search for crop area |
| `--dirdepth`  | `int`  | `-1` | Max directory depth (-1 for no limit) |
| `--wildcards` | `str`  | `*.tif,*.tiff` | Comma-separated list of file patterns |
| `--to-csv`    | `path`  | `crop.csv` | Write crop data to CSV file |
| `--from-csv`  | `path`  | `crop.csv` | Use previously saved crop data in CSV file |
| `--search`    | `dir`  |  | Base directory with cropped (masked) images to search for crop area |
| `--rename` | `dir` |  | Rename files using detected or loaded crop data. Provide path to base directory |
| `--unname` | `dir` |  | Revert crop-data-based renaming of files. Provide path to base directory |
| `--crop-color` | `R,G,B`  | `0,0,0` | RGB color used for crop mask. Use values consistent with image color depth |
| `--check-multiple` | `int`  | `8` | check that width and height are divisible by N |
