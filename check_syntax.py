#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
简单的语法检查脚本
"""
import sys
import py_compile
import os

files_to_check = [
    "bsc_bot/analyzer/local_simulator.py",
    "bsc_bot/analyzer/security_checker.py"
]

print("=" * 60)
print("Python 语法检查")
print("=" * 60)

all_ok = True
for filepath in files_to_check:
    full_path = os.path.join(os.path.dirname(__file__), filepath)
    print(f"\n检查: {filepath}")
    try:
        py_compile.compile(full_path, doraise=True)
        print(f"  ✓ 语法正确")
    except py_compile.PyCompileError as e:
        print(f"  ✗ 语法错误:")
        print(f"    {e}")
        all_ok = False

print("\n" + "=" * 60)
if all_ok:
    print("✓ 所有文件语法检查通过")
    sys.exit(0)
else:
    print("✗ 存在语法错误")
    sys.exit(1)
