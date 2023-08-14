#!/usr/bin/env python3

from c_util import *
from threading import Thread #Multithreading
import subprocess   #Sys commands
import time #Debug runtime info
try:
    from argparse import ArgumentParser
except Exception:
    print(error_string+"Module argparse not found!")
    print("Install it via")
    print("\t pip install argparse")
    exit()
import shutil #Only used to del old build Folder Contents
import signal #Catch Ctrl-C

#In case the path-seperator is an escape character, double it up (Windows..)
reg_pathsep_t = os.path.sep
reg_pathsep = ""
for i in reg_pathsep_t:
    if i == "\\":
        reg_pathsep = reg_pathsep + i*2
    else:
        reg_pathsep = reg_pathsep + i

#Performance Evaluation
t0 = time.time()
procTime = 0

#Catch abort, don't print pythons default message
#https://stackoverflow.com/questions/1112343/how-do-i-capture-sigint-in-python
def signal_handler(sig, frame):
    print("\n"+color.RED+"Aborting"+color.END)
    exit()
signal.signal(signal.SIGINT, signal_handler)

#check if Modules are installed

parser = ArgumentParser()

#Configure argparser
parser.add_argument('config', nargs="?", default="comp.toml", help="Which comp.toml file to use. Uses the plain one by default")
parser.add_argument('-F', '--extra-files', action="append", default=[], help="Define source files of an excluded Folder to take into Compilation")
parser.add_argument('-D', '--extra-folders', action="append", default=[], help="Override exclude for this folder to instead be an Entry point")
parser.add_argument('-L', '--lib-folders', action="append", default=[], help="Compile the argument each into one .a")
parser.add_argument('-v', '--verbose', action='store_true', help="Verbose output, gives more information.")
parser.add_argument('-p', '--print-commands', action='store_true', help="Print all commands used to compile.")
parser.add_argument('--no-ccache', action='store_true', help="Use ccache to speed up compilation via caching. Needs to be installed, will not be used if not avaivable.")
parser.add_argument('-C', '--no-cache', action='store_true', help="Delete cache file before starting compilation")
parser.add_argument('--print-structure', action='store_true', help="Display a file tree in colors describing the status of each subdir in the project")
parser.add_argument('-T', '--thread-num', default=1, type=int, help="Number of threads to use for compilation. Using ccache is a greater benefit than multithreading.")

args = parser.parse_args()

#Variables Shared between all subprojects
cache_dictionary = {}

header_files = {} #header files included with -I, dict of {path/filename : File}
neutral_files = set() #all abs paths of files in neutral folders
excluded_files = [] #files with an interesting fileending, that were stored in a folder set to be excluded

all_proj_dirs_with_links = [] #list to all directories containing projects, includes duplicates
all_projs = [] #List of all projects as Project() Objects, used to launch all of their tasks
#every Project() object also has self.subproject_dirs, a list of the subprojects that are located in this Project

default_config_path = os.path.join(os.path.split(__file__)[0], "default.toml")
default_config_definitions = {} #definitions read from the default config file
default_include_choices = {} #stores answer if, and which specific header file is supposed to be included for each file by default
                                #or if the user should be asked to make a choice between multiple options again

checked_header_files = [] #list of Header files that got processed already, so we don't deal with them twice

#Only prints if verbose is set (* means any number of arguments, just like usual print)
def vprint(*t):
    if args.verbose:
        print(*t)

#Load the cache
build_dir = os.path.join(os.getcwd(), "build")

if not args.no_cache and os.path.exists(os.path.join(build_dir, "comp_cache")):
    cache_dictionary = toml.load(os.path.join(build_dir, "comp_cache"))

needed_src = {} #dict of header files that dont need source files/which src they need {path+name:False/Path+Name}
if "NEEDED_SRC_FILES_SUBCACHE" in cache_dictionary:
    needed_src = cache_dictionary["NEEDED_SRC_FILES_SUBCACHE"] 

subproject_usage_cache = {}
if "SUBPROJ" in cache_dictionary:
    subproject_usage_cache = cache_dictionary["SUBPROJ"]

linkerscript_cache = {}
if "LINKERSCRIPT_TO_USE" in cache_dictionary:
    linkerscript_cache = cache_dictionary["LINKERSCRIPT_TO_USE"]

