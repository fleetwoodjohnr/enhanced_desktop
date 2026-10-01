import sys


def main(argv=None):
    argv = argv if argv is not None else sys.argv
    # The terminal widget's window runs as its own process (see terminal.py).
    if argv[1:2] == ["--terminal"]:
        from .terminal import main as terminal
        return terminal(argv[2:])
    from .application import DesktopForgeApplication
    app = DesktopForgeApplication()
    return app.run(argv)
