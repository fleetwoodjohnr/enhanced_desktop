import sys

from .application import DesktopForgeApplication


def main(argv=None):
    app = DesktopForgeApplication()
    return app.run(argv if argv is not None else sys.argv)
