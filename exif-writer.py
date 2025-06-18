import sys, argparse
import os, shutil, fnmatch
from pathlib import Path
import tifffile, numpy as np
import subprocess, exiftool
from datetime import datetime
import time
from zoneinfo import ZoneInfo
from tzlocal import get_localzone_name
from enum import Enum
import locale, copy
import ast, re

_module_date = datetime(2025, 6, 19)
_module_designer = "Alexander Taluts"

#Exiftool path
exiftool_exe = None

#Dictionary class for safe string formatting
class SafeDict(dict):
    def __init__(self, *args, missing_value = 'UNDEF', **kwargs):
        super().__init__(*args, **kwargs)
        self._missing_value = missing_value
    def __missing__(self, key):
        return self._missing_value

#Marker options for tag values 
class Marker(Enum):
    MANDATORY   = '<MANDATORY>'     #error will be raised if tag value won't be assigned
    OPTIONAL    = '<OPTIONAL>'      #tag will not be added if its value wasn't assigned
    AUTO        = '<AUTO>'          #value will be acquired by the script automatically
    SKIP        = '<SKIP>'          #tag will not be added but will remain if already existed in the source file
    DELETE      = '<DELETE>'        #tag will be deleted

#EXIF Flash values
exif_flash_enum = {
    0:  "No Flash",                                                # 0x0
    1:  "Fired",                                                   # 0x1
    5:  "Fired, Return not detected",                              # 0x5
    7:  "Fired, Return detected",                                  # 0x7
    8:  "On, Did not fire",                                        # 0x8
    9:  "On, Fired",                                               # 0x9
    13: "On, Return not detected",                                 # 0xD
    15: "On, Return detected",                                     # 0xF
    16: "Off, Did not fire",                                       # 0x10
    20: "Off, Did not fire, Return not detected",                  # 0x14
    24: "Auto, Did not fire",                                      # 0x18
    25: "Auto, Fired",                                             # 0x19
    29: "Auto, Fired, Return not detected",                        # 0x1D
    31: "Auto, Fired, Return detected",                            # 0x1F
    32: "No flash function",                                       # 0x20
    48: "Off, No flash function",                                  # 0x30
    65: "Fired, Red-eye reduction",                                # 0x41
    69: "Fired, Red-eye reduction, Return not detected",           # 0x45
    71: "Fired, Red-eye reduction, Return detected",               # 0x47
    73: "On, Red-eye reduction",                                   # 0x49
    77: "On, Red-eye reduction, Return not detected",              # 0x4D
    79: "On, Red-eye reduction, Return detected",                  # 0x4F
    80: "Off, Red-eye reduction",                                  # 0x50
    88: "Auto, Did not fire, Red-eye reduction",                   # 0x58
    89: "Auto, Fired, Red-eye reduction",                          # 0x59
    93: "Auto, Fired, Red-eye reduction, Return not detected",     # 0x5D
    95: "Auto, Fired, Red-eye reduction, Return detected",         # 0x5F
}
exif_flash_enum_fired = (1, 5, 7, 9, 25, 29, 31, 65, 69, 71, 73, 77, 79, 89, 93, 95)
exif_flash_enum_notfired = (0, 8, 16, 20, 24, 88)
exif_flash_enum_notpresent = (32, 48)

#EXIF Orientation values
exif_orientation_enum = {
    1: "Horizontal (normal)",
    2: "Mirror horizontal",
    3: "Rotate 180",
    4: "Mirror vertical",
    5: "Mirror horizontal and rotate 270 CW",
    6: "Rotate 90 CW",
    7: "Mirror horizontal and rotate 90 CW",
    8: "Rotate 270 CW"
}

#EXIF ColorSpace values
exif_colorspace_enum = {
    0x0001: "sRGB",
    0x0002: "Adobe RGB",        #not standard EXIF, instead, an Adobe RGB image is indicated by "Uncalibrated" with an InteropIndex of "R03"
    0xfffd: "Wide Gamut RGB",   #not standard EXIF, used by some Sony cameras
    0xfffe: "ICC Profile",      #not standard EXIF, used by some Sony cameras
    0xffff: "Uncalibrated"
}

#EXIF FileSource values
exif_filesource_enum = {
    1: "Film Scanner",
    2: "Reflection Print Scanner",
    3: "Digital Camera"
}

#EXIF ExposureMode values
exif_exposuremode_enum = {
    0: "Auto",
    1: "Manual",
    2: "Auto bracket"
}

#EXIF WhiteBalance values
exif_whitebalance_enum = {
    0: "Auto",
    1: "Manual"
}

#EXIF:GPS Altitude reference values
exif_gps_altituderef_enum = {
    0: "Above Sea Level",
    1: "Below Sea Level",
    2: "Positive Sea Level (sea-level ref)",
    3: "Negative Sea Level (sea-level ref)"
}

#EXIF:GPS Altitude reference values
exif_gps_processingmethod_enum = {
    0: "GPS",
    1: "CELLID",
    2: "WLAN",
    3: "MANUAL"
}

