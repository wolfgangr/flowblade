"""
    Flowblade Movie Editor is a nonlinear video editor.
    Copyright 2012 Janne Liljeblad.

    This file is part of Flowblade Movie Editor <https://github.com/jliljebl/flowblade/>.

    Flowblade Movie Editor is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    Flowblade Movie Editor is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with Flowblade Movie Editor.  If not, see <http://www.gnu.org/licenses/>.
"""

"""
Helper functions and data
"""
import time

import math
import hashlib
import os
import pickle
import re
import threading
import xml.dom.minidom

import appconsts
import editorstate

_start_time = 0.0

# ---------------------------------- CLASSES
class EmptyClass:
    pass


class Ticker:
    """
    Calls function repeatedly with given delay between calls.
    """
    def __init__(self, action, delay):
        self.action = action # callback function
        self.delay = delay # in seconds
        self.running = False
        self.exited = False
    
    def start_ticker(self, delay=None):
        self.ev = threading.Event()
        if delay == None: # If no delay specified, use default delay set at creation time
            delay = self.delay
        self.thread = threading.Thread(target=self.runner,  
                                       args=(self.ev, 
                                       delay, 
                                       self.action))
        self.running = True
        self.thread.start()
    
    def stop_ticker(self):
        try:
            self.ev.set()
            self.running = False # ! self.ev.set() may go to Exception leave this having wrong value if already stopped? 
        except Exception:
            pass # called when not running

    def runner(self, event, delay, action):
        while True:
            if not self.running:
                break
            action()
            if not self.running:
                break
            if event.isSet():
                break
            event.wait(delay)
        self.exited = True


class LaunchThread(threading.Thread):
    def __init__(self, data, callback):
        threading.Thread.__init__(self)
        self.data = data
        self.callback = callback
        
    def run(self):
        self.callback(self.data)
        
# -------------------------------- UTIL FUNCTIONS
def fps():
    return editorstate.PROJECT().profile.fps()

def get_fps_str_with_two_decimals(fps_str):
    fps_sides = fps_str.split(".")
    try:
        decimals = fps_sides[1][0:2]
    except:
        decimals = fps_sides[1]
    return fps_sides[0] + "." + decimals

# frames_per_second needs to be float
def clip_length_string(length, frames_per_second=None):
    """ 
    Returns length string for length in frames.
    """
    if frames_per_second == None:
        frames_per_second = fps()
        
    fr = length % frames_per_second
    sec = length / frames_per_second
    mins = sec / 60
    sec = int(math.floor(sec % 60))
    hours = int(math.floor(mins / 60))
    mins = int(math.floor(mins % 60))
    
    hr_str = ""
    if hours > 0:
        hr_str = str(hours) + "h"
    min_str = ""
    if mins > 0 or hours > 0:
        min_str = str(mins) + "m"
    if sec > 0 or min_str != "":
        s_str = str(sec) + "s"
    else:
        s_str = str(fr) + "fr"
    return hr_str + min_str + s_str

def get_tc_string(frame):
    """ 
    Returns timecode string for frame
    """
    return get_tc_string_with_fps(frame, fps())

def get_tc_string_short(frame):
    tc_str = get_tc_string(frame)
    while len(tc_str) > 4:
        if tc_str[0: 1] == "0" or tc_str[0: 1] == ":":
            tc_str = tc_str[1: len(tc_str)]
        else:
            break
    return tc_str
            
def get_tc_frame(frame_str):
    """
    Return timecode frame from string
    """
    return get_tc_frame_with_fps(frame_str, fps())

def get_tc_frame_with_fps(frame_str, frames_per_sec):
    # split time string hh:mm:ss:ff into integer and
    # calculate corresponding frame
    try:
        times = frame_str.split(":", 4)
    except Exception:
        return 0

    # now we calculate the sum of frames that would sum up at corresponding
    # time
    sum = 0
    for t in times:
        num = int(t)
        sum = sum * 60 + num

    # but well, actually, calculated sum is wrong, because according
    # to our calculation, that would give us 60 fps, we need to correct that
    # last 'num' is frames already, no need to correct those
    sum = sum - num
    sum = int(sum / (60.0 / round(frames_per_sec)))
    sum = sum + num

    # and that is our frame, so we return sum
    return sum

def get_tc_string_with_fps(frame, frames_per_sec):
    # convert fractional frame rates (like 23.976) into integers,
    # otherwise the timeline will slowly drift over time
    frames_per_sec = int(round(frames_per_sec))

    fr = frame % frames_per_sec
    sec = frame / frames_per_sec
    mins = sec / 60
    sec = sec % 60
    hours = mins / 60
    mins = mins % 60
    return "%02d:%02d:%02d:%02d" % (hours, mins, sec, fr)