class Project:
    def __init__(self, md="", ftu="", is_top_level = False, inherited_definitions = {}):
        global cache_dictionary, neutral_files, header_files, excluded_files, all_projs, build_dir
        global procTime, subproject_usage_cache, needed_src

        #region-----------VARIABLE DECLARATIONS--------------------

        #Directory of this project, is in case of top_level cwd, or argument md
        self.main_directory = ""
        
        #List of directories in this Project that are to compiled into a library
        self.lib_dirs = []

        #List of directories in this Project that are made an Entrypoint via Cmd Argument
        self.extra_folders = []

        #List of files in this Project that will be acted on as if they are in an Entrypoint via Cmd Argument
        self.extra_files = []
        

        #Config-file to use for the following sub-projects, if empty the user will be asked, configured by comp.toml
        self.subproject_config_to_use = "comp.toml"

        #List of linkerscripts found in this subproject
        self.found_linkersscripts = []
        
        if is_top_level:
            #Cmd-Line definitions are only valid for top-level projects
            self.lib_dirs = args.lib_folders
            self.extra_folders = args.extra_folders
            self.extra_files = args.extra_files
            self.main_directory = top_level_dir

        #If this is a subproject, change build dir and caller_dir to args of work function
        else:
            self.main_directory = md
        
        #Config-file to use for this project
        self.config_file_to_use = ftu

        #Name of the subfolder in the build directory to compile this project into
        self.build_subdir = os.path.split(self.main_directory)[1]+"#"+self.config_file_to_use

        #invalidate cache if configfile has changed
        if os.path.exists(os.path.join(self.main_directory, self.config_file_to_use)):
            hash = hash_file(os.path.join(self.main_directory, self.config_file_to_use))
        else:
            hash = "-1"

        #Detect changes in config file and discard chache if that's the case
        if "HASHES" not in cache_dictionary:
            cache_dictionary["HASHES"] = {}
        if self.main_directory in cache_dictionary["HASHES"]:
            if cache_dictionary["HASHES"][self.main_directory] != hash:
                tmp = cache_dictionary["HASHES"]
                cache_dictionary = {}
                subproject_usage_cache = {}
                cache_dictionary["HASHES"] = tmp
        else:
            tmp = cache_dictionary["HASHES"]
            cache_dictionary = {}
            subproject_usage_cache = {}
            cache_dictionary["HASHES"] = tmp
        cache_dictionary["HASHES"][self.main_directory] = hash
        if "MOC_REALPATHS" not in cache_dictionary:
            cache_dictionary["MOC_REALPATHS"] = []

        print(color.BOLD+"Initializing Project"+color.END, rel_to_top(self.main_directory))
        #create build dir structure
        if is_top_level:
            if not os.path.exists(build_dir):
                os.mkdir(build_dir)
                os.mkdir(os.path.join(build_dir, self.build_subdir))
            else:
                for root, dirs, files in os.walk(build_dir):
                    for f in files:
                        if args.no_cache and f == "comp_cache":
                            os.unlink(os.path.join(root, f))
                    for d in dirs:
                        shutil.rmtree(os.path.join(root, d))
                os.mkdir(os.path.join(build_dir, self.build_subdir))
        else:
            #Detect subprojects with the same name and don't let the user do that
            try:
                os.mkdir(os.path.join(build_dir, self.build_subdir))
            except FileExistsError:
                print(error_string+"Subproject with name", self.build_subdir,"defined twice")
                print("Multiple Symbolic links to the same Project are allowed, but Projects themselves must have unique names")
                exit()

        os.mkdir(os.path.join(build_dir, self.build_subdir, "non_prop"))
        os.mkdir(os.path.join(build_dir, self.build_subdir, "obj"))
        os.mkdir(os.path.join(build_dir, self.build_subdir, "lib"))

        #files that will be included in compilation, dict of {filename_no_ext : File}
        self.src_files = {} 
        
        #all abs paths of files in entry folders
        self.entry_files = []

        self.non_propageted_dirs = [] #source files coming from one of these directories will be marked not to be included when another subproject requires this project
        #!! If GENERATE_TEST is true, this will be set to a default of ["test"]

        #Strings to link precompiled sources, private means placed in a non-prop and entry folders, public is only entry
        self.private_precomps = ""
        self.public_precomps = ""

        #every sourcefile in entries will be compiled into a .o file, and ressources for that will be searched for primarily (=> no duplicate names of src files allowed)
        #in all header files located in (sub) folders of any target folder
        #header files outside a target folder will also be searched, but if a single match in a target folder is found that one will be used
        #if there are multiple options that lie in a target folder, the user will be asked to make a choice (see fill_includes)
        #sourcefiles for headerfiles will be searched for primarily in target folders. if none are found there, none will be used
        #if a/multiple are found outside a target folder, the user will be asked to make a choice
        self.entries = []

        #files in excluded folders will not be put in entry_files, but in excluded_files
        #if there is no match for a needed file in other valid folders, but one is found in an excluded folder, the user will be told about that
        self.excludes = []

        self.exclude_src_dirs = []

        self.manual_neutrals = []

        #List of directories of found subprojects, used to not compile files located in them again
        self.subproject_dirs = []

        #By default, only generate an executable out of this (Sub-)Project if it is at the top level
        self.generate_executable = is_top_level
        generate_tests = False

        self.only_link_with_direct_parent = False

        self.to_be_inherited_definitions = {}

        #fileendings describe how a file should be treated (compiler used, is header/sourcefile)
        #defaults are amended by the given comp.toml file
        self.srcC_fileendings = {".c", ".s", ".S"}
        self.srcCpp_fileendings = {".cpp", ".cc"}
        self.header_fileendings = {".h", ".hpp"}

        self.precompiled_os_fileendings = {".o", ".obj"}
        self.precompiled_as_fileendings = {".a", ".so"}
        self.precompiled_fileendings = self.precompiled_os_fileendings.union(self.precompiled_as_fileendings)
        self.linkerscript_fileendings = {".ld"}

        self.cflags = []
        self.cppflags = []
        self.linkerflags = []
        self.ccomp = "gcc"
        self.cppcomp = "g++"
        self.ar = "ar"
        self.linker = self.cppcomp
        default_c_comp = True
        default_cpp_comp = True
        default_ar = True
        default_linker = True
        
        #endregion

        #region--------------ARGUMENT PARSING----------------------
        entrypoint_was_defined = False
        qt5_dirs = []
        qt_path_changed = False
        #endregion

        #region--------------ARGUMENT PARSING----------------------
        entrypoint_was_defined = False
        qt5_dirs = []
        def parse(definitions):
                nonlocal qt_path_changed, generate_tests, entrypoint_was_defined, qt5_dirs, default_ar, default_c_comp, default_cpp_comp, default_linker
                check_for_unknown_definitions(definitions)
                if "ENTRYPOINT" in definitions:
                    entrypoint_was_defined = True
                    if type(definitions["ENTRYPOINT"]) == str:
                        self.entries.append(os.path.join(self.main_directory,definitions["ENTRYPOINT"]))
                    else:
                        self.entries.extend([os.path.join(self.main_directory, x) for x in definitions["ENTRYPOINT"]])
                if "EXCLUDES" in definitions:
                    if type(definitions["EXCLUDES"]) == str:
                        self.excludes.append(os.path.join(self.main_directory,definitions["EXCLUDES"]))
                    else:
                        self.excludes.extend([os.path.join(self.main_directory, x) for x in definitions["EXCLUDES"]])
                if "EXCLUDE_SRC" in definitions:
                    if type(definitions["EXCLUDE_SRC"]) == str:
                        self.exclude_src_dirs.append(os.path.join(self.main_directory,definitions["EXCLUDE_SRC"]))
                    else:
                        self.exclude_src_dirs.extend([os.path.join(self.main_directory, x) for x in definitions["EXCLUDE_SRC"]])
                if "NEUTRALS" in definitions:
                    if type(definitions["NEUTRALS"]) == str:
                        self.manual_neutrals.append(os.path.join(self.main_directory,definitions["NEUTRALS"]))
                    else:
                        self.manual_neutrals.extend([os.path.join(self.main_directory, x) for x in definitions["NEUTRALS"]])
                #Flags can either be a list of strings, or just a long string. We'll convert it to a list with a single element in the second case
                if "CFLAGS" in definitions:
                    tmp_flags = definitions["CFLAGS"]
                    if type(tmp_flags) == str:
                        self.cflags.append(tmp_flags)
                    elif type(tmp_flags) == list:
                        for fl in tmp_flags:
                            if fl == "CPPFLAGS":
                                self.cflags.extend(self.cppflags)
                            elif fl == "LINKERFLAGS":
                                self.cflags.extend(self.linkerflags)
                            else:
                                self.cflags.append(fl)
                if "CPPFLAGS" in definitions:
                    tmp_flags = definitions["CPPFLAGS"]
                    if type(tmp_flags) == str:
                        self.cppflags.append(tmp_flags)
                    elif type(tmp_flags) == list:
                        for fl in tmp_flags:
                            if fl == "CFLAGS":
                                self.cppflags.extend(self.cflags)
                            elif fl == "LINKERFLAGS":
                                self.cppflags.extend(self.linkerflags)
                            else:
                                self.cppflags.append(fl)
                if "LINKERFLAGS" in definitions:
                    tmp_flags = definitions["LINKERFLAGS"]
                    if type(tmp_flags) == str:
                        self.linkerflags.append(tmp_flags)
                    elif type(tmp_flags) == list:
                        for fl in tmp_flags:
                            if fl == "CPPFLAGS":
                                self.linkerflags.extend(self.cppflags)
                            elif fl == "CFLAGS":
                                self.linkerflags.extend(self.cflags)
                            else:
                                self.linkerflags.append(fl)
                
                if "HEADER" in definitions:
                    self.header_fileendings=self.header_fileendings.union(definitions["HEADER"])
                if "C" in definitions:
                    self.srcC_fileendings=self.srcC_fileendings.union(definitions["C"])
                if "CPP" in definitions:
                    self.srcCpp_fileendings=self.srcCpp_fileendings.union(definitions["CPP"])
                if "CCOMP" in definitions:
                    self.ccomp = definitions["CCOMP"]
                    default_c_comp = False
                if "CPPCOMP" in definitions:
                    self.cppcomp = definitions["CPPCOMP"]  
                    default_cpp_comp = False
                if "LINKER" in definitions:
                    self.linker =  definitions["LINKER"]
                    default_linker = False
                else:
                    self.linker = self.cppcomp
                if "AR" in definitions:
                    self.ar = definitions["AR"]
                    default_ar = False
                if "AS_LIB" in definitions:
                    if type(definitions["AS_LIB"]) == str:
                        self.lib_dirs.append(os.path.join(self.main_directory,definitions["AS_LIB"]))
                    else:
                        self.lib_dirs.extend([os.path.join(self.main_directory, x) for x in definitions["AS_LIB"]])
                if "EXCLUDED_FILES" in definitions:
                    excluded_files.extend([os.path.join(self.main_directory, a) for a in definitions["EXCLUDED_FILES"]])
                if "GENERATE_TEST" in definitions:
                    generate_tests = definitions["GENERATE_TEST"]
                    #if this is set, and NON_PROPAGATE is not, ./test will not be propagated
                    #also an executable will be generated
                    #also if NEXT_CONFIG is not set, subprojs configs with the same name as the current config file will be choosen
                if "GENERATE_EXECUTABLE" in definitions:
                    self.generate_executable = definitions["GENERATE_EXECUTABLE"]
                    #if this is set, and NON_PROPAGATE is not, ENTRYPOINT will not be propagated
                    #also an executable will be generated
                if generate_tests:
                    self.non_propageted_dirs = [os.path.abspath(os.path.join(self.main_directory, name)) for name in os.listdir(self.main_directory) if os.path.isdir(os.path.join(self.main_directory, name)) and name.endswith("test")]
                    self.entries.extend(self.non_propageted_dirs)
                elif self.generate_executable:
                    self.non_propageted_dirs = self.entries

                if "PROPAGATE" in definitions:
                    tmp = [os.path.join(self.main_directory, x) for x in definitions["PROPAGATE"]]
                    for t in tmp:
                        if t in self.non_propageted_dirs:
                            self.non_propageted_dirs.remove(t)
                
                if "NEXT_CONFIG" in definitions:
                    self.subproject_config_to_use = definitions["NEXT_CONFIG"]
                elif generate_tests:
                    self.subproject_config_to_use = self.config_file_to_use
                if "QT5_MAKE" in definitions:
                    qt5_dirs.extend(definitions["QT5_MAKE"])
                    new_qt_realpaths = set([os.path.realpath(x) for x in qt5_dirs])
                    qt_path_changed = new_qt_realpaths != set(cache_dictionary["MOC_REALPATHS"])  
                    cache_dictionary["MOC_REALPATHS"] = new_qt_realpaths             
                if "ONLY_LINK_WITH_DIRECT_PARENT" in definitions:
                    self.only_link_with_direct_parent = ["ONLY_LINK_WITH_DIRECT_PARENT"]
                if "INHERIT" in definitions:
                    self.to_be_inherited_definitions = {}
                    to_inherit = definitions["INHERIT"]
                    check_for_unknown_definitions(to_inherit, end=True)
                    for d in to_inherit:
                        try:
                            self.to_be_inherited_definitions[d] = definitions[d]
                        except:
                            print(error_string+"A definition that was supposed to be inherited to a subproject is not defined in this project")
                            exit()

        #Parsing comp.toml
        defs = default_config_definitions.copy()
        defs.update(inherited_definitions)
        defs.update(read_definitions(os.path.join(self.main_directory, self.config_file_to_use)))
        parse(defs)
        if not entrypoint_was_defined:
            vprint("No entrypoint was defined for project", self.main_directory+". Adding the top-level folder")
            self.entries.append(self.main_directory)

        if generate_tests:
            self.generate_executable = True

        #If no manual Excludes are defined, ignore top level folders called "*test"
        if not generate_tests and len(self.excludes) == 0:
            x = [os.path.abspath(os.path.join(self.main_directory, name)) for name in os.listdir(self.main_directory) if os.path.isdir(os.path.join(self.main_directory, name)) and name.endswith("test")]
            self.excludes.extend(x)

        #Clean -lib-folders, extra-folders, excludes and entries args of trailing '/'
        def rm_backslash(l):
            for i in range(len(l)):
                if l[i].endswith(os.path.sep):
                   l[i] = l[i][:-1]
        rm_backslash(self.lib_dirs)
        rm_backslash(self.extra_folders)
        rm_backslash(self.excludes)
        rm_backslash(self.entries)
        rm_backslash(self.exclude_src_dirs)

        #Check if path definitions actually exists
        for L in self.lib_dirs:
            if not os.path.exists(L):
                print(error_string+"Library path",L,"wasn't found in any directory of project in", self.main_directory)
                exit()
        for L in self.entries:
            if not os.path.exists(L):
                print(error_string+"Entrypoint path",L,"wasn't found in any directory of project in", self.main_directory)
                exit()
        for L in self.manual_neutrals:
            if not os.path.exists(L):
                print(error_string+"Neutral path",L,"wasn't found in any directory of project in", self.main_directory)
                exit()
        for L in self.excludes:
            if not os.path.exists(L):
                print(warning_string+"Exclude path",L,"wasn't found in any directory of project in", self.main_directory)
        for L in self.exclude_src_dirs:
            if not os.path.exists(L):
                print(warning_string+"Exclude-src path",L,"wasn't found in any directory of project in", self.main_directory)

        #Check overlapping file extension definitions
        self.src_fileendings = self.srcC_fileendings.union(self.srcCpp_fileendings)
        self.allowed_fileendings = self.src_fileendings.union(self.header_fileendings)

        if (self.srcC_fileendings & self.srcCpp_fileendings) | (self.srcC_fileendings & self.header_fileendings) | (self.srcCpp_fileendings & self.header_fileendings):
            print(error_string+"overlapping Header, C and Cpp fileendings. Choose one or the other")
            print("Header:",self.header_fileendings)
            print("C:",self.srcC_fileendings)
            print("CPP:",self.srcCpp_fileendings)
            exit()

        #Add extra Dirs to compilation
        for d in self.extra_folders:
            p = os.path.join(self.main_directory, d)
            if os.path.exists(p):
                self.entries.append(p)
            else:
                print(error_string+"Extra Folder", d,"is not found in this directory")
                print("Tip: you need to declare it as a relative path starting at the projects root folder")
                exit()

        #region---CHECK IF TOOLS ARE AVAIVABLE---
        if default_c_comp:
            vprint("No CCOMP was given, using gcc")
            if check_presence("gcc"):
                vprint(error_string+"Couldn't find gcc! Looking for clang instead")
                if check_presence("clang"):
                    print(error_string+" Couldn't find clang either! Install either gcc or clang and give terminals access to it")
                    exit()
                vprint("Found clang, using that")
                self.ccomp = "clang"
        else:
            if check_presence(self.ccomp):
                print(error_string+"The demanded C-Compiler", self.ccomp,"wasn't found! Aborting")
                exit()

        if default_cpp_comp:
            if check_presence("g++"):
                vprint(error_string+"Couldn't find g++! Looking for clang++ instead")
                if check_presence("clang++"):
                    print(error_string+" Couldn't find clang++ either! Install either g++ or clang++ and give terminals access to it")
                    exit()
                vprint("Found clang++, using that")
                self.cppcomp = "clang++"
        else:
            if check_presence(self.cppcomp):
                print(error_string+"The demanded Cpp-Compiler", self.cppcomp,"wasn't found! Aborting")
                exit()

        if default_ar:
            if check_presence(self.ar) and len(self.lib_dirs) > 0:
                print(error_string+"Couldn't find ar! Aborting!")
                exit()
        else:
            if check_presence(self.ar):
                print(error_string+"The demanded AR", self.ar,"wasn't found! Aborting")
                exit()

        if default_linker:
            vprint("No Linker was given, using", self.cppcomp)
            self.linker = self.cppcomp
        else:
            if check_presence(self.linker):
                print(error_string+"The demanded Linker", self.linker,"wasn't found! Aborting")
                exit()

        if not args.no_ccache:
            if check_presence("ccache"):
                print(warning_string+"Couldn't find ccache! Not using it")
            else:
                self.ccomp = "ccache "+self.ccomp
                self.cppcomp = "ccache "+self.cppcomp
        #endregion

        for u in qt5_dirs:
            qt5_make(os.path.join(self.main_directory,u), self.header_fileendings, qt_path_changed)
        #endregion

    #region-----------------FILE CLASS-------------------------
    class File:
        def __init__(self, project, name, path, reas, modtime = None):
            self.name = name #filename with extension
            n_e = os.path.splitext(name)
            self.name_no_ext = n_e[0]
            self.path = path #filepath without filename
            self.ext = n_e[1] #fileextension
            self.reason = reas #which file requires the inclusion of this file, may also be the a target folder
            self.include_string = "" #safes the include string of this file, so it doesn't
                                        #need to be calculated again every time this file is included
            self.compiled_to_lib_folder = is_path_in_any_dir(path, project.lib_dirs)
            self.project = project
            if modtime == None:
                self.modtime = os.path.getmtime(os.path.join(path, name))
            else:
                self.modtime = modtime
            pass

        #self to string, used for printing and indexing in dicts
        def __str__(self) -> str:
            return os.path.join(self.path, self.name)
        
        #looks up when the file was modified and compares that to the cache date
        def is_outdated(self) -> bool:
            if str(self) in cache_dictionary and cache_dictionary[str(self)]["T"]>self.modtime:
                try:
                    for f in cache_dictionary[str(self)]["I"]:
                        cache_time = cache_dictionary[f]["T"]
                        if f in header_files:
                            file_time = header_files[f].modtime
                        else:
                            if os.path.exists(f):
                                file_time = os.path.getmtime(f)
                            else: file_time = cache_time + 500 #File doenst exist anymore!
                        if cache_time < file_time:
                            return True
                except KeyError:
                    #Catch KeyError on ["I"], happens if a source file is included as header, and a file
                    #that includes it gets an earlier run than the sourcefile itself, so only ["T"] is written, ["I"] not yet
                    return True
            else:
                return True
            return False
        
        #searches for neccessary files using the preprocessor. Returns those in a list. Uses a caching system
        def fill_includes(self):
            global procTime
            inc_ret_list = [] #Files this file includes, as list of File objects
            inc_cache_list = [] #Files this file includes, as list of abs Paths. Used as cache

            all_inc_paths = [] #Paths to pe included with -I
            inc_path_reasons = []

            #Takes a list of paths, puts them into inc_ret_list as files, and inc_cache_list as strings, if they need to be included manually
            #also add them to header_files if they aren't already
            def combine(il):
                for i in il:
                    #catch Headerfiles included manually with -I in C-/CPPFLAGS
                    if i.startswith(top_level_dir):
                        if i in header_files:
                                #Catch self-including files (.file in ASM), dont't do anything with them agian, might lead to loops
                                if i == str(self):
                                    continue
                                inc_ret_list.append(header_files[i])
                                inc_cache_list.append(str(header_files[i]))
                        else:
                            fs = os.path.split(i) 
                            file = self.project.File(self.project, fs[1], fs[0], str(self))
                            #If "i" is a src file, it was either from this project /and/or in a neutral folder (from another project)
                            #So here it is being marked as a header file
                            header_files[i] = file
                            if i in neutral_files:
                                neutral_files.remove(i)
                            if i != str(self):
                                inc_ret_list.append(file)
                                inc_cache_list.append(str(file))

            #Caching mechanism
            outdated = self.is_outdated()
            if not outdated:
                self.include_string = cache_dictionary[str(self)]["S"]
                combine(cache_dictionary[str(self)]["I"])
                cache_dictionary[str(self)] = {"T": time.time(), "I":inc_cache_list, "S": self.include_string}
                return inc_ret_list

            c_f = self.project.get_comp_flags(self)

            #Runs the preprocessor with a command that makes it return the first Headerfile that wasn't found
            def runPrepro():
                global procTime
                ttt = time.time()
                cmd = "echo | "+c_f[0]+" -E -MM -Wno-everything " + str(self) + self.include_string+ " "+c_f[1]
                tmp = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                procTime+=time.time()-ttt
                return tmp
                
            output = runPrepro()

            #While the preprocessor has files it doesn't find
            while output.stderr:
                error_list = error_match.findall(output.stderr)
                if len(error_list) > 0 :
                    looking_for_file = error_match.findall(output.stderr)[0]
                else:
                    print(error_string+"IN PREPRO. PARSING:")
                    print(output.stderr)
                    print("When trying to parse file:")
                    print(self)
                    print("Maybe there is something wrong with the Flags, or missing global dependencies?")
                    exit()

                if looking_for_file[0] == '\'' or looking_for_file[0] == '"':
                    looking_for_file = looking_for_file[1:-1]
                
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
                
                non_target_matches = [] #List of Files that weren't choosen because a fitting file in a target folder was present, only used for user printout
                choosen_file = None #Placeholder for the include file object {"f": File, "named_as":"filename", "is_raw":False}
                mark_as_choosen = False #Save the users answero to weather or not include a found file by default, if it is needed again
                #check if looking_for_file has a default include set, so the user wouldn't need to be asked again
                #if this is the case, it is
                
                #If we ansered that question already and wanted to include that file for every file that asks for it, do so
                if looking_for_file in default_include_choices:
                    choosen_file = default_include_choices[looking_for_file]                   

                #Look for files in already already listed header files, and the whole project
                else:
                    possible_files = [] #list of matches, either {"f":File, "is_raw":False} or {"f":"path/name", "is_raw":True}
                    excluded_matches = [] #Files that would match, but are in an excluded directory

                    #searches for looking_for_file, puts results in possible_files/excluded_matches
                    def find_anywhere():
                        if os.path.splitext(looking_for_file)[1] in self.project.header_fileendings:
                            #try to find match in header_files
                            for path_name, existing_file in header_files.items():
                                if filesearch.findall(path_name): #if a listed header file matches the we are looking for
                                    possible_files.append({"f":existing_file, "is_raw":False}) #save it as possibility
                        else:
                            #try to match in src_files
                            for file in self.project.src_files.values():
                                if filesearch.findall(str(file)):
                                    possible_files.append({"f":file, "is_raw":False})
                        #try to match in neutral_files
                        for abs_path in neutral_files:
                            if filesearch.findall(abs_path):
                                possible_files.append({"f":abs_path, "is_raw":True})

                        #The following code would enable src_files to be found as headers from different projects
                        #This has lots of side-effects, and would probably break many things, so it's disabled for now

                        #If there is no match, try to find it in all projects src_files
                        #if possible_files == [] and os.path.splitext(looking_for_file)[1] in self.project.src_fileendings:
                        #    for prj in all_projs:
                        #        for file in prj.src_files.values():
                        #            if filesearch.findall(str(file)):
                        #                possible_files.append({"f":file, "is_raw":False, "is_src_from_other"})

                        #try to match in excluded_files
                        for abs_path in excluded_files:
                            if filesearch.findall(abs_path):
                                excluded_matches.append(abs_path)

                    find_anywhere()
                    
                    #ERROR: No matching files were found anywhere
                    if len(possible_files) == 0:
                        print(error_string+"File", looking_for_file,"is required by",self, "but no file like this is in this project")
                        if len(excluded_matches) > 0:
                            print("Matches were found in excluded folders:")
                            for p in excluded_matches:
                                print(p)

                        #Search just for a file with the correct name, if one is found tell the user about it
                        #but dont keep going
                        if os.path.split(looking_for_file)[1] != looking_for_file:
                            looking_for_file = os.path.split(looking_for_file)[1]
                            filesearch = re.compile("/"+looking_for_file+'\s*$')
                            possible_files = []
                            find_anywhere()
                            if len(possible_files) + len(excluded_matches) > 0:
                                possible_files.extend(excluded_matches)
                                print("Found files matching the required name, but in non-matching folder:")
                                for p in possible_files:
                                    print(str(p["f"]))
                        
                        #If looking_for_file is a src file, look for that with no guards as well
                        if os.path.splitext(looking_for_file)[1] in self.project.src_fileendings:
                            all_files = []
                            for prj in all_projs:
                                all_files.extend(prj.entry_files)
                            res = find_file_locations(os.path.split(looking_for_file)[1], all_files)
                            if res != []:
                                print("Found the following files with matching names from different projects. Including src-Files from other project is only supported if the source file is in a neutral folder.")
                                for p in res:
                                    print(str(p))
                                print("Consider putting them in a neutral folder, or making them Header-Files.")
                        exit()

                    #Check how many matches there are in entry folders of this project
                    num_files_in_this_proj = 0
                    single_matching_file_in_target = None
                    
                    for f in possible_files:
                        pt = f["f"] if f["is_raw"] else f["f"].path
                        #Prefer Header files from the same project
                        if pt.startswith(self.project.main_directory) and not is_path_in_any_dir(pt, self.project.subproject_dirs):
                            num_files_in_this_proj+=1
                            single_matching_file_in_target = f
                        else:
                            non_target_matches.append(str(f["f"]))

                    #Check if multiple options aren't actually the same ones in real path
                    real_path_possibilities = set()
                    to_rmv = []
                    i = 0
                    for p in possible_files:
                        if p["is_raw"]:
                            rp = os.path.realpath(p["f"])
                        else:
                            rp = os.path.realpath(str(p["f"]))
                        if rp not in real_path_possibilities:
                            real_path_possibilities.add(rp)
                        else:
                            to_rmv.append(i)
                        i+=1
                    for i in to_rmv[::-1]:
                        del possible_files[i]
                                    

                    #If it is only one, take that one                    
                    if num_files_in_this_proj == 1:
                        choosen_file = single_matching_file_in_target

                    #There was one matching header file found in Neutral Files, taking that one
                    elif len(possible_files) == 1:
                        choosen_file = possible_files[0]
                    
                    else:
                        #Multiple option were found in target folders and/or neutral_files, asking user what to do
                        print(interaction_required_string+"File ",rel_to_top(str(self)),"requires",looking_for_file,". Multiple possibilities were found:")
                        ii = 0
                        for pf in possible_files:
                            print(color.BOLD+"(" + str(ii) +") " + rel_to_top(str(pf["f"]))+color.END)
                            if not pf["is_raw"]:
                                print("\tAlso in use by "+pf["f"].reason)
                            ii+=1

                        inp = -1
                        while inp > len(possible_files)-1 or inp < 0:
                            try:
                                inp = int(xinput("Press the preceding index to include the file\n"))  
                            except ValueError:
                                inp = -1  

                        choosen_file = possible_files[inp]

                        #Ask the user wether to use this file by default in the future or not
                        inp = "i"
                        while inp.capitalize() != "Y" and inp.capitalize() != "N":
                            inp = xinput("Include this file automatically for other files requiring " + str(looking_for_file)+"? y/n\n")
                        if inp.capitalize() == "Y":
                            mark_as_choosen = True

                #Adding the file
                f = choosen_file["f"]
                ipath = abspath_to_relpath(f, looking_for_file)
                self.include_string+=" -I "+ ipath

                #Meta-include ambiguity detection data collection
                all_inc_paths.append(ipath)
                inc_path_reasons.append(looking_for_file)
                
                if mark_as_choosen:
                    default_include_choices[looking_for_file] = {"f":f, "named_as": looking_for_file, "is_raw":False}

                #Print info about ignored None-Target Matches, if there are any
                if args.verbose and len(non_target_matches) > 0 and num_files_in_this_proj == 1:
                    print("Ignored None-Target matches for needed file",looking_for_file, "as file in target folder", choosen_file["f"], "was found:")
                    for nm in non_target_matches:
                        print("\t"+nm)

                output = runPrepro()

            inc_list = output.stdout.split() #List of all files that self includes
            
            if len(inc_list) == 2:
                inc_list = []
            elif len(inc_list) >= 3:
                inc_list = inc_list[3:]
            inc_list = [i for i in inc_list if i != "\\" and i != "//"]
            if len(inc_list) > 0:
                combine(inc_list)

            #Check for dual namings
            include_statements = [] #list of include statements, tuple ("how/file/was/called.h", "path/to/file/where/include/happened/", "callingfile.h")
            for a in check_include_duality(str(self), inc_list):
                include_statements.append((a, self.path, self.name))
            for i_f in inc_list:
                sp = os.path.split(i_f)
                for a in check_include_duality(i_f, inc_list):
                    include_statements.append((a, sp[0], sp[1]))

            #gcc gives 1. priority to adjacent files of the one calling for #include, then -I directories in order of apperance
            #search through all statements and include-paths to see if multiple matches exist
            for i_s in include_statements:
                existing_files = [] #list of files matching an #include stattement, 
                    #type: tuple: (path/to/matching/file.h, includepath/via/which/it/was/found, why that path is present, index in inc_path_list)
                #get match for file adjacent to called
                lp = os.path.join(i_s[1], i_s[0])
                if os.path.isfile(lp):
                    existing_files.append((lp, i_s[1], "self", -1))
                #match in include dirs
                for i in range(len(all_inc_paths)):
                    i_p = all_inc_paths[i]
                    pt = os.path.join(i_p, i_s[0])
                    if os.path.isfile(pt):
                        #dont count files with the same realpath twice
                        same_realpath_already_present = False
                        for ef in existing_files:
                            if os.path.realpath(ef[0]) == os.path.realpath(pt):
                                same_realpath_already_present = True
                                break
                        if not same_realpath_already_present:
                            existing_files.append((pt, i_p, inc_path_reasons[i], i))

                if len(existing_files) == 0:
                    vprint(warning_string+"Can't find manually included file", lp, "in this project, can't check for dual includes.")
                elif len(existing_files) > 1:
                    print(warning_string+"Multiple files of the name", i_s[0],"included for file", os.path.join(i_s[1], i_s[2]),":")
                    i = 0
                    for e in existing_files:
                        print(color.BOLD+"("+str(i)+") ",e[0]+color.END)
                        print("\tIncluded via path", e[1])
                        print("\tPresent because of",e[2], "requirement in", str(self),"\n")
                        i+=1

                    print("File", existing_files[0][0], "will take priority according to gcc include rules.")
                    print("Consider adding a more specifying path to your include statements to make them unique")
                    xinput("Press Enter to accept this\n")

            #Save cache for main
            t_n = time.time()
            cache_dictionary[str(self)] = {"T": t_n, "I":inc_cache_list, "S": self.include_string}
            for f in inc_ret_list:
                #Headerfiles only need a time, since they aren't getting compiled on their own
                #But we dont want to erase any info that might be there, since inc_ret_list also may contain included src-files..
                #So this check is neccessary
                if str(f) in cache_dictionary:
                    cache_dictionary[str(f)]["T"] = t_n
                else:
                    cache_dictionary[str(f)] = {"T": t_n}

            return inc_ret_list
    #endregion

    #returns the needed compiler/falgs for a given file
    def get_comp_flags(self, f):
        if f.ext in self.srcC_fileendings:
            comp = self.ccomp
            flags = " "+ (" ".join(self.cflags))
        elif f.ext in self.srcCpp_fileendings or f.ext in self.header_fileendings:
            comp = self.cppcomp
            flags = " "+ (" ".join(self.cppflags))
        else:
            print("It feels wrong to not have an 'else'-part for this. This will never run. If it does, something has gone TERRIBLY wrong")
            exit()
        return(comp, flags)

    def presort(self):
        #walk down the directory tree, marking every target folder in entries, excluded folder in excludes
        #and adding files with interesing extensions to entry_files/neutral_files/excluded_files respectively
        #root = current folder
        #dirs = dirs contained in current folder
        #files = files contained in current folder
        for root, dirs, files in os.walk(self.main_directory, topdown=True, followlinks=True):
            ##Ignore hidden folders and build
            if is_irrelevant(root):
                continue
            for f in files:
                pf = os.path.join(root,f) 
                if pf in neutral_files:
                    neutral_files.remove(pf)

            #Get if root is in the current project, or a subproject
            is_from_this_project = not is_path_in_any_dir(root, self.subproject_dirs)
            #Don't deal with other projects files
            if not is_from_this_project:
                continue

            to_exc = False
            #Note excluded files
            if root in self.excludes:
                #Makes the path absolute and checks for interesing extensions
                for fe in files:
                    if os.path.splitext(fe)[1] in self.allowed_fileendings:
                        excluded_files.append(os.path.join(root, fe))
                to_exc = True
                

            if to_exc: #Excludes
                for d in dirs:
                    pt = os.path.join(root, d)
                    #Mark subfolders as excluded too, but only if they aren't entries
                    if pt not in self.entries and pt not in self.manual_neutrals:
                        self.excludes.append(pt)
                continue

            else: #Neutral and Entry 
                #Find subproject declarations
                comp_match = []
                if root != self.main_directory:
                    for f in files:
                        comp_match.extend(comp_reg.findall(f))
                if comp_match: 
                    if root in self.entries:
                        self.subproject_dirs.append(root) #Note found Subprojects
                        continue
                    else:
                        print(interaction_required_string)
                        print("Project found in Neutral Folder", rel_to_top(root), ", ignoring its config file as Projects are only allowed in Entry Locations.")
                        xinput("Press Enter to accept this")

                if root in self.exclude_src_dirs:
                    abs_path_list = [os.path.join(root, fe) for fe in files if os.path.splitext(fe)[1] in self.header_fileendings]
                    precom_abs_path_list = []
                    excluded_files.extend(os.path.join(root, fe) for fe in files if os.path.splitext(fe)[1] in self.src_fileendings)
                    for d in dirs:
                        pt = os.path.join(root, d)
                        if pt not in self.entries and pt not in self.manual_neutrals:
                            self.excludes.append(pt)
                else:  
                    abs_path_list = [os.path.join(root, fe) for fe in files if os.path.splitext(fe)[1] in self.allowed_fileendings]
                    precom_abs_path_list = [os.path.join(root, fe) for fe in files if os.path.splitext(fe)[1] in self.precompiled_fileendings]
                
                #Look for Linkerscripts
                for f in files: 
                    if os.path.splitext(f)[1] in self.linkerscript_fileendings:
                        self.found_linkersscripts.append(os.path.join(root, f))

                if root in self.entries: #Entry
                    for prec in precom_abs_path_list:
                        if prec in excluded_files:
                            continue
                        self.private_precomps+=prec+" "
                        if not root.startswith(tuple(self.non_propageted_dirs)):
                            self.public_precomps+=prec+" "

                    self.entry_files.extend(abs_path_list)
                    for d in dirs:
                        #... add the subfolders as Entries too, if they aren't excluded
                        pt = os.path.join(root, d)
                        
                        if pt not in self.excludes and pt not in self.manual_neutrals and not is_irrelevant(pt):
                            self.entries.append(os.path.join(root, d))
                            
                else: #Neutral
                    if precom_abs_path_list:
                        vprint("Found precompiled files in Neutral Folder", root)
                        vprint("To include them put them in an Entry Folder")
                    for ap in abs_path_list:   
                        neutral_files.add(ap)
        all_proj_dirs_with_links.extend(self.subproject_dirs)


        #Remove extra Dirs from excludes
        for d in self.extra_folders:
            p = os.path.join(self.main_directory, d)
            if p in self.excludes:
                self.excludes.remove(p)
            if p in self.manual_neutrals:
                self.manual_neutrals.remove(p)

        #Check if there are lib-folders with the same name
        lib_names = {}
        for L in self.lib_dirs:
            name = os.path.split(L)[1]
            if name not in lib_names:
                lib_names[name] = L
            else:
                print(error_string+"Library with name '", name,"' is defined twice:")
                print(L)
                print(lib_names[name])
                exit()

        #Check if defined Lib isn't actually part of a subproject
        for L in self.lib_dirs:
            is_from_this_project = True
            for s_d in self.subproject_dirs:
                if L.startswith(s_d):
                    is_from_this_project = False
                    break
            if not is_from_this_project:
                print(error_string+"Defined Library",L, "is located in a subproject")
                print("If you want to compile a subproject as a Lib, define that in its comp.toml file")
                exit()

        #Create folders to put the obj that will be bundeled into a lib into
        if len(self.lib_dirs) > 0:
            for L in self.lib_dirs:
                name = os.path.split(L)[1]
                os.mkdir(os.path.join(build_dir,self.build_subdir, name))

        #Check if lib-folders are in entries:
        for L in self.lib_dirs:
            if L not in self.entries:
                print(error_string+"Folder", L, "is not in an Entry Path, so it can not be bundled into a Library")
                exit()

        #Add extra Files to compilation
        all_taken_by_argument = []
        for j in self.extra_files:
            arg_search = re.compile("/"+j+'\s*$')
            how_many_added_counter = 0
            just_added = []
            for Lp in (excluded_files, neutral_files):  
                for p in Lp:
                    i = arg_search.findall(p)
                    if i:
                        how_many_added_counter += 1
                        all_taken_by_argument.append(p)
                        just_added.append(p)
                        if p in excluded_files:
                            excluded_files.remove(p)
            #Check if only one File per Cmd was added
            if how_many_added_counter == 0:
                print(error_string+"No files matching extra file",j,"found")
                exit()
            if how_many_added_counter > 1:
                print(error_string+"Multiple files found for Extra definition", j)
                exit()

        #Fill src_files and header_files with all src/header files(as File objects) in entry folders, as well as -F arguments
        interessting_files = self.entry_files
        interessting_files.extend(all_taken_by_argument)
        for raw_file in interessting_files:
            sp = os.path.split(raw_file)
            path = sp[0]
            name = sp[1]
            f = self.File(self, name, path, "Build target "+path)
            if raw_file in neutral_files:
                    neutral_files.remove(raw_file)
            if raw_file in excluded_files:
                continue

            if f.ext in self.src_fileendings and raw_file:
                if f.name_no_ext in self.src_files:
                    print(error_string+"File", name, "defined twice: ")
                    print(raw_file, "\n\tRequired by Build target "+path)
                    print(self.src_files[f.name_no_ext], "\n\tRequired by "+self.src_files[f.name_no_ext].reason)
                    exit()
                self.src_files[f.name_no_ext] = f
                

            elif f.ext in self.header_fileendings:
                if name in header_files:
                    print(error_string+"multiple definition of Header file")
                    print("This is impossible, header_files is keyed by absolute paths")
                    exit()
                n = os.path.join(sp[0], name)
                header_files[n] = f

        #Check if there is overlapp between Entry and Excludes
        ens = set(self.entries)
        exs = set(self.excludes)
        mns = set(self.manual_neutrals)
        overlap = ens & exs
        overlap.union(ens & mns)
        overlap.union(exs & mns)
        if overlap:
            print(error_string+"Overlap between Neutral, Excludes and Entry definitions:", overlap)
            exit()
        comp_match = []

        #Subproject found! Compiling that first with its definitions
        for root in self.subproject_dirs:
            #Find all comp.toml files
            comp_match = []
            for f in os.listdir(root):
                comp_match.extend(comp_reg.findall(f))

            #Read cache for prev usage
            file_to_use = ""
            found_cache = False
            if root in subproject_usage_cache:
                file_to_use = subproject_usage_cache[root]
                found_cache = True

            if file_to_use == "IGNORE THIS PROJECT":
                vprint("Not using project at", root)
                continue

            #Use config file def. if cache is unavaivable
            if file_to_use == "":
                file_to_use = self.subproject_config_to_use

            #No cache and no matching config file, ask the user what to do
            if not found_cache and file_to_use not in comp_match:
                print(interaction_required_string+" The config file", file_to_use,"was expected to be present in", rel_to_top(root))
                inp = "j"
                while inp.capitalize() != "Y" and inp.capitalize() != "N":
                    inp = xinput("Proceed with present config file/s, or ignore this Project? (y to proceed/n to ignore this project)\n")
                #He wants to ignore it
                if inp.capitalize() != "Y":
                    print("Okay, proceeding to handle this folder like an excluded folder")
                    self.excludes.append(root)
                    for nroot, dirs, files in os.walk(root, topdown=True, followlinks=False):
                        for fe in files:
                            if os.path.splitext(fe)[1] in self.allowed_fileendings:
                                excluded_files.append(os.path.join(nroot, fe))
                    subproject_usage_cache[root] = "IGNORE THIS PROJECT"
                    continue
                #He wants to choose which file to use
                else:
                    inp = -1
                    print("Which config file to use?")
                    i = 0
                    for cm in comp_match:
                        print(color.BOLD+"("+str(i)+") "+cm+color.END)
                        i+=1
                    while inp > len(comp_match)-1 or inp < 0:
                        try:
                            inp = int(xinput("Press the preceding index to use the file\n"))  
                        except ValueError:
                            inp = -1  
                    file_to_use = comp_match[inp]
                    subproject_usage_cache[root] = file_to_use

            #Init the project
            proj_exists_already = False
            for pr in all_projs:
                if os.path.realpath(pr.main_directory) == os.path.realpath(root) and pr.config_file_to_use == file_to_use:
                    proj_exists_already = True
            if not proj_exists_already:
                subproj = Project(root, file_to_use, inherited_definitions=self.to_be_inherited_definitions)
                all_projs.append(subproj)
                subproj.presort()
        
        
    def search(self, src_additions = []):
        vprint("Searching in project", self.main_directory)
        #initialize search for ressources
        s_t = time.time()

        if src_additions == []:
            src_additions = list(self.src_files.values()) #all added src files as list
        all_additions = src_additions.copy() #all added files as list
        
        #Debug info
        counter = 0

        #Add files that should be taken as src_files, which were previously not used
        def add_file_to_search(newfile):
            #It was from self project
            if newfile.path.startswith(self.main_directory):
                needed_src[str(file)] = str(newfile)
                if str(file) in neutral_files:
                    neutral_files.remove(str(file))
                self.src_files[newfile.name_no_ext] = newfile
                new_src_additions.append(newfile)

            #File was neutral in another Project..
            else:
                #Figure out which subproj it was
                subpro_match_dir = is_path_in_any_dir(newfile, all_proj_dirs_with_links)
                proj_match = ""
                for prj in all_projs:
                    if subpro_match_dir == prj.main_directory:
                        proj_match = prj
                        break
                if proj_match == "":
                    print(error_string+"Project does not match any project")
                    print("something has gone terribly wrong, this should never happen")
                    exit()
                #Add it there and fill its includes
                proj_match.src_files[newfile.name_no_ext] = newfile
                neutral_files.remove(newfile)
                proj_match.search(src_additions = [newfile]) 

        while all_additions: #run this until we dont add any more ressources
            counter+=1
            vprint("Iteration", counter)

            #find all missing header files for src+header files 
            new_header_additions = []
            #Only go through src_additions to ignore unneded Header Files
            for file in src_additions:
                included_files = file.fill_includes()
                for i_f in included_files:
                    if i_f not in checked_header_files:
                        checked_header_files.append(i_f) #don't compute a file mutiple times
                        new_header_additions.append(i_f) #only check them in the next run, not any other following ones
                

            new_src_additions = [] #list of new Files added as src files

            #look if there is a src file for a header we dont have in src yet
            for file in new_header_additions:
                if file.ext in self.src_fileendings:
                    #Oh no, someone is including a src file like a Header
                    #Don't turn this File into an obj then
                    if file.path in self.excludes:
                        print(error_string+"File", file.reason, "requires", file, "which is in an excluded Folder")
                        exit()
                    if file.name_no_ext in self.src_files:
                        del self.src_files[file.name_no_ext]
                    new_src_additions.append(file)
                    continue

                if file.name_no_ext in self.src_files: 
                    #Found one sourcefile already present in a target folder for this header, all is well
                    continue

                #use needed_src cache
                if str(file) in needed_src:
                    if str(file) in cache_dictionary and cache_dictionary[str(file)]["T"] > file.modtime:
                        if needed_src[str(file)]:
                            sp = os.path.split(needed_src[str(file)])
                            path = sp[0]
                            name = sp[1]
                            newfile = self.File(self, name, path, str(file)) #this source file shall be included into the compilation
                            add_file_to_search(newfile)
                        continue

                matches = [] #list of all matching FILES
                excluded_matches = [] #List of paths that were excluded due tu exclude folder definitions
                #search neutral files if nothing was found in entry dirs
                for ext in self.src_fileendings:        
                    srcname = file.name_no_ext+ext
                    found_file_locs = find_file_locations(srcname, neutral_files)
                    for ff in found_file_locs:
                        #Create a File object and add it to possible matches
                        matches.append(self.File(self, srcname, ff, file))
                    excluded_matches.extend(find_file_locations(srcname, excluded_files))

                if matches:
                    #Found source files in entry_files, ask the user if/which one to include
                    print(interaction_required_string+"Following source files for header file", rel_to_top(str(file)), "with matching names were found")          
                    i = 0
                    for match in matches:
                        print(color.BOLD+"("+str(i)+") ",rel_to_top(str(match))+color.END)
                        i+=1
                    inp = -1
                    
                    while inp > len(matches)-1 or inp < 0:
                        k = xinput("Press the preceding index to include the file, x for none\n")
                        try:
                            if k == 'x':
                                break
                            inp = int(k)
                        except ValueError:
                            inp = -1 
            
                    if k == 'x':
                        print("Okay, not adding a source file for", file.name)
                        needed_src[str(file)] = False
                    else:
                        print("Adding", matches[inp], "to compilation")
                        #File is from this project
                        add_file_to_search(matches[inp])

                else:
                    needed_src[str(file)] = False
                    if args.verbose:
                        print("No matching source files were found for", file.name)
                        if len(excluded_matches)>0:
                            print("Matches were found in excluded Folder:")
                            for p in excluded_matches:
                                print(p)

            #Update additions to handle these ones in the next round
            #Not 100% sure if .copy is necessary, but we have plenty of memory to spare
            src_additions = new_src_additions.copy()
            all_additions = new_header_additions.copy()
            all_additions.extend(src_additions)

        vprint("Iterative include-search done in",time.time()-s_t,"s")   
  
    def compile(self):
        print(color.BOLD+"Compiling Object-files"+color.END+ " for project", rel_to_top(self.main_directory))
        s_t = time.time()

        def gen_o(src_files_slice):
            #This is multithreaded! But no writing to shared variables happens here, and no Files in src_files_slice
            #Are handeled by 2 Threads (ensured by get_chunks())
            for f in src_files_slice:
                comp, flags = self.get_comp_flags(f)
                include_string = f.include_string
                if f.compiled_to_lib_folder:
                    dir = os.path.join(build_dir, self.build_subdir, f.compiled_to_lib_folder)
                else:
                    if f.path.startswith(tuple(self.non_propageted_dirs)):
                        dir = os.path.join(build_dir, self.build_subdir, "non_prop")
                    else:                       
                        dir = os.path.join(build_dir, self.build_subdir, "obj")
                cmd = comp +" "+include_string + " -c "+os.path.join(f.path, f.name) + " -o " + os.path.join(dir, f.name_no_ext+".o")+flags+os.linesep
                if args.print_commands:
                    print(cmd)
                output = subprocess.run(cmd, shell=True)

        to_comp = chunks(list(self.src_files.values()),args.thread_num) #Split source files to compile into N lists
        threads = []
        for i in range(args.thread_num): #Start N threads, each working on compiling a list
            thread = Thread(target = gen_o, args = (to_comp[i],))
            threads.append(thread)
            thread.start()
        for i in range(args.thread_num): #Wait for them all to finish
            threads[i].join()

        vprint("Done in",time.time()-s_t,"s")

        #Bundle each declared library into a .a
        for L in self.lib_dirs:
            name = os.path.split(L)[1]
            print(color.BOLD+"Bundeling Libray"+color.END, name)
            cmd = self.ar+" rc "+os.path.join(build_dir,self.build_subdir,"lib", name+".a")+" "+os.path.join(build_dir,self.build_subdir, name,"*")
            if args.print_commands:
                print(cmd)
            subprocess.run(cmd, shell=True)
        