#metadata - default values
metadata_default = {
    'Script:LockTagList'        : False,                        #n/a    : bool                              - lock tag list (only initial tags listed here are allowed to be in final metadata, addition of tags groups which will be stripped before writing to image file are allowed though)
    #Image transformations
    'ImageTransform:Enabled'    : False,                        #n/a    : bool                              - transform is disabled by default, if transform data will be found it will be enabled
    'ImageTransform:Crop'       : [0, 0, 4096, 2656],           #n/a    : int[4]                            - resulting crop area [<origin_left>, <origin_top>, <area_width>, <area_height>]
    'ImageTransform:Rotate'     : 0,                            #n/a    : int {0|±90|±270}                  - rotation angle 
    'ImageTransform:Flip'       : [False, False],               #n/a    : bool[2]                           - flip [<horizontal>, <vertical>]
    'ImageTransform:Compression': ['none', None],               #n/a    : [string, dict]                    - compression (imagecodecs may be required), passed to tifffile.imwrite() as [<compression>, <compressionargs>], check tifffile.COMPRESSION for available options
    #EXIF
    'DocumentName'              : Marker.AUTO,                  #0x010D : string                            - original file name (consists of film ID, frame number, strip number, etc.)
    'ImageDescription'          : "",                           #0x010E : string                            - description of an image (scene or object description, etc.)
    'Make'                      : Marker.MANDATORY,             #0x010F : string                            - camera manufacturer
    'Model'                     : Marker.MANDATORY,             #0x0110 : string                            - camera model
    'Orientation'               : exif_orientation_enum[1],     #0x0112 : string {"<dict>"}                 - image orientation (use value from dictionary)
    'ModifyDate'                : Marker.AUTO,                  #0x0132 : string {"YYYY:MM:DD hh:mm:ss"}    - image file modification date (write datetime of resulting file being created)
    'Artist'                    : "",                           #0x013B : string                            - name of the camera owner, photographer or image creator
    'Copyright'                 : Marker.OPTIONAL,              #0x8298 : string                            - copyright holder
    'ExposureTime'              : Marker.OPTIONAL,              #0x829A : string {"sec." | "1/denom."}      - exposure time of the photo in seconds
    'FNumber'                   : Marker.OPTIONAL,              #0x829D : float                             - aperture F-number
    'ISO'                       : Marker.OPTIONAL,              #0x8827 : int                               - ISO speed rating (film sensitivity)
    'DateTimeOriginal'          : Marker.MANDATORY,             #0x9003 : string {"YYYY:MM:DD hh:mm:ss"}    - original date and time of image being taken (photo was shot)
    'CreateDate'                : Marker.AUTO,                  #0x9004 : string {"YYYY:MM:DD hh:mm:ss"}    - datetime when photo was digitized (film was scanned)
    'OffsetTime'                : Marker.AUTO,                  #0x9010 : string {"±hh:mm"}                 - time zone for ModifyDate
    'OffsetTimeOriginal'        : Marker.OPTIONAL,              #0x9011 : string {"±hh:mm"}                 - time zone for DateTimeOriginal
    'OffsetTimeDigitized'       : Marker.AUTO,                  #0x9012 : string {"±hh:mm"}                 - time zone for CreateDate
    'ShutterSpeedValue'         : Marker.AUTO,                  #0x9201 : string {"sec." | "1/denom."}      - shutter speed value (set in seconds, but stored as an APEX value)
    'ApertureValue'             : Marker.AUTO,                  #0x9202 : float                             - aperture value (set as an F number, but stored as an APEX value)
    'EXIF:Flash'                : Marker.OPTIONAL,              #0x9209 : string {"<dict>"}                 - status of the flash when the image was shot (use value from dictionary)
    'FocalLength'               : Marker.OPTIONAL,              #0x920A : float                             - actual focal length of the lens in mm (NOT converted to 35mm equivalent)
    'ImageNumber'               : Marker.OPTIONAL,              #0x9211 : int                               - image number on a film roll (sequential, not corresponding to marks on the roll, starting from 0 for partial frame and 1 for complete frame)
    'ImageHistory'              : "",                           #0x9213 : string                            - record of edits or operations that the image has undergone since its original capture ('^' indicates position where additional text will be inserted)
    'MakerNotes:All'            : Marker.DELETE,                #0x927C : pointer                           - Manufacturer Notes IFD
    'UserComment'               : "",                           #0x9286 : string                            - user comments to the image without character code limitations of 0x010E
    'ColorSpace'                : Marker.MANDATORY,             #0xA001 : string {"<dict>"}                 - color space of the image (use value from dictionary)
    'ExifImageWidth'            : Marker.AUTO,                  #0xA002 : int                               - image width in EXIF (should be filled by this script)
    'ExifImageHeight'           : Marker.AUTO,                  #0xA003 : int                               - image height in EXIF (should be filled by this script)
    'FileSource'                : exif_filesource_enum[1],      #0xA300 : string {"<dict>"}                 - image source (use value from dictionary)
    'ExposureMode'              : Marker.SKIP,                  #0xA402 : string {"<dict>"}                 - exposure mode (use value from dictionary)
    'WhiteBalance'              : Marker.SKIP,                  #0xA403 : string {"<dict>"}                 - white balance mode (use value from dictionary)
    'FocalLengthIn35mmFormat'   : Marker.OPTIONAL,              #0xA405 : int                               - focal length in mm of the lens CONVERTED to 35mm equivalent
    'OwnerName'                 : Marker.SKIP,                  #0xA430 : string                            - camera owner name
    'SerialNumber'              : Marker.SKIP,                  #0xA431 : string                            - camera body serial number
    'LensInfo'                  : Marker.OPTIONAL,              #0xA432 : float[4]                          - 4 rational values giving focal and aperture ranges, called LensSpecification by the EXIF spec [<ShortEnd_FocalLength>, <LongEnd_FocalLength>, <ShortEnd_Fnumber>, <LongEnd_Fnumber>]
    'LensMake'                  : Marker.OPTIONAL,              #0xA433 : string                            - lens manufacturer
    'LensModel'                 : Marker.OPTIONAL,              #0xA434 : string                            - lens model
    'LensSerialNumber'          : Marker.OPTIONAL,              #0xA435 : string                            - lens serial numner
    'ImageTitle'                : "",                           #0xA436 : string                            - image title/caption
    'Photographer'              : Marker.OPTIONAL,              #0xA437 : string                            - photographer name
    'ImageEditor'               : Marker.OPTIONAL,              #0xA438 : string                            - image editor name
    'ReelName'                  : Marker.OPTIONAL,              #0xC789 : string                            - film reel name/identifier
    #tags that will be added as part of 0x9213 'ImageHistory'
    'ImageHistory:Film'         : Marker.OPTIONAL,              #n/a    : string                            - film type name
    #GPS tags
    'GPSLatitudeRef'            : Marker.AUTO,                  #0x0001 : string {'N' | 'S'}                - GPS latitude reference (North/South)
    'GPSLatitude'               : Marker.OPTIONAL,              #0x0002 : float                             - GPS latitude value
    'GPSLongitudeRef'           : Marker.AUTO,                  #0x0003 : string {'E' | 'W'}                - GPS longitude reference (East/West)
    'GPSLongitude'              : Marker.OPTIONAL,              #0x0004 : float                             - GPS longitude value
    'GPSAltitudeRef'            : Marker.AUTO,                  #0x0005 : string {"<dict>"}                 - GPS altitude reference (use value from dictionary)
    'GPSAltitude'               : Marker.OPTIONAL,              #0x0006 : float                             - GPS altitude value
    'GPSProcessingMethod'       : Marker.AUTO,                  #0x001B : string {"<dict>"}                 - method used to determine location (use value from dictionary)
    #extra tags for internal purposes (will be stripped before writing metadata into a file)
    'Extra:FileID'              : "",                           #n/a    : string                            - file name/identifier
    'Extra:FilePath'            : "",                           #n/a    : string                            - file full path relative to base directory
    'Extra:FileDirectory'       : "",                           #n/a    : string                            - file directory relative to base directory
    'Extra:FileNameBase'        : "",                           #n/a    : string                            - filename without extension
    'Extra:FileNameExtension'   : "",                           #n/a    : string                            - filename extension
    'Extra:FilmID'              : "",                           #n/a    : string                            - film reel name/identifier
    'Extra:FilmFrameNumber'     : 0,                            #n/a    : int                               - frame number on a film roll
    'Extra:StripID'             : "",                           #n/a    : string                            - film strip name/identifier
    'Extra:StripFrameNumber'    : 0,                            #n/a    : int                               - frame number on a film strip
}

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

#Convert string to an int
def str2int(s, negative_prefix = 'm'):
    if not isinstance(s, str):
        raise ValueError("Input must be a string.")
    try:
        if s.startswith(negative_prefix):
            return -int(s[len(negative_prefix):])
        else:
            return int(s)
    except (ValueError, TypeError) as e:
        raise ValueError(f"Cannot convert '{s}' to integer.") from e

