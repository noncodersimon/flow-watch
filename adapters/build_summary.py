"""Rebuild data/summary.json from the per-instrument files.

Runs last in the workflow, after every adapter has written its metrics and
events. The screener reads only this file, so a phone draws the table
without downloading a single series.
"""

from common import save_summary


def main():
    save_summary()


if __name__ == "__main__":
    main()
