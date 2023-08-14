# Comp4me - a C/C++ build System for small projects

## Dependencies:
* python3
* pip install toml
* pip install argparse
* any C/C++ compiler, tested with clang/clang++ , gcc/g++/c++
* ar - command must be avaivable (Is installed by default on all Linux Distros)

optional Dependencies:
    ccache - command needs to be avaivable, for Linux just download the bin (https://ccache.dev/download.html),
        extract it, and place it in /bin

## Take a look at the wiki!
The [wiki](https://github.com/Simonrazer/comp4me/wiki/Quickstart) has a quick-start layed out. This Readme is soon to be replaced.

## Usage:
Place this tool's files anywhere you like, but don't sepperate the present files. You may create Symbolic Links to the folder containing them.
With the terminal placed at the top of your project, run 

    path/to/comp4me.py ARGS
    
Where `ARGS` are arguments as described below. If your shell does not recognize `#!` markers, you need to preface this line with `python3`.

comp4me makes default assumptions, so very little configuration is required.
One default assumption is that every Project that generates an executable by default ignores top-level folders whose names end with "test". For reasons stated later, it is not advised to use files with Source-File endings (.c, .cpp..) as Header Files.

Confguration can be done via configration files ending in "comp.toml" placed at the top of the project, and via Command Lina Arguments. The default configuration-file for a project shall be called "comp.toml". Additionally, a global default configuration file named "default.toml" may be placed next to comp4me.py, containing the same definitions as seen later. Global default will be overridden by project Configuration files. If no configuration-file was found only default assumptions will be used. 

By default, all folders except the previously stated exception will be compiled.

By definig `ENTRYPOINT = ["path/to/srcs","...]` in a configuration-file you may override that behaviour, so only the given paths and their subdirectories will be compiled.\
Source-Files with the same name are not allowed to be compiled in a single project.\
Folders can be totally excluded from Compilation via `EXCLUDES = ["paths/to/scrs", "...]`\
By defining `EXCLUDES` the files folders excluded by default (top-level folders ending in "test") will no longer be ignored. If you still want them to be, you have to manually name them to the list.\
Folders that aren't defined as or are not subfolders of `EXCLUDED` or `ENTRYPOINT` folders are treated as `NEUTRAL` folders:
* Files in NEUTRAL Folders are only used if they are needed by files placed in `ENTRYPOINT` Folders
* This means Headerfiles placed in `NEUTRAL` Folders will be found
* If a(or multiple) Source file with a matching Name for a Header file in a `NEUTRAL` Folder exists, the User will be asked if(which one) to take into the compilation process as well.


Linkerscripts are being identified by the fileending ".ld". If one or multiple are found in `ENTRYPOINT` folders, the useer fill be asked if/which one should be used.\
Precompiled files are being identified by the fileendings ".a", ".so", ".obj" and ".o". They will used in the linking process for this project if they are found in an `ENTRYPOINT` folder.\
If there is ambiguity which Header-File a File wants to include, the User will be asked.\
If a single matching Header-File exists in a Target Folder, that one wil be used.\

When replacing Files with older Vrsions of them, it is highly recommended to delete the Cache using the `-C` flag. Cache checks are being done useing the last Modification date stored inside the files, so different Content will only be detected when the Modification date has advanced.

It is recommended to use Headerfiles with distinct names, or to include them via distinct relative paths. There may be situations where search paths are being used which lead to the wrong Header-file being included. Using distinct names or relativ paths prevents that. Comp4me will warn the user if such a scenario is taking place.

All avaivable Definitions in comp.toml are:  
* `ENTRYPOINT = ["path/to/srcs","...]`

* `EXCLUDES = ["path/to/srcs","...]`

* `NEUTRALS = ["path/to/srcs","...]`
	- define folders that would actually be in Entrypoint/Excluded Locations as Neutral Folders  
	
* `EXCLUDE_SRC = ["path/to/srcs","...]`
	- define folders where only Headerfiles will be taken from. Otherwise treated as an excluded Folder.
	
* `CCOMP = "x"`
* `CPPCOMP = "x`
	- define the C/C++ compiler to be used, default is gcc/g++, with a fallback to clang/clang++  
	
* `AR = "x"`
	- define the library bundeler to use, defualt is ar  
	
* `LINKER = "x"`
	- define the linker to be used, default is same as CPPCOMP  
	
* `CFLAGS = "xxx.." / CFLAGS = ["xx","xx..]`
* `CPPFLAGS = "xxx.." / CPPFLAGS = ["xx","xx..]`
* `LINKERFLAGS = "xxx.." / LINKERFLAGS = ["xx","xx..]`
	- define the Flags to be used for C/C++ compilation, and at the Linking step. Definition as a String or as a List of strings is valid.  
It is possible to include Flags of one Category in the other, for example:
	CFLAGS = "-m32"
	CPPFLAGS = ["CFLAGS", "-g"]  

* `HEADER = [".hpp", ".x...]`
* `C = [".xx", ".x...]`
* `CPP = [".xx", ".x...]`
	- ammends the default definitions of which File-extensions to treat as which File-Type. Overriding the defaults is not supported.  
	Defaults are:  
	        * srcC_fileendings = {".c", ".s", ".S"}  
	        * srcCpp_fileendings = {".cpp", ".cc"}  
	        * header_fileendings = {".h", ".hpp"}  
	        
* `MAKE_UIC = ["path/to/srcs","...]`
	- list of directories that should be preprocessed with UIC and MOC, which is neccessary for Projects using the QT5 GUI System. MOC and UIC need to be installed.  
	
* `AS_LIB = ["path/to/srcs","...]`
	- list of directories in which the present source files should be bundeled into a library before linking.

Projects can have multiple configuration-files. Their names need to end in "comp.toml". Which one will be used at the top level is determined by the positional Argument when launching the tool.

Subfolders in which a configuration file is present are used as Subprojects. There may also be multiple ones present, which Configuration file is used for subprojects is determined by the `NEXT_CONFIG` Config-Variable of their parent project. If this was not defined, or the declared Configuration-file not present the User will be asked which one to use, or if this project should be ignored.

All Subprojects of the Top-Level Project will be linked to create one Executable, unless specified differently in their configuration files.

Definitions relevant to Subprojects are:

* `NEXT_CONFIG = "x"`
	- defines which comp.toml file should be used if subprojects with multiple files ending in comp.toml are present in a subproject by their name.

* `GENERATE_EXECUTABLE`
	- boolean value (true/false, for top-Level projects by default true, for lower level projects by default false), determines if this project should generate an executable

* `PROPAGATE = ["path/to/srcs","...]`
	- list of folders in this Project that should be linked with other Projects. If GENERATE_EXECUTABLE is true, no folders are shared with other projects, to prevent multiple definitions of main. This Overrides that behaviour for the given directories. Enabeling this for some folders only makes sense if the this project produces an executable.

* `ONLY_LINK_WITH_DIRECT_PARENT = false`
	- boolean value, defines if a project should only be linked with its direct parent project, or be linked with all other projects. Turning this on only makes sense if the direct parent project produces an executable.

* `INHERIT = ["DEFINITION", "...]`
	- list of definitions that avaivable. The set definitions must be set to a value for this project. Enables that the set definitions' values will be inhereted to this project's subprojects. By setting `INHERIT = ["INHERIT", "...]`, the set definitions will be passed on to sub-subprojects, and continuing. If a subprojects changes one of the set definitions, this one will be used, and in case of further inheritance be inherited.

* `GENERATE_TEST`
	- boolean value (true/false, default false), shorthand for
		* GENERATE EXECUTABLE = true
		* PROPAGATE = all of main_directory without main_directory/*test
		* ENTRYPOINT += main_directory/*test
		* NEXT_CONFIG = *the name of this config file* (overrideable by defining NEXT_CONFIG)

Files can include Files from other Projects. Using Source files (.c/.cpp) in #include statements is not advised, as if they are in a different Project, they will only be found if they are not in an Entrypoint location of that project.

Command-Line Arguments are:  

+ *positional*
	- name of the configuration file to use for the top-level project, default is "comp.toml"  
+ `-L rel/path/to/dir`
	- define folders that will each be compiled into an archive before linking, equivalent to AS_LIB = []
+ `--no-ccache`
	- Don't use ccache to speed up compilation via caching
+ `-F filename`
	- Override a specific source-file to not be Excluded, but be treated as if it is in an Entry-Location
+ `-D rel/path/to/dir`
	- Override a specific folder to not be Excluded, and add it to Entry-Locations
+ `-v`
	- verbose output, prints extra information
+ `-p`
	- prints the commands used at each stage during compilation
+ `-C`
	- clear cache
+ `-T number`
	- number of Threads to use for parallel compilation of object files. Has minimal impact of compilation time when using ccache
+ `--print-structure`
	- print a directory tree of all interesting folders, color coded for the role they were given. Red = Excluded, Green = Entrypoint, Green and Bold = Project Folder, White = Neutral
+ `--print-commands`
	- print all the commands used during the compilation process

comp4me will create a build directory at the top of the project. It will be deleted and recreated on every run, so don't put anything in there.


## Example
In `/example` a example Project using comp4me is present. It is using a precompiled objectfile for demonstration purposes. This file is compiled for Linux-x86 Systems. To compile it for your System, just compile `program/some_stuff/crazy.cpp` with the `-o` Flag. Alternatively, move this file into `/program`

This example features an Entrypoint, an Excluded Folder and a precompiled file. A header and a name-matching source file are placed in a Neutral folder. Additionaly, not-used files (`unused_file.cpp`, `stuff.cpp`, `crazy.cpp`) are located here to demonstrate how comp4me reads the structure of your project.