#Convert string to a float
def str2float(s, negative_prefix='m', decimal_point = '.'):
    if not isinstance(s, str):
        raise ValueError("Input must be a string.")
    s = s.replace(decimal_point, ".")
    try:
        if s.startswith(negative_prefix):
            return -float(s[len(negative_prefix):])
        else:
            return float(s)
    except (ValueError, TypeError) as e:
        raise ValueError(f"Cannot convert '{s}' to float.") from e

#Delete key with specified prefixes from the dictionary
def delete_keys_with_prefixes(dictionary, prefixes):
    keys_to_delete = [key for key in dictionary if any(key.startswith(prefix) for prefix in prefixes)]
    for key in keys_to_delete:
        del dictionary[key]

#Replace unsafe path characters and trim length
def path_sanitize_variable(key, value, max_length = None):
    #handle exceptions that should not be sanitized
    if key.startswith('Extra:File'): return value 

    if isinstance(value, str):
        #sanitize strings
        result = re.sub(r'[<>:"/\\|?*\x00-\x1F]', '_', value.strip())
        if max_length is not None: value = value[:max_length]
    elif value is None: 
        #sanitize None
        result = str(value)
    elif isinstance(value, (list, tuple)):
        #sanitize elements of lists and tuples
        result = [path_sanitize_variable(key, v, max_length = max_length) if isinstance(v, str) else v for v in value]
    elif hasattr(value, '__dict__'):
        #handle objects with __dict__ attribute (custom objects)
        result = str(value)  #convert to string representation
        result = path_sanitize_variable(key, result, max_length=max_length)  #sanitize that string
    else:
        #pass other types as is
        result = value
    return result

#Build a safe path using a template and metadata dictionary
def path_build(path, metadata, max_total_length = None, max_value_length = None, missing_value='UNDEF'):
    #sanitize and optionally truncate individual values
    metadata_sanitized = {
        k.replace(':', "_cln_"): path_sanitize_variable(k, v, max_length = max_value_length)
        for k, v in metadata.items()
    }
    metadata_sanitized = SafeDict(metadata_sanitized, missing_value = missing_value)
    #change special characters to match fstrings syntax
    #--- replace all ':' with substitute except one in drive letter 
    path = re.sub(r'([A-Za-z]):(?=[\\/])', r'\1__DRIVELETTERCOLON__', str(path))
    path = path.replace(':', "_cln_")
    path = path.replace('__DRIVELETTERCOLON__', ":")
    #--- replace formatting delimiter
    path = path.replace('?', ":")
    #generate filename with safe substitution
    path = path.format_map(metadata_sanitized)
    #trim total length if needed
    if max_total_length is not None and len(path) > max_total_length:
        #optionally preserve file extension
        if os.path.extsep in path:
            name, ext = path.rsplit(os.path.extsep, 1)
            name = name[:max_total_length - len(ext) - 1]
            path = name + os.path.extsep + ext
        else:
            path = path[:max_total_length]
    return path

#Check if tag value can be written (updated)
def tag_iswritable(tag_name, metadata):
    if tag_name in metadata:
        if metadata[tag_name] != Marker.SKIP and metadata[tag_name] != Marker.DELETE:
            return True
    return False

#Check if flash was fired
def exif_flash_fired(exif_flash_value):
    if isinstance(exif_flash_value, int):
        if not exif_flash_value in exif_flash_enum:
            raise ValueError(f"Unknown EXIF Flash value '{exif_flash_value}'.")
    elif isinstance(exif_flash_value, str):
        for key, value in exif_flash_enum.items():
            if value == exif_flash_value:
                exif_flash_value = key
                break
        else:
            raise ValueError(f"Unknown EXIF Flash value '{exif_flash_value}'.")
    else:
        raise ValueError(f"Invalid EXIF Flash value type '{exif_flash_value}'.")
    if exif_flash_value in exif_flash_enum_fired: return True
    if exif_flash_value in exif_flash_enum_notfired: return False
    if exif_flash_value in exif_flash_enum_notpresent: return False

#Format flat dictionary with nested key groups into a string
def format_nested_dict(flat_dict,
                       format_entry_prefix='\n',
                       format_entry_postfix=';',
                       format_value_delimiter=': ',
                       format_block_prefix='{',
                       format_block_postfix='}',
                       format_block_indent='    '):

    def nest_keys(d):
        """Converts flat dict with colon-separated keys into nested dict."""
        nested = {}
        for key, value in d.items():
            parts = key.split(':')
            cur = nested
            for part in parts[:-1]:
                cur = cur.setdefault(part, {})
            cur[parts[-1]] = value
        return nested

    def render(d, indent=''):
        lines = []
        for key, value in d.items():
            if isinstance(value, dict):
                lines.append(f"{indent}{key}{format_value_delimiter}{format_block_prefix}")
                lines.append(render(value, indent + format_block_indent))
                lines.append(f"{indent}{format_block_postfix}{format_entry_postfix}")
            else:
                lines.append(f"{indent}{key}{format_value_delimiter}{value}{format_entry_postfix}")
        return format_entry_prefix.join(lines)

    nested = nest_keys(flat_dict)
    return render(nested)