def get_tc_string_with_fps_for_filename(frame, frames_per_sec):
    frames_per_sec = int(round(frames_per_sec))

    fr = frame % frames_per_sec
    sec = frame / frames_per_sec
    mins = sec / 60
    sec = sec % 60
    hours = mins / 60
    mins = mins % 60
    return "%02d-%02d-%02d-%02d" % (hours, mins, sec, fr)
    
def get_time_str_for_sec_float(sec):
    mins = sec / 60
    sec = sec % 60
    hours = mins / 60
    mins = mins % 60
    
    if hours >= 24.0:
        days = hours / 24
        hours = hours % 24
        return str(int(days)) + " days " + str(int(hours)) + "h " + str(int(mins)) + "m " + str(int(sec)) + "s"
    if hours >= 1.0:
        return str(int(hours)) + "h " + str(int(mins)) + "m " + str(int(sec)) + "s"
    if mins >= 1.0:
        return str(int(mins)) + "m " + str(int(sec)) + "s"
    return str(int(sec)) + "s"
        
def get_track_name(track, sequence):
    if track.type == appconsts.VIDEO:
        # Video tracks are numbered to USER as 'V1' ,'V2' with 'V1' being
        # tracks[current_sequence.first_video_index]
        if track.id == sequence.first_video_index:
            text = "V1"
        else:
            text = "V" + str(track.id - sequence.first_video_index + 1)
    else:
        # Audio tracks are numbered in *opposite* direction for USER view
        # so if we have audio tracks in tracks[1] and tracks[2]
        # User thinks tracks[1] is 'A2' and track[2] is 'A1'
        # This is also compensated for in Sequence.get_first_active_track()
        text = "A" + str(sequence.first_video_index - track.id)
    return text

def file_extension_is_graphics_file(ext):
    ext = ext.lstrip(".")
    ext = ext.lower()
    if ext in _graphics_file_extensions:
        return True
    else:
        return False

# ------------------------------------------------ module util methods
def get_media_type(file_path):
    """
    Returns media type of file.
    """
    if os.path.exists(file_path):
        mime_type = get_file_type(file_path)
    else:
        # IMAGE_SEQUENCE media objects have a MLT formatted resource path that does not
        # point to an existing file in the file system. 
        # We're doing a heuristic here to identify those.
        pros_index = file_path.find("%0")
        d_index = file_path.find("d.")
        if pros_index != -1 and d_index != -1:
            return appconsts.IMAGE_SEQUENCE
        all_index = file_path.find(".all")
        if all_index != -1:
            return appconsts.IMAGE_SEQUENCE
            
        return appconsts.FILE_DOES_NOT_EXIST
        
    if mime_type.startswith("video"):
        return appconsts.VIDEO
    
    if mime_type.startswith("audio"):
        return appconsts.AUDIO
    
    if mime_type.startswith("image"):
        return appconsts.IMAGE
    
    return appconsts.UNKNOWN
    
def get_file_type(file_path):
    name, ext = os.path.splitext(file_path)
    ext = ext.lstrip(".")
    ext = ext.lower()
    if ext in _video_file_extensions:
        return "video"
    
    if ext in _audio_file_extensions:
        return "audio"
    
    if ext in _graphics_file_extensions:
        return "image"
    
    return "unknown"

def is_mlt_xml_file(file_path):
    name, ext = os.path.splitext(file_path)
    ext = ext.lstrip(".")
    ext = ext.lower()
    if ext == "xml" or ext == "mlt":
        return True
    
    return False
        
