import os
import sys


# Keep the source package clean while the test suite launches Python subprocesses.
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
