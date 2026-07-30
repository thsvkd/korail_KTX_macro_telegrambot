"""Stand-ins for the search process, for tests that never spawn a real one."""

import subprocess
from unittest.mock import Mock

# What a search process that started properly looks like from the outside.
#
# start_reservation_process waits briefly on a newly spawned child and treats
# an exit within that window as a failure to start - the check that exists
# because a mistyped interpreter once produced a "search started" message for
# a process that had already died. A bare Mock() answers wait() with another
# Mock rather than blocking, which reads as an instant exit, so a test whose
# search is meant to run has to say so.


def alive_process(pid: int = 4242) -> Mock:
    """
    A Popen stand-in for a search that started and kept running.

    Args:
        pid: Process ID the caller will see

    Returns:
        A Mock that behaves like a running child process
    """
    process = Mock()
    process.pid = pid
    process.returncode = None
    process.poll.return_value = None
    # Still running when the grace period runs out, which is exactly how a
    # working search announces itself.
    process.wait.side_effect = subprocess.TimeoutExpired(cmd="python", timeout=1.0)
    return process


def make_alive(popen: Mock, pid: int = 4242) -> Mock:
    """
    Point a patched subprocess.Popen at a running child.

    Args:
        popen: The patched Popen
        pid: Process ID the caller will see

    Returns:
        The child process Mock
    """
    process = alive_process(pid)
    popen.return_value = process
    return process
