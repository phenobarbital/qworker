import logging
from pprint import pprint

class colors:
    """
    Colors class.

       reset all colors with colors.reset;
       Use as colors.subclass.colorname.
    i.e. colors.fg.red or colors.fg.greenalso, the generic bold, disable,
    underline, reverse, strike through,
    and invisible work with the main class i.e. colors.bold
    """

    reset = '\033[0m'
    bold = '\033[01m'
    disable = '\033[02m'
    underline = '\033[04m'
    reverse = '\033[07m'
    strikethrough = '\033[09m'
    invisible = '\033[08m'

    class fg:
        """
        colors.fg.

        Foreground Color subClass
        """

        black = '\033[30m'
        red = '\033[31m'
        green = '\033[32m'
        orange = '\033[33m'
        blue = '\033[34m'
        purple = '\033[35m'
        cyan = '\033[36m'
        lightgrey = '\033[37m'
        darkgrey = '\033[90m'
        lightred = '\033[91m'
        lightgreen = '\033[92m'
        yellow = '\033[93m'
        lightblue = '\033[94m'
        pink = '\033[95m'
        lightcyan = '\033[96m'

def cPrint(msg, color: colors = None, level: str = "INFO", use_pprint: bool = False):
    try:
        if color is not None:
            if isinstance(color, colors):
                coloring = colors.bold + color
            else:
                coloring = colors.bold + getattr(colors.fg, color)
        elif level:
            if level == "INFO":
                coloring = colors.bold + colors.fg.green
            elif level == "SUCCESS":
                coloring = colors.bold + colors.fg.lightgreen
            elif level == "NOTICE":
                coloring = colors.fg.blue
            elif level == "DEBUG":
                coloring = colors.fg.lightblue
            elif level == "WARN" or level == "WARNING":
                coloring = colors.bold + colors.fg.yellow
            elif level == "ERROR":
                coloring = colors.fg.lightred
            elif level == "CRITICAL":
                coloring = colors.bold + colors.fg.red
            else:
                coloring = colors.reset
        else:
            coloring = colors.reset
    except Exception as err:
        logging.warning(
            f"Wrong color schema {color}, error: {err!s}"
        )
        coloring = colors.reset
    if use_pprint:
        pprint(msg)
    else:
        print(coloring + msg, colors.reset)