def link():
    s_t = time.time()    

    for subpro in all_projs:
        if not subpro.generate_executable:
            continue
        print(color.BOLD+"Linking Executable "+color.END+"for", rel_to_top(subpro.main_directory))

        linker_string = ""
        #region------CHECK LINKERSCRIPT TO USE-----
        if subpro.found_linkersscripts:
            #Check if info of which script to use is already in the cache
            nocachefound = False
            if subpro.main_directory in linkerscript_cache:
                if linkerscript_cache[subpro.main_directory] == "X":
                    i = 0
                elif linkerscript_cache[subpro.main_directory] in subpro.found_linkersscripts:
                    linker_string = "-T "+linkerscript_cache[subpro.main_directory]+" "
                else:
                    nocachefound = True
            else:
                nocachefound = True
            
            if nocachefound:
                print(interaction_required_string+"Linkerscript found!")
                i = 0
                for l in subpro.found_linkersscripts:
                    print("("+rel_to_top(str(i))+") "+l)
                    i+=1

                inp = -1
                while inp > len(subpro.found_linkersscripts)-1 or inp < 0:
                    k = xinput("Press the precceding index for which Linkerscript to use, x for none\n")
                    try:
                        if k == 'x':
                            break
                        inp = int(k)
                    except ValueError:
                        inp = -1 
                if k == 'x':
                    print("Not using any")
                    linkerscript_cache[subpro.main_directory] = "X"
                else:
                    linker_string = "-T "+subpro.found_linkersscripts[inp]+" "
                    linkerscript_cache[subpro.main_directory] = subpro.found_linkersscripts[inp]
        else:
            linkerscript_cache[subpro.main_directory] = "N"
        #endregion

        all_os_locs_list = [] #List of folders to take all the .obj files out of
        #Take non_propageted files only from the project that we are looking at right now
        loc = os.path.join(build_dir, subpro.build_subdir, "non_prop")
        if os.listdir(loc):
            all_os_locs_list.append(loc)

        mutual_os_locs_list = []
        libs = "" #Libraries string 

        #Add /obj, /lib Folders and precompiled filse of all relevant projects
        subproj_real_subprojdirs = [os.path.realpath(x) for x in subpro.subproject_dirs]
        for sd in all_projs:
            if sd.main_directory == subpro.main_directory:
                continue
            if sd.only_link_with_direct_parent and os.path.realpath(sd.main_directory) not in subproj_real_subprojdirs:
                continue
            
            loc = os.path.join(build_dir, sd.build_subdir, "obj")
            if os.listdir(loc):
                mutual_os_locs_list.append(loc)

            for L in os.listdir(os.path.join(build_dir, sd.build_subdir, "lib")):
                libs += os.path.join(build_dir, sd.build_subdir, "lib", os.path.split(L)[1])+" "
            
            libs += sd.public_precomps+" "

            #linker_string += " " + (" ".join(sd.linkerflags))
        
        all_os_locs_list.extend(mutual_os_locs_list)

        all_os = "" #String from list
        for o in all_os_locs_list:
            all_os += o + os.path.sep + "* "

        precomp_string = libs + subpro.private_precomps
        cmd = subpro.linker+" "+all_os+precomp_string+linker_string+(" ".join(subpro.linkerflags))
        if args.print_commands:
            print(cmd)
        subprocess.run(cmd, shell=True)
        vprint("Done in",time.time()-s_t,"s")

    #Write Cache
    cache_dictionary["NEEDED_SRC_FILES_SUBCACHE"] = needed_src
    cache_dictionary["SUBPROJ"] = subproject_usage_cache
    cache_dictionary["LINKERSCRIPT_TO_USE"] = linkerscript_cache
    output_file_name = os.path.join(build_dir,"comp_cache")
    with open(output_file_name, "w") as toml_file:
        toml.dump(cache_dictionary, toml_file)

    vprint("Total time",time.time()-t0,"s")

