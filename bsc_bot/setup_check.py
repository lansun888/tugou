import sys
import importlib
import os

def check_dependency(package_name, import_name=None):
    if import_name is None:
        import_name = package_name
    try:
        importlib.import_module(import_name)
        print(f"✅ {package_name} (imported as {import_name}) - 安装成功")
        return True
    except ImportError as e:
        print(f"❌ {package_name} (imported as {import_name}) - 安装失败: {e}")
        return False

def main():
    print("正在检查 BSC 自动交易机器人环境依赖...\n")
    
    dependencies = [
        ("web3", "web3"),
        ("websockets", "websockets"),
        ("aiohttp", "aiohttp"),
        ("redis", "redis"),
        ("loguru", "loguru"),
        ("python-telegram-bot", "telegram"),
        ("sqlalchemy", "sqlalchemy"),
        ("aiosqlite", "aiosqlite"),
        ("pydantic", "pydantic"),
        ("python-dotenv", "dotenv"),
        ("requests", "requests"),
        ("PyYAML", "yaml"),
        ("asyncio", "asyncio")  # built-in but good to check
    ]
    
    all_pass = True
    for package, module in dependencies:
        if not check_dependency(package, module):
            all_pass = False
            
    print("\n" + "="*50)
    if all_pass:
        print("🎉 所有依赖检查通过！环境搭建成功！")
        print(f"当前 Python 版本: {sys.version.split()[0]}")
        print(f"虚拟环境路径: {sys.prefix}")
    else:
        print("⚠️ 部分依赖缺失，请检查安装步骤。")
        print("尝试运行: pip install -r requirements.txt (如果有的话) 或者手动安装缺失的包")

if __name__ == "__main__":
    main()
