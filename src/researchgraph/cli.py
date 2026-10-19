"""Command-line interface for researchgraph."""

import click

from researchgraph import __version__


@click.group()
@click.version_option(__version__, prog_name="rg")
def main():
    """Store and inspect a research graph in a git repository."""
