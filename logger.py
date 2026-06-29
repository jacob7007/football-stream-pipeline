import os
import sys

# Enable ANSI escape characters on Windows
if sys.platform == 'win32':
    try:
        os.system('')
    except Exception:
        pass

# ANSI Escape Sequences for Colors
COLOR_RESET = "\033[0m"
COLOR_BOLD = "\033[1m"
COLOR_UNDERLINE = "\033[4m"

# Foreground Colors (high-intensity)
COLOR_BLACK = "\033[30m"
COLOR_RED = "\033[91m"
COLOR_GREEN = "\033[92m"
COLOR_YELLOW = "\033[93m"
COLOR_BLUE = "\033[94m"
COLOR_MAGENTA = "\033[95m"
COLOR_CYAN = "\033[96m"
COLOR_WHITE = "\033[97m"
COLOR_DARK_GRAY = "\033[90m"

def step_header(step_num: str, title: str):
    """Prints a styled header for a pipeline step."""
    banner = "=" * 65
    print(f"\n{COLOR_BOLD}{COLOR_CYAN}{banner}{COLOR_RESET}")
    print(f"{COLOR_BOLD}{COLOR_CYAN}>>> [{step_num}] {title.upper()}{COLOR_RESET}")
    print(f"{COLOR_BOLD}{COLOR_CYAN}{banner}{COLOR_RESET}\n")

def info(message: str, indent: int = 2):
    spaces = " " * indent
    print(f"{spaces}{COLOR_BLUE}ℹ{COLOR_RESET}  {message}")

def success(message: str, indent: int = 2):
    spaces = " " * indent
    print(f"{spaces}{COLOR_GREEN}✔{COLOR_RESET}  {COLOR_BOLD}{message}{COLOR_RESET}")

def warning(message: str, indent: int = 2):
    spaces = " " * indent
    print(f"{spaces}{COLOR_YELLOW}⚠{COLOR_RESET}  {message}")

def error(message: str, indent: int = 2):
    spaces = " " * indent
    print(f"{spaces}{COLOR_RED}✘{COLOR_RESET}  {COLOR_BOLD}{message}{COLOR_RESET}", file=sys.stderr)

def custom(prefix: str, message: str, color: str, indent: int = 2):
    spaces = " " * indent
    print(f"{spaces}{color}{prefix}{COLOR_RESET} {message}")

def separator(char: str = "-", length: int = 65, color: str = COLOR_DARK_GRAY):
    print(f"{color}{char * length}{COLOR_RESET}")
