import sys
import os

# Ensure the src directory is on the path for pytest with src layout
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
