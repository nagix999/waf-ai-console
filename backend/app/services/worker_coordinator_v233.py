"""Editor-only release: keep the exact v2.32 judgment coordinator and fences.

The independent display lane is selected by the new launcher. No new judgment
runtime, schema or prompt version is introduced for this display-only change.
"""

from .worker_coordinator_v232 import serve

__all__ = ["serve"]
