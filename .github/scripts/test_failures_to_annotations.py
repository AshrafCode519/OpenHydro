"""
Turns the failures in a `unittest -v` log into GitHub annotations, so they show on the run's page (and
through the public API) without the full log, which only the repository's admins can download.
"""

import sys

SEPARATOR = "=" * 70
DIVIDER = "-" * 70
MAX_ANNOTATIONS = 10  # GitHub shows at most 10 errors per step


def escape_data(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def escape_property(text: str) -> str:
    return escape_data(text).replace(":", "%3A").replace(",", "%2C")


def annotations(log: str):
    for block in log.split("\n" + SEPARATOR + "\n")[1:MAX_ANNOTATIONS + 1]:
        head, _, body = block.partition("\n" + DIVIDER + "\n")
        body = body.split("\n" + DIVIDER + "\n")[0]  # the last failure is followed by the summary
        yield f"::error title={escape_property(head.strip())[:250]}::{escape_data(body.strip()[-4000:])}"


if __name__ == "__main__":
    with open(sys.argv[1], encoding="utf-8", errors="replace") as f:
        for line in annotations(f.read()):
            print(line)
