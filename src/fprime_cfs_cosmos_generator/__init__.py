"""Standalone per-application cFS COSMOS target generation."""

from .generator import BundleArtifacts, GeneratorError, build_bundle

__all__ = ["BundleArtifacts", "GeneratorError", "build_bundle"]
