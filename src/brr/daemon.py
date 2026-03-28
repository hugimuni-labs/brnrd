"""Central process for managing multiple repositories.

The daemon listens for chat commands, routes them to the appropriate
repository worker and orchestrates scheduled checks.  In this seed
version, it only prints a message when started.
"""


def start() -> None:
    """Start the daemon for the current repository.

    In the MVP, this function does nothing beyond printing a message.
    A full implementation would initialise a registry of repos, start
    listeners for connectors and spawn repo workers as needed.
    """
    print("[brr] daemon not yet implemented.  `brr up` does nothing in this seed version.")