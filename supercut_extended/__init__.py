"""Supercut Extended -- GPU-accelerated replacement for Outplayed's Supercut.

Outplayed detects the highlights well; it just renders them with libx264 on the CPU.
This package reuses Outplayed's own event data and cut list, and puts the encode on
the GPU instead.
"""

__version__ = "1.2.0"
