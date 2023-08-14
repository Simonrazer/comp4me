import os #Access to Filesystem
import re   #Regex
import subprocess   #Sys commands
import hashlib
import time
from threading import Thread

#region--------------PRINTING TOOLS---------------

#Thread that runs a little animation to indicate the programm working
wait_paused = False
def wait_anim():
    global wait_paused 
    wait_index = 0
    animation = [
        "[        ]",
        "[=       ]",
        "[===     ]",
        "[====    ]",
        "[=====   ]",
        "[======  ]",
        "[======= ]",
        "[========]",
        "[ =======]",
        "[  ======]",
        "[   =====]",
        "[    ====]",
        "[     ===]",
        "[      ==]",
        "[       =]",
        "[        ]"
    ]
    while True:
        if wait_paused:
            time.sleep(1)
            continue
        print(animation[wait_index % len(animation)], end="\r")
        time.sleep(.1)
        wait_index += 1

wait_thread = Thread(target = wait_anim, daemon=True)
wait_thread.start()

#input function that pauses and removes the waiting animation during input
def xinput(*txt):
    global wait_paused 
    wait_paused = True
    print ("                     ", end="\r")
    inputt = input(*txt)
    wait_paused = False
    return inputt
#endregion

top_level_dir = os.getcwd()
error_match = re.compile(r"fatal error: \\?\"?'?([_a-zA-Z0-9\./-]+)")
comp_reg = re.compile(r".*comp.toml\s*$")
include_match = re.compile(r"^[\s]*#include[^\S\r\n]*[\"<]([^\s\"\>]*)[\">]")
#In case the path-seperator is an escape character, double it up (Windows..)
reg_pathsep_t = os.path.sep
reg_pathsep = ""
for i in reg_pathsep_t:
    if i == "\\":
        reg_pathsep = reg_pathsep + i*2
    else:
        reg_pathsep = reg_pathsep + i

#https://stackoverflow.com/questions/8924173/how-can-i-print-bold-text-in-python
class color:
   PURPLE = '\033[95m'
   CYAN = '\033[96m'
   DARKCYAN = '\033[36m'
   BLUE = '\033[94m'
   GREEN = '\033[92m'
   YELLOW = '\033[93m'
   RED = '\033[91m'
   BOLD = '\033[1m'
   UNDERLINE = '\033[4m'
   END = '\033[0m'

error_string = color.RED+color.BOLD+"ERROR: "+color.END
warning_string = color.YELLOW+color.BOLD+"WARNING: "+color.END
interaction_required_string = color.CYAN+color.BOLD+"USER NEEDED: "+color.END
try: 
    import toml #TOML File Format
except Exception:
    print(error_string+"Module toml not found!")
    print("Install it via")
    print("\t pip install toml")
    exit()
def read_definitions(filepath):
    tmp_definitions = {}
    if os.path.exists(filepath):
        with open(os.path.join(filepath),"r") as fi:
            tmp_definitions = toml.load(fi)
    else:
        return {}
    return tmp_definitions

known_definitions = ["INHERIT", "EXCLUDE_SRC","ENTRYPOINT","NEUTRALS","EXCLUDES", "HEADER","C","CPP", "CCOMP", "CFLAGS", "CPPCOMP","CPPFLAGS","LINKER", "LINKERFLAGS","AR", "AS_LIB", "GENERATE_EXECUTABLE", "GENERATE_TEST", "EXCLUDED_FILES", "PROPAGATE", "NEXT_CONFIG", "QT5_MAKE", "ONLY_LINK_WITH_DIRECT_PARENT"]
def check_for_unknown_definitions(definitions, end = False):
    for d in definitions:
        if d not in known_definitions:
            if not end:
                print(warning_string+"config Variable", d, "is unknown")
                xinput("Press enter to acknowledge this. Tip: you can use # to write comments in config files")
            else:
                print(error_string+"config Variable", d ,"is unknown")
                exit()

#f = abspath, nm = path the result should be relative to to equal f
def abspath_to_relpath(f, nm):
        pt = str(f)
        ln = len(nm)
        arg = pt[:-ln]
        return arg

def rel_to_top(f):
    return os.path.relpath(f, top_level_dir)

def silent_cmd(c):
    p = subprocess.Popen(c, shell=True, stdout=subprocess.DEVNULL,stderr=subprocess.STDOUT)
    return p

