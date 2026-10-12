#!/bin/sh
# usage: run.sh <decompiled.lua>   (from the oracle directory)
set -e
python3 build.py ../data/z.lua "$1"
timeout 3000 ../bin/luau run_orig.luau > orig.txt 2>&1 || true
timeout 3000 ../bin/luau run_dec.luau > dec.txt 2>&1 || true
grep -a -n "HARNESS\|TRACE_END\|MAIN ERROR" orig.txt dec.txt | head
grep -a -v "^COV\|^PATCH" orig.txt > o.tr
grep -a -v "^COV" dec.txt > d.tr
python3 norm.py o.tr d.tr 2>&1 | head -50