#Transform the image
def image_transform(image, crop_left, crop_top, crop_width, crop_height, rotate_cw = None, flip_horizontal = None, flip_vertical = None):
    #if width or height is 0 -> don't crop in that direction
    if crop_width  == 0: crop_width  = image.shape[1]
    if crop_height == 0: crop_height = image.shape[0]
    
    #size check
    if crop_width <= 0 or crop_height <= 0:
        raise ValueError("Error! Crop region size is invalid.")
    if crop_left < 0 or crop_top < 0 or image.shape[0] < crop_top + crop_height or image.shape[1] < crop_left + crop_width:
        raise ValueError("Error! Crop region is outside image boundaries.")
    elif image.shape[0] == crop_top + crop_height and image.shape[1] == crop_left + crop_width:
        print("Nothing to change.")

    #crop
    image_result = image[crop_top:crop_top + crop_height, crop_left:crop_left + crop_width]

    #rotate
    if rotate_cw:
        if rotate_cw % 90 != 0:
            raise ValueError("Error! Rotate can only be performed by multiple of 90 degrees.")
        k = (-rotate_cw // 90) % 4
        if k != 0:
            image_result = np.rot90(image_result, k=k)

    #flip
    if flip_horizontal: image_result = np.fliplr(image_result)
    if flip_vertical:   image_result = np.flipud(image_result)

    return image_result

#Get metadata from scanner
def metadata_get_scanner(file_path):
    result = {}
    with exiftool.ExifToolHelper(executable = exiftool_exe) as exif:
        #Scanner model from 0x0110 'Model' and software from 0x0131 'Software'
        output = exif.get_tags(file_path, ['Model', 'Software'])[0]
        if 'EXIF:Model' in output: result['Scanner:Model'] = output['EXIF:Model']
        if 'EXIF:Software' in output: result['Scanner:Software:Name'] = output['EXIF:Software']
        
        if 'Scanner:Model' in result and 'Scanner:Software:Name' in result:
            if "nikon" in result['Scanner:Model'].lower() and 'nikon' in result['Scanner:Software:Name'].lower():
                #NikonScanIFD from Nikon MakerNotes
                output = exif.get_tags(file_path, 'NikonScan:all')[0]
                for tag_name, value in output.items():
                    if tag_name == 'SourceFile': continue  #skip this key entirely
                    if tag_name.startswith('MakerNotes:'): tag_name = tag_name[len('MakerNotes:'):]  #remove prefix
                    result['Scanner:Software:' + tag_name] = value

                #fix NikonScan bug for negative gain values (negative values higher than they set in GUI by 0.01 )
                if "Nikon Scan" in result['Scanner:Software:Name']:
                    if 'Scanner:Software:MasterGain' in result:
                        value = result['Scanner:Software:MasterGain']
                        if value < 0: value -= 0.01
                        result['Scanner:Software:MasterGain'] = format(value, "g")
                    if 'Scanner:Software:ColorGain' in result:
                        try:
                            color_gain = [float(part) for part in result['Scanner:Software:ColorGain'].split()]
                            result['Scanner:Software:ColorGain'] = ""
                            for value in color_gain:
                                if value < 0: value -= 0.01
                                result['Scanner:Software:ColorGain'] += format(value, "g") + ', '
                            result['Scanner:Software:ColorGain'] = result['Scanner:Software:ColorGain'].strip(', ')
                        except ValueError:
                            raise ValueError("Error! Can't parse NikonScan:ColorGain value.")        

                #insert AutoExposure parameter (equals True by default)
                if "Nikon Scan" in result['Scanner:Software:Name']:
                    if 'Scanner:Software:MasterGain' in result:
                        tmp = {}
                        for tag_name, value in result.items():
                            if tag_name == 'Scanner:Software:MasterGain': tmp['Scanner:Software:AutoExposure'] = True
                            tmp[tag_name] = value
                        result = tmp
    return result

#Get metadata from a file
def metadata_get_file(file_path, strip_whitespace = True, encoding = None):
    if encoding is None: encoding = locale.getpreferredencoding(False)
    try:
        result = {}
        with open(file_path, 'r', encoding = encoding) as f:
            for line in f:
                if len(line) > 0:                                       #skip empty lines
                    if line.lstrip().startswith(('#', ';')): continue   #skip comments
                    if '=' not in line: continue                        #skip lines that are not key=value pairs
                    key, value = line.split('=', 1)
                    if strip_whitespace:
                        key = key.strip()
                        value = value.strip()
                    for marker in Marker:
                        if value == marker.value:
                            result[key] = marker
                            continue
                    try:
                        result[key] = ast.literal_eval(value)
                    except (ValueError, SyntaxError):
                        result[key] = value
        #enable ImageTransform if there are tags from that group and enable value is not set explicitly
        if not 'ImageTransform:Enabled' in result:
            for tag_name in result:
                if tag_name.startswith('ImageTransform:'):
                    result['ImageTransform:Enabled'] = True
                    break
        
        return result
    except Exception as e:
        print(f"Error! Can't load metadata from file '{file_path}': {e}")
        return None

#Get metadata from paths
def metadata_get_path(input_path: Path, base_dir: Path):
    #list of variables
    #--- filename
    file_id                   = None        #file identifier
    file_name, file_ext       = None, None  #file basename and extension
    file_relpath, file_reldir = None, None  #file path and directory relative to base directory
    #--- identifiers
    film_id, film_frame     = None, None    #film identifier and frame number in film
    strip_id, strip_frame   = None, None    #film strip identifier and frame number on that strip
    image_number            = None          #image number
    #--- image transformations
    crop                    = None          #image crop
    rotate, flip            = None, None    #image rotation and flip
    compression             = None          #image compression
    #--- camera settings
    exposure_time           = None          #exposure time
    aperture                = None          #aperture
    iso                     = None          #iso
    flash                   = None          #flash
    orientation             = None          #orientation
    lens_focal_length       = None          #lens focal length
    #--- environment
    camera_maker, camera_model                   = None, None           #camera model and maker
    datetime_original, datetime_original_offset  = None, None           #datetime of original image being taken + timezone offset
    gnss_latitude, gnss_longitude, gnss_altitude = None, None, None     #GNSS coordinates and altitude
    #--- description
    image_description       = None          #image description
    image_title             = None          #image title
    user_comment            = None          #user comment

    #set path variables
    file_name    = input_path.stem
    file_ext     = input_path.suffix.replace(os.path.extsep, '')
    file_relpath = input_path.relative_to(base_dir)
    file_reldir  = file_relpath.parent

    #split original basename and metadata
    if file_name.find('__') < 0:
        #no metadata
        file_id = file_name
        metadata = ""
    else:
        #metadata present
        basename_metadata = file_name.rsplit('__', 1)
        if len(basename_metadata[0]) > 0: file_id = basename_metadata[0]
        metadata = basename_metadata[1]

    #get metadata from input filename
    metadata = metadata.split('_')
    for entry in metadata:
    #film identifier and frame number in film: "F<FILM_ID>[-<FRAME_NUM_ON_FILM>]"
        if entry.startswith('F'):
            entry_value = entry[1:]
            film_id_frame = entry_value.rsplit("-", 1)
            film_id = film_id_frame[0]
            if len(film_id_frame) > 1:
                if film_id_frame[1]: film_frame = str2int(film_id_frame[1])
                else: raise ValueError("Error! Frame number on film value not specified.")
            continue
        #film strip identifier and frame number on that strip: "S<STRIP_ID>[-<FRAME_NUM_ON_STRIP>]"
        if entry.startswith('S'):
            entry_value = entry[1:]
            strip_id_frame = entry_value.rsplit("-", 1)
            strip_id = strip_id_frame[0]
            if len(strip_id_frame) > 1:
                if strip_id_frame[1]: strip_frame = str2int(strip_id_frame[1])
                else: raise ValueError("Error! Frame number on strip value not specified.")
            continue
        #image number: "N<IMAGE_NUMBER>"
        if entry.startswith('N'):
            entry_value = entry[1:]
            if entry_value: image_number = str2int(entry_value)
            else: raise ValueError("Error! Image number value not specified.")
            continue
        #image crop: "C<LEFT>[-<TOP>[-<WIDTH>[-<HEIGHT>]]]"
        if entry.startswith('C'):
            entry_value = entry[1:]
            if entry_value:
                crop = entry_value.split("-")
                for i, value in enumerate(crop):
                    crop[i] = str2int(crop[i])
                for value in crop:
                    if value < 0: raise ValueError("Error! Crop values can't be negative.")
            else:
                raise ValueError("Error! Crop value not specified.")
            continue
        #image rotation and flip: "R<ROTATION_CW{ANGLE|90CW|90CCW}>[<FLIP{H|V}>]"
        if entry.startswith('R'):
            entry_value = entry[1:]
            rotate = 0
            flip = [False, False]
            if 'H' in entry_value:
                flip[0] = True
                entry_value = entry_value.replace('H', '')
            if 'V' in entry_value:
                flip[1] = True
                entry_value = entry_value.replace('V', '')
            if   entry_value == "90CW":  rotate = 90
            elif entry_value == "90CCW": rotate = 270
            else:
                rotate = str2int(entry_value)
            continue
        #image compression: "Z<COMPRESSION_ID>"
        if entry.startswith('Z'):
            entry_value = entry[1:]
            if entry_value: compression = entry_value
            else: raise ValueError("Error! Comperession identifier not specified.")
            continue
        #exposure time: "T<EXPOSURE_TIME{<TIME_IN_SECONDS>|'<DENOMINATOR>}>"
        if entry.startswith('T'):
            entry_value = entry[1:]
            if entry_value:
                if entry_value.startswith("'"):
                    #value as fraction denominator (1/x)
                    exposure_time = -str2int(entry_value[1:])
                else:
                    #value in seconds
                    exposure_time = str2float(entry_value)
            else:
                 raise ValueError("Error! Exposure time value not specified.")
            continue
        #aperture: "A<F-NUMBER>"
        if entry.startswith('A'):
            entry_value = entry[1:]
            if entry_value: aperture = str2float(entry_value)
            else: raise ValueError("Error! Aperture value not specified.")
            continue
        #ISO: "I<ISO_VALUE>"
        if entry.startswith('I'):
            entry_value = entry[1:]
            if entry_value: iso = str2int(entry_value)
            else: raise ValueError("Error! ISO value not specified.")
            continue
        #flash: "X<EXIF_FLASH_VALUE_NUMBER>"
        if entry.startswith('X'):
            entry_value = entry[1:]
            if entry_value: flash = str2int(entry_value)
            else: raise ValueError("Error! Flash value not specified.")
            continue
        #orientation: "O<VALUE{<CODE>|90CW|90CCW|180}>"
        if entry.startswith('O'):
            entry_value = entry[1:]
            if entry_value:
                if   entry_value == "90CW":  orientation = 6
                elif entry_value == "90CCW": orientation = 8
                elif entry_value == "180":   orientation = 3
                else:
                    orientation = str2int(entry_value)
                if not (1 <= orientation <= 8):
                    raise ValueError("Error! Invalid orientation value.")
            else: raise ValueError("Error! Orientation value not specified.")
            continue
        #lens focal length: "L<FOCAL_LENGTH>"
        if entry.startswith('L'):
            entry_value = entry[1:]
            if entry_value: lens_focal_length = str2int(entry_value)
            else: raise ValueError("Error! Lens focal length value not specified.")
            continue
        #camera model and maker: "M[<MODEL>][@<MAKER>]"
        if entry.startswith('M'):
            entry_value = entry[1:]
            camera = entry_value.split("@", 1)
            if camera[0]: camera_model = camera[0]
            if len(camera) > 1 and camera[1]: camera_maker = camera[1]
            continue
        #datetime of original image being taken + timezone offset: "D<YYYY>[-<MM>[-<DD>[-<hh>[-<mm>[-<ss>[@<tzo_hh>[-<tzo_mm>]]]]]]]"
        if entry.startswith('D'):
            entry_value = entry[1:]
            if entry_value:
                tmp = entry_value.split("@", 1)
                datetime_original = tmp[0].strip()
                if len(tmp) > 1 and tmp[1].strip(): datetime_original_offset = tmp[1].strip()
                if datetime_original:
                    #datetime
                    datetime_original = datetime_original.split("-")
                    for i, value in enumerate(datetime_original):
                        datetime_original[i] = str2int(datetime_original[i])
                    if len(datetime_original) < 6: datetime_original += [0] * (6 - len(datetime_original))
                    #--- check values (to remain correct size)
                    if not (0 <= datetime_original[0] <= 9999): raise ValueError("Error! Datetime year must be 4 digits long.")
                    for value in datetime_original[1:]:
                        if not (0 <= value <= 99): raise ValueError("Error! Datetime component value (except year) must be 2 digits long.")           
                #timezone offset
                if datetime_original_offset is not None:
                    datetime_original_offset = datetime_original_offset.split("-", 1)
                    for i, value in enumerate(datetime_original_offset):
                        datetime_original_offset[i] = str2int(datetime_original_offset[i])
                    if len(datetime_original_offset) < 2: datetime_original_offset += [0] * (2 - len(datetime_original_offset))
                    #check values
                    if not (-24 <= datetime_original_offset[0] <= 24): raise ValueError("Error! Datetime offset hours must within ±24.")
                    if not (0 <= datetime_original_offset[1] <= 59): raise ValueError("Error! Datetime offset minutes must be between 0 and 59.")
            else:
                raise ValueError("Error! Datetime value not specified.")
            continue
        #GNSS coordinates and altitude: "G<{+|-|N|S}LATITUDE_DEG>,<{+|-|E|W}LONGITUDE_DEG>[,<ALTITUDE_M>]"
        if entry.startswith('G'):
            entry_value = entry[1:].replace(' ', '')
            gnss_location = entry_value.split(",")
            if (2 <= len(gnss_location) <= 3):
                gnss_sign = 1
                if gnss_location[0].startswith('N'):
                    gnss_location[0] = gnss_location[0][1:]
                    gnss_sign = 1
                elif gnss_location[0].startswith('S'):
                    gnss_location[0] = gnss_location[0][1:]
                    gnss_sign = -1
                gnss_latitude = gnss_sign * str2float(gnss_location[0])
                gnss_sign = 1
                if gnss_location[1].startswith('E'):
                    gnss_location[1] = gnss_location[1][1:]
                    gnss_sign = 1
                elif gnss_location[1].startswith('W'):
                    gnss_location[1] = gnss_location[1][1:]
                    gnss_sign = -1
                gnss_longitude = gnss_sign * str2float(gnss_location[1])
                if len(gnss_location) > 2:
                    gnss_altitude = str2float(gnss_location[2])
            else:
                raise ValueError("Error! GPS location can't be parsed.")
            continue
        #image title "H<IMAGE_TITLE>" (use &#95; if you need underscore in a value)
        if entry.startswith('H'):
            image_title = entry[1:].replace('&#95;', '_')
            continue
        #image description "N<IMAGE_DESCRIPTION>" (use &#95; if you need underscore in a value)
        if entry.startswith('N'):
            image_description = entry[1:].replace('&#95;', '_')
            continue
        #user comment: "U<USER_COMMENT>" (use &#95; if you need underscore in a value)
        if entry.startswith('U'):
            user_comment = entry[1:].replace('&#95;', '_')
            continue

    result = {}
    #--- filename
    if file_id is not None: result['Extra:FileID'] = file_id
    if file_name is not None: result['Extra:FileNameBase'] = file_name
    if file_ext is not None: result['Extra:FileNameExtension'] = file_ext
    if file_relpath is not None: result['Extra:FilePath'] = str(file_relpath)
    if file_reldir is not None: result['Extra:FileDirectory'] = str(file_reldir)
    #--- identifiers
    if film_id is not None:
        result['ReelName'] = film_id
        result['Extra:FilmID'] = film_id
    if film_frame is not None:
        result['ImageNumber'] = film_frame
        result['Extra:FilmFrameNumber'] = film_frame
    if strip_id is not None: result['Extra:StripID'] = strip_id
    if strip_frame is not None: result['Extra:StripFrameNumber'] = strip_frame
    if image_number is not None: result['ImageNumber'] = image_number
    #--- image transformations
    if crop is not None:
        result['ImageTransform:Crop'] = crop
        result['ImageTransform:Enabled'] = True
    if rotate is not None:
        result['ImageTransform:Rotate'] = rotate
        result['ImageTransform:Enabled'] = True
    if flip is not None:
        result['ImageTransform:Flip'] = flip
        result['ImageTransform:Enabled'] = True
    if compression is not None:
        result['ImageTransform:Compression'] = compression
        result['ImageTransform:Enabled'] = True
    #--- camera settings
    if exposure_time is not None:
        if exposure_time >= 0: result['ExposureTime'] = exposure_time
        else:                  result['ExposureTime'] = f"1/{-exposure_time}"
    if aperture is not None: result['FNumber'] = aperture
    if iso is not None: result['ISO'] = iso
    if flash is not None: result['EXIF:Flash'] = exif_flash_enum[flash]
    if orientation is not None: result['Orientation'] = exif_orientation_enum[orientation]
    if lens_focal_length is not None: result['FocalLength'] = lens_focal_length
    #--- environment
    if camera_maker is not None: result['Make'] = camera_maker
    if camera_model is not None: result['Model'] = camera_model
    if datetime_original is not None: result['DateTimeOriginal'] = f"{datetime_original[0]:04d}:{datetime_original[1]:02d}:{datetime_original[2]:02d} {datetime_original[3]:02d}:{datetime_original[4]:02d}:{datetime_original[5]:02d}"
    if datetime_original_offset is not None: result['OffsetTimeOriginal'] = f"{datetime_original_offset[0]:+03d}:{datetime_original_offset[1]:02d}"
    if gnss_latitude is not None: result['GPSLatitude'] = gnss_latitude
    if gnss_longitude is not None: result['GPSLongitude'] = gnss_longitude
    if gnss_altitude is not None: result['GPSAltitude'] = gnss_altitude
    #--- description
    if image_description is not None: result['ImageDescription'] = image_description
    if image_title is not None: result['ImageTitle'] = image_title
    if user_comment is not None: result['UserComment'] = user_comment

    return result

#Update existing metadata
def metadata_update(base, update, allow_new_tags = True):
    for key, value_new in update.items():
        if key in base:
            if tag_iswritable(key, base):
                if isinstance(value_new, (list, tuple)) and isinstance(base[key], (list, tuple)):
                    #both values are arrays -> update their elements positionaly
                    value_old = list(base[key])
                    for i in range(len(value_new)):
                        if i < len(value_old):
                            value_old[i] = value_new[i]
                        else:
                            value_old.append(value_new[i])
                    base[key] = type(base[key])(value_old)  #preserve original type
                else:
                    #update value by simple overwrite
                    base[key] = value_new
        elif allow_new_tags or key.startswith('Extra:'):
            base[key] = value_new

#Fill metadata tags with values set to AUTO with actual data
def metadata_autofill(file_path, metadata):
    if metadata.get('DocumentName') == Marker.AUTO:
        film_id     = metadata.get('Extra:FilmID')
        film_frame  = metadata.get('Extra:FilmFrameNumber')
        strip_id    = metadata.get('Extra:StripID')
        strip_frame = metadata.get('Extra:StripFrameNumber')
        if film_id is not None and film_frame is not None and strip_id is not None and strip_frame is not None:
            metadata['DocumentName'] = f"{film_id}-{film_frame:02d}_S{strip_id}-{strip_frame}"
        else:
            metadata['DocumentName'] = ""
            print("Warning! Can't assign 'DocumentName', not enough data.")

    if metadata.get('ModifyDate') == Marker.AUTO:
        now = datetime.now().astimezone()
        metadata['ModifyDate'] = now.strftime("%Y:%m:%d %H:%M:%S")
        if tag_iswritable('OffsetTime', metadata) and metadata['OffsetTime'] == Marker.AUTO:
            now_offset = now.strftime("%z")
            metadata['OffsetTime'] = now_offset[:3] + ":" + now_offset[3:]

    if metadata.get('ShutterSpeedValue') == Marker.AUTO:
        metadata['ShutterSpeedValue'] = metadata.get('ExposureTime', Marker.SKIP)

    if metadata.get('ApertureValue') == Marker.AUTO:
        metadata['ApertureValue'] = metadata.get('FNumber', Marker.SKIP)

    if metadata.get('ExifImageWidth') == Marker.AUTO:
        with tifffile.TiffFile(file_path) as tif:
            page = tif.pages[0]
            metadata['ExifImageWidth'] = page.imagewidth

    if metadata.get('ExifImageHeight') == Marker.AUTO:
        with tifffile.TiffFile(file_path) as tif:
            page = tif.pages[0]
            metadata['ExifImageHeight'] = page.imagelength

    if metadata.get('CreateDate') == Marker.AUTO:
        with exiftool.ExifToolHelper(executable = exiftool_exe) as exif:
            modify_date = exif.get_tags(file_path, 'ModifyDate')[0]['EXIF:ModifyDate']
            metadata['CreateDate'] = modify_date.replace('.', ':')
        if metadata.get('OffsetTimeDigitized') == Marker.AUTO:
            try:
                dt = datetime.strptime(metadata['CreateDate'], "%Y:%m:%d %H:%M:%S")
                local_zone = ZoneInfo(get_localzone_name())
                dtz = dt.replace(tzinfo = local_zone)
                dtz_offset = dtz.strftime("%z")
                metadata['OffsetTimeDigitized'] = dtz_offset[:3] + ":" + dtz_offset[3:]
            except ValueError:
                pass
    if metadata.get('GPSLatitudeRef') == Marker.AUTO:
        gps_latitude = metadata.get('GPSLatitude')
        if not isinstance(gps_latitude, Marker):
            if isinstance(gps_latitude, str):
                if gps_latitude.startswith('N') or gps_latitude.startswith('S'):
                    metadata['GPSLatitudeRef'] = gps_latitude[:1]
                    metadata['GPSLatitude'] = str2float(gps_latitude[1:])
                else:
                    raise ValueError("Error! GPS latitude can't be parsed.")
            elif isinstance(gps_latitude, float):
                if gps_latitude >= 0:
                    metadata['GPSLatitudeRef'] = 'N'
                else:
                    metadata['GPSLatitudeRef'] = 'S'
                    metadata['GPSLatitude'] = -metadata['GPSLatitude']
            else:
                raise ValueError("Error! GPS latitude can't be processed.")
        else:
            metadata['GPSLatitudeRef'] = Marker.SKIP

    if metadata.get('GPSLongitudeRef') == Marker.AUTO:
        gps_longitude = metadata.get('GPSLongitude')
        if not isinstance(gps_longitude, Marker):
            if isinstance(gps_longitude, str):
                if gps_longitude.startswith('E') or gps_longitude.startswith('W'):
                    metadata['GPSLongitudeRef'] = gps_longitude[:1]
                    metadata['GPSLongitude'] = str2float(gps_longitude[1:])
                else:
                    raise ValueError("Error! GPS longitude can't be parsed.")
            elif isinstance(gps_longitude, (float, int)):
                if gps_longitude >= 0:
                    metadata['GPSLongitudeRef'] = 'E'
                else:
                    metadata['GPSLongitudeRef'] = 'W'
                    metadata['GPSLongitude'] = -metadata['GPSLongitude']
            else:
                raise ValueError("Error! GPS longitude can't be processed.")
        else:
            metadata['GPSLongitudeRef'] = Marker.SKIP

    if metadata.get('GPSAltitudeRef') == Marker.AUTO:
        gps_altitude = metadata.get('GPSAltitude')
        if not isinstance(gps_altitude, Marker):
            if isinstance(gps_altitude, (float, int)):
                if gps_altitude >= 0:
                    metadata['GPSAltitudeRef'] = exif_gps_altituderef_enum[0]
                else:
                    metadata['GPSAltitudeRef'] = exif_gps_altituderef_enum[1]
                    metadata['GPSAltitude'] = -metadata['GPSAltitude']
            else:
                raise ValueError("Error! GPS altitude can't be processed.")
        else:
            metadata['GPSAltitudeRef'] = Marker.SKIP

    if metadata.get('GPSProcessingMethod') == Marker.AUTO:
        if not isinstance(metadata.get('GPSLatitude', Marker.SKIP), Marker) and not isinstance(metadata.get('GPSLongitude', Marker.SKIP), Marker):
            metadata['GPSProcessingMethod'] = exif_gps_processingmethod_enum[3]
        else:
            metadata['GPSProcessingMethod'] = Marker.SKIP

#Update value of 0x9213 'ImageHistory' tag
def metadata_update_imagehistory(metadata):
    image_history = metadata['ImageHistory'].split('^', 2)
    if len(image_history) == 1: image_history.append("")

    #extract ImageHistory group tags into separate dictionary
    image_history_dict = {}
    for tag_name, tag_value in metadata.items():
        if tag_name.startswith('ImageHistory:'):
            if not isinstance(tag_value, Marker):
                image_history_dict[tag_name[len('ImageHistory:'):]] = tag_value
    
    #add Scanner group to it
    for tag_name, tag_value in metadata.items():
        if tag_name.startswith('Scanner:'):
            if not isinstance(tag_value, Marker):
                image_history_dict[tag_name] = tag_value

    metadata['ImageHistory'] = image_history[0] + format_nested_dict(image_history_dict) + image_history[1]

#Update metadata values based on conditions
def metadata_update_conditional(metadata):
    if metadata.get('Make', None) == "Panasonic" and (metadata.get('Model', None) == 'C-D325EF' or metadata.get('Model', None) == 'C-325EF'):
        #Camera = Panasonic C-(D)325EF
        #flash built-in automatic (set as "Auto, Did not fire" by default)
        if tag_iswritable('EXIF:Flash', metadata) and isinstance(metadata['EXIF:Flash'], Marker):
            metadata['EXIF:Flash'] = exif_flash_enum[24]

        #exposure time is fixed to 1/130 seconds
        exposure_time = "1/130"
        if tag_iswritable('ExposureTime', metadata): metadata['ExposureTime'] = exposure_time
        if tag_iswritable('ShutterSpeedValue', metadata): metadata['ShutterSpeedValue'] = exposure_time

        #if flash is fired then aperture F-number is 5.6, otherwise it's 9.0
        aperture_fnumber = 9.0
        if 'EXIF:Flash' in metadata and exif_flash_fired(metadata['EXIF:Flash']): aperture_fnumber = 5.6
        if tag_iswritable('FNumber', metadata): metadata['FNumber'] = aperture_fnumber
        if tag_iswritable('ApertureValue', metadata): metadata['ApertureValue'] = aperture_fnumber

        #focal length is fixed to 34mm
        focal_length = 34.0
        if tag_iswritable('FocalLength', metadata): metadata['FocalLength'] = focal_length
        if tag_iswritable('FocalLengthIn35mmFormat', metadata): metadata['FocalLengthIn35mmFormat'] = focal_length
        
        #lens is built-in
        if tag_iswritable('LensInfo', metadata): metadata['LensInfo'] = [34.0, 34.0, 5.6, 5.6]
        if tag_iswritable('LensMake', metadata): metadata['LensMake'] = "Panasonic"
        if tag_iswritable('LensModel', metadata): metadata['LensModel'] = "Built-in, fixed-focus prime lens (1.3m-inf.)"
        
#Process image file
def process_file(input_path: Path, output_path: Path, metadata: dict, temp_dir: Path = None):
    #temporary file path
    if temp_dir is None:
        temp_path = output_path.resolve()
        while not temp_path.exists():
            temp_path = temp_path.parent
            if temp_path == temp_path.parent:
                raise FileNotFoundError("No part of the output path exists.")
        temp_path = temp_path / input_path.with_suffix('.tmp').name
    elif temp_dir.is_dir():
        temp_path = temp_dir / input_path.with_suffix('.tmp').name
    else:
        raise FileNotFoundError("Can't work with temp directory.")

    #get metadata about scanner from input file
    metadata_scanner = metadata_get_scanner(input_path)

    #update metadata
    metadata_update(metadata, metadata_scanner, True)           #inject scanner metadata regardless of tag list lock state
    if tag_iswritable('ImageHistory', metadata): metadata_update_imagehistory(metadata)
    metadata_update_conditional(metadata)

    #apply transformations of the image
    if metadata.pop('ImageTransform:Enabled', False):
        #image tramsformations are needed, perform them and save result to a new file
        #get transform parameters
        image_transform_crop        = metadata.pop('ImageTransform:Crop', [0, 0, 0, 0])
        image_transform_rotate      = metadata.pop('ImageTransform:Rotate', 0)
        image_transform_flip        = metadata.pop('ImageTransform:Flip', [False, False])
        image_transform_compression = metadata.pop('ImageTransform:Compression', ['none', None])
        if isinstance(image_transform_compression, (list, tuple)) and len(image_transform_compression) > 1:
            image_transform_compressionargs = image_transform_compression[1]
            image_transform_compression = image_transform_compression[0]
        else:
            image_transform_compressionargs = None

        #perform transformations
        image = tifffile.imread(input_path)
        image = image_transform(image, image_transform_crop[0], image_transform_crop[1], image_transform_crop[2], image_transform_crop[3], image_transform_rotate, image_transform_flip[0], image_transform_flip[1])
        os.makedirs(os.path.dirname(temp_path), exist_ok = True)
        tifffile.imwrite(temp_path, image, photometric='rgb', compression=image_transform_compression, compressionargs=image_transform_compressionargs)

        #restore original metadata in a new image file
        with exiftool.ExifTool(executable = exiftool_exe) as exif:
            icc_profile = exif.execute(*["-icc_profile", "-b", str(input_path)], raw_bytes = True)  #extract ICC profile from original image file
            icc_file = temp_path.with_suffix(os.path.extsep + 'icc')
            with open(icc_file, 'wb') as f: f.write(icc_profile)                                    #save the ICC profile to a temporary file
            args = ['-TagsFromFile', str(input_path), '-All:All']
            if len(exif.execute('-ImageDescription', str(input_path))) == 0:        args.append('-ImageDescription=')
            if len(exif.execute('-ComponentsConfiguration', str(input_path))) == 0: args.append('-ComponentsConfiguration=')
            args.extend(['-overwrite_original', str(temp_path)])
            exif.execute(*args)
            exif.execute('-overwrite_original', '-icc_profile<='+ str(icc_file), str(temp_path))    #inject ICC profile from profile temporary file into image temporary file
            os.remove(icc_file)
    else:
        #image tramsformations are not needed, simply copy image file to a new location
        os.makedirs(temp_path.parent, exist_ok = True)
        shutil.copy(input_path, temp_path)
    
    #autofill EXIF data values
    metadata_autofill(temp_path, metadata)

    #resolve actual output path and move file there
    output_path = path_build(output_path, metadata)
    output_path = os.path.join(os.path.dirname(input_path), output_path)
    os.makedirs(os.path.dirname(output_path), exist_ok = True)
    shutil.move(temp_path, output_path)

    #remove all extra tags from metadata
    delete_keys_with_prefixes(metadata, ['ImageTransform:', 'Script:', 'Scanner:', 'ImageHistory:', 'Extra:'])

    #write EXIF data to image file
    args = ['-E', '-overwrite_original']
    for tag_name, tag_value in metadata.items():
        if tag_value == Marker.DELETE:
            args.append(f"-{tag_name}=")
            continue
        elif tag_value == Marker.SKIP or tag_value == Marker.OPTIONAL:
            continue
        elif tag_value == Marker.AUTO:
            print(f"Warning! '{tag_name}' = <AUTO> after autofill already passed.")
        elif tag_value == Marker.MANDATORY:
            raise ValueError(f"Error! Mandatory tag '{tag_name}' value not assigned.")

        if isinstance(tag_value, (list, tuple)):
            tmp_str = ""
            for item in tag_value: tmp_str += str(item) + ' '
            tag_value = tmp_str.strip()
        if isinstance(tag_value, str):
            tag_value = tag_value.replace('\n', '&#xd;&#xa;')

        if tag_value == "":
            #handle assignation of empty values
            args.append(f"-{tag_name}^=")
            continue
        if tag_name == 'DateTimeOriginal' or tag_name == 'ModifyDate' or tag_name == 'CreateDate':
            #allow syntactically correct but semantically invalid values to datetime tags
            datetime_regex = re.compile(r"^\d{4}:\d{2}:\d{2} \d{2}:\d{2}:\d{2}$")
            if datetime_regex.match(tag_value):
                try:
                    datetime.strptime(tag_value, "%Y:%m:%d %H:%M:%S")
                except ValueError:
                    #format is right but values are invalid -> using assignation of raw data
                    args.append(f'-{tag_name}#={tag_value}')
                    continue
        #regular tag value assignation
        args.append(f'-{tag_name}={tag_value}')
    args.append(output_path)
    
    command = [exiftool_exe]
    command.extend(args)
    subprocess.run(command, encoding = "utf-8", check = True)
    return output_path

def main():
    #parse call arguments
    parser = argparse.ArgumentParser(description=f"EXIF-writer - a tool for scanned images, v.{_module_date:%Y-%m-%d} by {_module_designer}.")
    parser.add_argument("base_dir", type=Path, help="Base directory of image files.")
    parser.add_argument("output_path", type=Path, help="Output files path (use template). If set to existing directory copies the structure of base directory.")
    parser.add_argument("--tempdir", type=Path, default=None, metavar="<dir>", help="Directory to store temporary files [default: deepest already existing directory in output path before template variable resolution].")
    parser.add_argument("--exiftool", type=Path, default=None, metavar="<file>", help="Path to exiftool.")
    parser.add_argument("--dirdepth", type=int, default=-1, metavar="<int>", help="Max directory depth (-1 for no limit) [default: -1].")
    parser.add_argument("--metafile", type=Path, default=Path("metadata.txt"), metavar="<file>", help="Metadata file name [default: 'metadata.txt'].")
    parser.add_argument("--wildcards", type=str, default="*.tif,*.tiff", metavar="<str>", help="Comma-separated list of file patterns [default: '*.tif,*.tiff'].")
    args = parser.parse_args()

    wildcards = [w.strip() for w in args.wildcards.split(",")]
    base_dir = args.base_dir.resolve()
    output_path = args.output_path.resolve()
    temp_dir = args.tempdir
    dir_depth = args.dirdepth
    metafile = args.metafile
    exiftool_find(args.exiftool)

    #if existing directory is provided as an output use it as base directory to mirror base directory structure
    if output_path.is_dir():
        output_path = output_path / r'{Extra:FilePath}'

    #displaying parameters
    print("EXIF-writer by Alexander Taluts.")
    print(f"    Exiftool        : {exiftool_exe}")
    print(f"    Base directory  : {base_dir}")
    print(f"    Directory depth : {dir_depth}")
    print(f"    Wildcards       : {wildcards}")
    print(f"    Metafile        : {metafile}")
    print(f"    Output          : {output_path}")
    print( "    Temp. directory : ", end="")
    if temp_dir is None: print("<auto>")
    else: print(str(temp_dir))
    print("")

    print("Processing files...")
    file_counter = 0
    time_start = time.monotonic()
    #iterate through directories
    for root, dirs, files in os.walk(base_dir):
        current_path = Path(root)
        depth = len(current_path.relative_to(base_dir).parts)
        if dir_depth >= 0 and depth > dir_depth:
            continue    #skip if depth is deeper than being set

        #update metadata from metafiles
        metadata_dir = copy.deepcopy(metadata_default)
        if metafile.is_absolute():
            #path to metafile is absolute -> update metadata from single file
            if metafile.exists():
                metadata_update(metadata_dir, metadata_get_file(metafile), not metadata_dir.get('Script:LockTagList', False))
        else:
            #path to metafile is relative -> update metadata cumulatively from multiple metafiles
            path_relparts = current_path.relative_to(base_dir).parts
            path_chain = [base_dir]
            for part in path_relparts:
                path_chain.append(path_chain[-1] / part)
            for path in path_chain:
                metafile_cur = path / metafile
                if metafile_cur.exists():
                    metadata_update(metadata_dir, metadata_get_file(metafile_cur), not metadata_dir.get('Script:LockTagList', False))

        #iterate throught files in current directory
        for filename in files:
            if any(fnmatch.fnmatch(filename, pat) for pat in wildcards):
                input_path = current_path / filename
                metadata_file = copy.deepcopy(metadata_dir)
                metadata_update(metadata_file, metadata_get_path(input_path, base_dir), not metadata_dir.get('Script:LockTagList', False)) #get metadata for file from its path
                file_counter += 1
                print(f"{file_counter}. {input_path} >> ", end="")
                process_file(input_path, output_path, metadata_file, temp_dir)

    duration = int(time.monotonic() - time_start)
    hours, remainder = divmod(duration, 3600)
    minutes, seconds = divmod(remainder, 60)
    print(f"Finished. Processed {file_counter} files in {hours:02}:{minutes:02}:{seconds:02}.")

if __name__ == "__main__":
    main()