def call_version(c):
    v1 = silent_cmd(c+ " -v")
    v2 = silent_cmd(c+ " -V")
    v1.wait()
    v2.wait()
    return {v1.returncode, v2.returncode}

def check_presence(t):
    return not any(element not in {127,1} for element in call_version(t))

#searches for a file with name `filename` in a given list
def find_file_locations(filename, place):
    result = []
    for f in place:
        if str(f).endswith(os.path.sep+filename) or str(f) == filename:    
            result.append(os.path.split(str(f))[0])
    return result


#This may be cached
#reads `file`, scans each line with a regex matching #include statements, returns all matches if the are also found in `known_includes`
read_files_cache = {}
def check_include_duality(file, known_includes):
    i_s = [] #list of names by which the included files are called in this file
    statements = []
    if file in read_files_cache:
        statements = read_files_cache[file]
    else:
        with open(file, "r") as f:
            lines = f.readlines()
            for l in lines:
                statements.extend(include_match.findall(l))
        read_files_cache[file] = statements

    for incl in statements:
        for ki in known_includes:
            if ki.endswith(os.path.sep+incl):
                i_s.append(incl)
                break
    return i_s

#returns the dir a path is located in, or False if in none
def is_path_in_any_dir(path, lib_dirs):
    candidate_libs = []
    for p in lib_dirs:
        if path.startswith(p):
            candidate_libs.append(p)
    if candidate_libs:
        l = max(candidate_libs, key=len)
        return os.path.split(l)[1]
    return False

def qt5_make(root, header_fileendings, path_change):
    def mocname(f):
        return root+os.path.sep+"moc_"+os.path.splitext(f)[0]+".cpp"
    
    def uicname(f):
        return root+os.path.sep+"ui_"+os.path.splitext(f)[0]+".h"

    process = silent_cmd("uic -v")
    process.wait()
    if process.returncode == 127:
        print(error_string+"Couldn't find uic")
        exit()
    process = silent_cmd("moc -v")
    process.wait()
    if process.returncode == 127:
        print(error_string+"Couldn't find moc")
        exit()
    for f in os.listdir(root):
        fp = os.path.join(root, f)
        modtime = os.path.getmtime(fp)
        if path_change:
            modtime = time.time()
        if os.path.isfile(fp):
            if f.endswith(".ui"):
                uicn = uicname(f)
                if not os.path.exists(uicn) or os.path.getmtime(uicn) < modtime:
                    cmd = "uic "+ fp +" > " + uicname(f)
                    subprocess.run(cmd, shell=True)
            elif os.path.splitext(f)[1] in header_fileendings and not f.startswith("ui_"):
                mocn = mocname(f)
                if not os.path.exists(mocn) or os.path.getmtime(mocn) < modtime:
                    cmd = "moc "+ fp +" > " + mocname(f)
                    subprocess.run(cmd, shell=True)

#https://www.programiz.com/python-programming/examples/hash-file (MIT-license)
def hash_file(filename):
   """"This function returns the SHA-1 hash
   of the file passed into it"""

   # make a hash object
   h = hashlib.sha1()

   # open file for reading in binary mode
   with open(filename,'rb') as file:

       # loop till the end of the file
       chunk = 0
       while chunk != b'':
           # read only 1024 bytes at a time
           chunk = file.read(1024)
           h.update(chunk)

   # return the hex representation of digest
   return h.hexdigest()

#Returns wether or not a file path is relevant, used to ignore hidden folders/build folder
def is_irrelevant(p) -> bool:
    return os.path.sep+"." in p or "build" in p.split(os.path.sep)

#Return the list l split into n chunks, ensuring that no element is listed twice, or is left out
def chunks(l, n):
    res = []
    for i in range(0, n):
        res.append(l[i::n])
    return res

def get_files_regex(looking_for_file):
    #.normcase converts all filepaths sepperators to system native ones
    looking_for_file_t = os.path.normcase(looking_for_file)
    #on windows, those are escape characters, sometimes. so they need to be doubled
    looking_for_file = ""
    for i in looking_for_file_t:
        if i == "\\":
            looking_for_file = looking_for_file + i*2
        else:
            looking_for_file = looking_for_file + i

    filesearch = re.compile(reg_pathsep+looking_for_file+'\s*$')
    return filesearch