def hex_to_rgb(value):
    value = value.lstrip('#')
    lv = len(value)
    return tuple(int(value[i:i+lv//3], 16) for i in range(0, lv, lv//3))

def int_to_hex_str(n):
    val = int_to_hex(n)
    if val == "0":
        return "00"
    else:
        return val

def int_to_hex(n):
    # Input value range 0 - 255, 00 - ff
    val_str = hex(n)[2:]
    if len(val_str) == 1:
        val_str = "0" + val_str
    return val_str

def gdk_color_str_to_mlt_color_str(gdk_color_str):
    raw_r, raw_g, raw_b = hex_to_rgb(gdk_color_str)
    val_str = "#" + int_to_hex(int((float(raw_r) * 255.0) / 65535.0)) + \
                    int_to_hex(int((float(raw_g) * 255.0) / 65535.0)) + \
                    int_to_hex(int((float(raw_b) * 255.0) / 65535.0))
    return val_str

def gdk_color_str_to_int(gdk_color_str):
    # returned int is 32-bit RGBA, alpha is 00 
    raw_r, raw_g, raw_b = hex_to_rgb(gdk_color_str)
    red = int((float(raw_r) * 255.0) / 65535.0)
    green = int((float(raw_g) * 255.0) / 65535.0)
    blue = int((float(raw_b) * 255.0) / 65535.0)
    
    return (red << 24) + (green << 16) + (blue << 8)

def gdk_color_str_to_cairo_rgb(gdk_color_str):
    # returned int is 32-bit RGBA, alpha is 00 
    raw_r, raw_g, raw_b = hex_to_rgb(gdk_color_str)
    return (float(raw_r)/65535.0, float(raw_g) /65535.0, float(raw_b)/65535.0)

def get_cairo_color_tuple_255_rgb(r, g, b):
    return (float(r)/255.0, float(g)/255.0, float(b)/255.0)

def cairo_color_from_gdk_color(gdk_color):
    color = gdk_color.to_string().rstrip(")").lstrip("rgb(").split(',')
    return (float(color[0])/255.0, float(color[1])/255.0, float(color[2])/255.0)
    
def do_nothing():
    pass

def get_unique_name_for_audio_levels_file(media_file_path, profile):
    size_str = str(os.path.getsize(media_file_path))
    fps_str = str(profile.description())
    file_name = hashlib.md5((media_file_path + size_str + fps_str).encode('utf-8')).hexdigest()
    return file_name

def get_img_seq_glob_lookup_name(asset_file_name):
    parts1 = asset_file_name.split("%")
    start = parts1[0]
    end = parts1[1].split("d")[1]
    try:
        end = end.split("?")[0]
    except:
        print("old style img seq name for " + asset_file_name)
    
    return start + "*" + end

def get_img_seq_resource_name(frame_file):
    (folder, file_name) = os.path.split(frame_file)
    try:
        number_parts = re.findall("[0-9]+", file_name)
        number_part = number_parts[-1] # we want the last number part 
    except:
        # Selected file does not have a number part in it, so it can't be an image sequence file.
        return None

    # Create resource name with MLT syntax for MLT producer
    number_index = file_name.find(number_part)
    path_name_part = file_name[0:number_index]
    end_part = file_name[number_index + len(number_part):len(file_name)]

    resource_name_str = path_name_part + "%" + "0" + str(len(number_part)) + "d" + end_part

    return resource_name_str

def get_file_producer_info(file_producer):
    clip = file_producer
    
    info = {}
    info["width"] = clip.get_int("width")
    info["height"] = clip.get_int("height")
    info["length"]  = clip.get_length()
    
    video_index = clip.get_int("video_index")
    audio_index = clip.get_int("audio_index")
    long_video_property = "meta.media." + str(video_index) + ".codec.long_name"
    long_audio_property = "meta.media." + str(audio_index) + ".codec.long_name"
    sample_rate_property = "meta.media." + str(audio_index) + ".codec.sample_rate"
    channels_property = "meta.media." + str(audio_index) +  ".codec.channels"
    
    info["vcodec"] = clip.get(str(long_video_property))
    info["acodec"] = clip.get(str(long_audio_property))
    info["channels"] = clip.get_int(str(channels_property))
    info["frequency"] =  clip.get_int(str(sample_rate_property))
    frame = clip.get_frame()
    info["fps_num"] = frame.get_double("meta.media.frame_rate_num")
    info["fps_den"] = frame.get_double("meta.media.frame_rate_den")
    info["progressive"] = frame.get_int("meta.media.progressive") == 1
    info["top_field_first"] = frame.get_int("meta.media.top_field_first") == 1
    
    resource = clip.get("resource")
    name, ext = os.path.splitext(resource)
    ext = ext.lstrip(".")
    ext = ext.lower()
    if ext == "xml" or ext =="mlt":
        update_xml_file_producer_info(resource, info)
        
    return info

def update_xml_file_producer_info(resource, info):
    # xml and mlt files require reading xml file to determine producer info
    mlt_doc = xml.dom.minidom.parse(resource)

    mlt_node = mlt_doc.getElementsByTagName("mlt").item(0)
    profile_node = mlt_node.getElementsByTagName("profile").item(0)

    info["width"] = int(profile_node.getAttribute("width"))
    info["height"] = int(profile_node.getAttribute("height"))
    info["fps_num"] = float(profile_node.getAttribute("frame_rate_num"))
    info["fps_den"] = float(profile_node.getAttribute("frame_rate_den"))
    info["progressive"] = int(profile_node.getAttribute("progressive"))
    #  <profile description="HD 720p 29.97 fps" width="1280" height="720" progressive="1" sample_aspect_num="1" sample_aspect_den="1" display_aspect_num="16" display_aspect_den="9" frame_rate_num="30000" frame_rate_den="1001" colorspace="0"/>
    
def is_media_file(file_path):
    file_type = get_file_type(file_path)
    if file_type == "unknown":
        return False
    else:
        return True

def program_is_installed(program):
    def is_exe(fpath):
        return os.path.isfile(fpath) and os.access(fpath, os.X_OK)

    fpath, fname = os.path.split(program)
    if fpath:
        if is_exe(program):
            return True
    else:
        for path in os.environ["PATH"].split(os.pathsep):
            path = path.strip('"')
            exe_file = os.path.join(path, program)
            if is_exe(exe_file):
                return True

    return False
    
# File exntension lists
_audio_file_extensions = [  "act",
                            "aif",
                            "aiff",
                            "alfc",
                            "aac",
                            "alac",
                            "amr",
                            "atrac",
                            "awb",
                            "dct",
                            "dss",
                            "dvf",
                            "flac",
                            "gsm",
                            "iklax",
                            "m4a",
                            "m4p",
                            "mka",
                            "mmf",
                            "mp2",
                            "mp3",
                            "mpc",
                            "msv",
                            "ogg",
                            "oga",
                            "opus",
                            "pcm",
                            "u16be",
                            "u16le",
                            "u24be",
                            "u24le",
                            "u32be",
                            "u32le",
                            "u8",
                            "ra",
                            "rm",
                            "raw",
                            "tta",
                            "vox",
                            "wav",
                            "wma",
                            "wavpack"]

_graphics_file_extensions = [   "bmp",
                                "tiff",
                                "tif",
                                "gif",
                                "tga",
                                "png",
                                "pgm",
                                "jpeg",
                                "jpg",
                                "svg"]

_video_file_extensions = [  "avi",
                            "dv",
                            "flv",
                            "mkv",
                            "mpg",
                            "mpeg",
                            "m2t",
                            "mov",
                            "mp4",
                            "qt",
                            "vob",
                            "webm",
                            "3gp",
                            "3g2",
                            "asf",
                            "divx",
                            "dirac",
                            "f4v",
                            "h264",
                            "hdmov",
                            "hdv",
                            "m2p",
                            "m2ts",
                            "m2v",
                            "m4e",
                            "mlt",
                            "mjpg",
                            "mp4v",
                            "mts",
                            "m21",
                            "m2p",
                            "m4v",
                            "mj2",
                            "m1v",
                            "mpv",
                            "m4v",
                            "mxf",
                            "mpegts",
                            "mpegtsraw",
                            "mpegvideo", 
                            "nsv",
                            "ogv",
                            "ogx",
                            "ps",
                            "ts",
                            "tsv",
                            "tsa",
                            "vfw",
                            "video",
                            "wtv",
                            "wm",
                            "wmv",
                            "xvid",
                            "y4m",
                            "yuv",
                            "xml"]


def start_timing(msg="start timing"):
    global _start_time
    _start_time = time.time()
    print(msg)

def elapsed_time(msg="elapsed: ", show_in_millis=True):
    elapsed_time = time.time() - _start_time
    if show_in_millis:
        elapsed_time = round(elapsed_time * 1000.0, 1)
        unit = "ms"
    else:
        unit = "s"

def unpickle(path):
    try:
        f = open(path, "rb")
        return pickle.load(f)
    except:
        f = open(path, "rb")
        return pickle.load(f, encoding='latin1') 

def get_flatpak_real_path_for_app_files(app_file):
    # Blender etc. some times need real absolute paths for application script files.
    # A path like /app/share/flowblade/file.blend is only valid inside the flatpak sandbox. 
    # You can get the real path on the host filesystem from the file /.flatpak-info (at top of the root directory inside the sandbox)
    f = open("/.flatpak-info", "r")
    for line in f:
        if line.startswith("app-path"):
            real_path = line[9:len(line)].rstrip() # 9 strips "app-path", rstrip strips newline
            app_file_path = real_path + app_file[4:len(app_file)] # strips "/app" from beginning of Flatpak path for files
                                                                  # combining flatpak app-path with flatpak relative path gets real absolute path
            return app_file_path
    
    return None # Hitting here needs to crash


def get_headless_arg_value(args, key_str):
    for arg in args:
        parts = arg.split(":")
        if len(parts) > 1:
            if parts[0] == key_str:
                return parts[1]
    
    return None