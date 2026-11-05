"""Load the plugin directory as a package (same way Hermes does: spec + submodule_search_locations)."""
import importlib.util, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def load():
    if "citation_guard" in sys.modules:
        return sys.modules["citation_guard"]
    spec = importlib.util.spec_from_file_location("citation_guard", ROOT / "__init__.py",
                                                  submodule_search_locations=[str(ROOT)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["citation_guard"] = mod
    spec.loader.exec_module(mod)
    return mod