if os.path.exists(default_config_path):
    default_config_definitions = read_definitions(default_config_path)
if not default_config_definitions:
    vprint("No default config file found")

top_level_config_file = args.config
if not os.path.exists(os.path.join(top_level_dir, top_level_config_file)):
    print("Needed config-file", args.config,"not found.")
    #Find all comp.toml files
    comp_match = []
    for f in os.listdir(top_level_dir):
        comp_match.extend(comp_reg.findall(f))
    if comp_match == []:
        print("No config file found for project top-Level project, using defaults")
    else:
        inp = -1
        print("Which config file to use?")
        i = 0
        for cm in comp_match:
            print(color.BOLD+"("+str(i)+") "+cm+color.END)
            i+=1
        while inp > len(comp_match)-1 or inp < 0:
            try:
                inp = int(xinput("Press the preceding index to use the file\n"))  
            except ValueError:
                inp = -1  
        top_level_config_file = comp_match[inp]

top_level = Project(is_top_level=True, ftu=top_level_config_file)
all_projs.append(top_level)
all_proj_dirs_with_links.append(top_level.main_directory)

top_level.presort() #Presort finds subprojects, adds them to all_projs, and calls presort() on them

if args.print_structure:
    tmp_en = []
    tmp_n = []
    tmp_ex = []
    for p in all_projs:
        tmp_en.extend(p.entries)
        tmp_n.extend(p.manual_neutrals)
        tmp_ex.extend(p.excludes)
    for root, dirs, files in os.walk(top_level.main_directory, topdown=True, followlinks=True):
        ##Ignore hidden folders and build
        if is_irrelevant(root):
            continue
        
        offset = "-" * (root.count(os.path.sep)-top_level.main_directory.count(os.path.sep))
        if root in tmp_n:
            print(color.END,offset+root)
        elif root in tmp_en:
            prefix = ""
            suffix = ""
            if root in all_proj_dirs_with_links:
                prefix = color.BOLD
                suffix = " (P)"
            print(color.GREEN+prefix,offset+root+color.END+suffix)
        elif root in tmp_ex:
            print(color.RED,offset+root,color.END)
        else:
            print(color.END,offset+root,color.END)
    print(color.END)
        
print(color.BOLD+"Starting iterative search"+color.END)
for p in all_projs:
    p.search()

for p in all_projs:
    p.compile()

link()

vprint("Time spent waiting for preprocessor:",procTime,"s")
exit()
