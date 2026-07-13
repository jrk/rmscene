"""Command-line entry point.

    python -m rmrender input.rm output.{png,pdf,svg} [--scale N]

The output format is chosen from the file extension. `--scale` applies
to PNG output only.
"""

import argparse
import logging
import pathlib

from .render import render_pdf, render_png, render_svg


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a v6 .rm file to PNG, PDF, or SVG")
    parser.add_argument("input", help="input .rm file")
    parser.add_argument("output", help="output file (.png, .pdf, or .svg)")
    parser.add_argument("--scale", type=float, default=1.0, help="resolution multiplier (PNG only)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)
    suffix = pathlib.Path(args.output).suffix.lower()
    if suffix == ".png":
        w, h = render_png(args.input, args.output, scale=args.scale)
    elif suffix == ".pdf":
        w, h = render_pdf(args.input, args.output)
    elif suffix == ".svg":
        w, h = render_svg(args.input, args.output)
    else:
        parser.error(f"Unsupported output format: {suffix}")
    print(f"{args.output}: {w:.0f}x{h:.0f}")


if __name__ == "__main__":
    main()
