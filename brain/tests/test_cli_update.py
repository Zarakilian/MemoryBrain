import sys
import importlib.util
from pathlib import Path
from unittest.mock import patch, MagicMock


def _load_cli():
    """Load cli/brain.py directly to avoid the 'brain' module import issue."""
    spec = importlib.util.spec_from_file_location(
        "brain_cli",
        Path(__file__).parent.parent.parent / "cli" / "brain.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cmd_update_exists():
    """cmd_update function must be defined in cli/brain.py."""
    mod = _load_cli()
    assert hasattr(mod, "cmd_update"), "cli/brain.py must define cmd_update()"


def test_cmd_update_exits_without_repo(tmp_path):
    """cmd_update must exit cleanly when MEMORYBRAIN_DIR unset and cwd is not a repo."""
    mod = _load_cli()
    import os
    with patch.dict(os.environ, {}, clear=False), \
         patch.object(sys, "exit") as mock_exit:
        os.environ.pop("MEMORYBRAIN_DIR", None)
        with patch("os.getcwd", return_value=str(tmp_path)):
            mod.cmd_update()
        mock_exit.assert_called_once_with(1)


def test_update_command_registered():
    """The 'update' command must be handled in main() dispatcher."""
    mod = _load_cli()
    import inspect
    src = inspect.getsource(mod)
    assert '"update"' in src or "'update'" in src, \
        "cli/brain.py main() must dispatch the 'update' command"
