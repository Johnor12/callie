"""The command line: the systemd unit `service install` writes."""

from chesstutor import cli
from tests import function_tests


def test_the_unit_runs_no_shell():
    text = cli.unit_text(["/opt/my python/python3", "-m", "chesstutor.cli", "serve"], {"PYTHONPATH": "/src", "PATH": "/a b/bin:/usr/bin:/100%/$x"})
    lines = text.splitlines()
    assert 'ExecStart="/opt/my python/python3" "-m" "chesstutor.cli" "serve"' in lines  # the program itself: no shell, no profile
    assert 'Environment="PYTHONPATH=/src"' in lines and 'Environment="PATH=/a b/bin:/usr/bin:/100%%/$x"' in lines
    assert cli.unit_text(["/bin/a$b"], {}).count('ExecStart="/bin/a$$b"') == 1 and "PATH" in cli._CARRIED


load_tests = function_tests(globals())
