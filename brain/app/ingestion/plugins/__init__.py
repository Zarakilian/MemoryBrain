import importlib
import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

ACTIVE_PLUGINS: list = []
INACTIVE_PLUGINS: list = []


def _scan_plugin_files() -> list:
    """Import all plugin modules in this directory. Skips __init__.py and *_stub.py."""
    plugins_dir = Path(__file__).parent
    modules = []
    for path in sorted(plugins_dir.glob("*.py")):
        if path.name == "__init__.py" or path.name.endswith("_stub.py"):
            continue
        module_name = f"app.ingestion.plugins.{path.stem}"
        try:
            mod = importlib.import_module(module_name)
            modules.append(mod)
        except Exception as e:
            logger.warning(f"Failed to import plugin {path.name}: {e}")
    return modules


async def discover_plugins() -> tuple[list, list]:
    """Discover, health-check, and categorise all plugins. Updates module globals."""
    global ACTIVE_PLUGINS, INACTIVE_PLUGINS
    active = []
    inactive = []

    for plugin in _scan_plugin_files():
        name = getattr(plugin, "MEMORY_TYPE", plugin.__name__)

        # Check required env vars
        required = getattr(plugin, "REQUIRED_ENV", [])
        if any(not os.getenv(var) for var in required):
            missing = [v for v in required if not os.getenv(v)]
            logger.info(f"Plugin '{name}' skipped — missing env: {missing}")
            inactive.append(plugin)
            continue

        # Health check
        try:
            healthy = await plugin.health_check()
        except Exception as e:
            logger.warning(f"Plugin '{name}' skipped — health_check raised: {e}")
            inactive.append(plugin)
            continue

        if healthy:
            active.append(plugin)
            logger.info(f"Plugin '{name}' activated")
        else:
            logger.info(f"Plugin '{name}' skipped — health_check returned False")
            inactive.append(plugin)

    ACTIVE_PLUGINS = active
    INACTIVE_PLUGINS = inactive
    return active, inactive
