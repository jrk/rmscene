"""Command-line entry point: python -m rmrender input.rm output.png [--scale N]"""

import argparse
import logging

from .render import render_png


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a v6 .rm file to PNG")
    parser.add_argument("input", help="input .rm file")
    parser.add_argument("output", help="output .png file")
    parser.add_argument("--scale", type=float, default=1.0, help="resolution multiplier")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)
    w, h = render_png(args.input, args.output, scale=args.scale)
    print(f"{args.output}: {w}x{h}")


if __name__ == "__main__":
    main()
