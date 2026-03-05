import sys
import os

print(f"Python executable: {sys.executable}")
print(f"CWD: {os.getcwd()}")
print(f"Path: {sys.path}")

try:
    import fastapi
    print("FastAPI imported successfully")
except ImportError as e:
    print(f"Failed to import fastapi: {e}")

try:
    sys.path.append(os.getcwd())
    import web.api
    print("web.api imported successfully")
except Exception as e:
    print(f"Failed to import web.api: {e}")
