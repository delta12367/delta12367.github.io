"""Prepare the working files from the original obfuscated script.

usage: python prepare.py path/to/ZeusterrEnhancements.txt

writes head.lua (everything before Backend.loadProgramData, needed by boot/lifter/assemble)
and progdata.pkl (the pre-decoded bytecode tables).
"""
import sys, os, pickle
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import luadata


def main():
    src_path = sys.argv[1]
    src = open(src_path, encoding="utf-8").read()
    cut = src.index("function Backend.loadProgramData()")
    with open(os.path.join(HERE, "head.lua"), "w", encoding="utf-8", newline="\n") as f:
        f.write(src[:cut])
    data = luadata.parse(src_path)
    with open(os.path.join(HERE, "progdata.pkl"), "wb") as f:
        pickle.dump(data, f)
    print("head.lua and progdata.pkl written")


if __name__ == "__main__":
    main()
