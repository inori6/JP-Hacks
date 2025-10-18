"""Utility helpers shared across the application."""

from __future__ import annotations

import hashlib
import socket
from pathlib import Path
from typing import Optional

from kivy.utils import platform


def is_network_available(base_url: str | None = None, timeout: float = 2.0) -> bool:
    """Best-effort connectivity probe.

    On Android we query the ConnectivityManager to avoid hitting the network.
    On other platforms we attempt a lightweight TCP connect to the target host
    or a public fallback.
    """

    if platform == "android":
        try:
            from jnius import autoclass

            PythonActivity = autoclass('org.kivy.android.PythonActivity')
            activity = PythonActivity.mActivity
            if activity is None:
                return False
            Context = autoclass('android.content.Context')
            connectivity = activity.getSystemService(Context.CONNECTIVITY_SERVICE)
            if connectivity is None:
                return False
            network = connectivity.getActiveNetwork()
            if network is None:
                return False
            capabilities = connectivity.getNetworkCapabilities(network)
            if capabilities is None:
                return False
            has_internet = capabilities.hasCapability(
                autoclass('android.net.NetworkCapabilities').NET_CAPABILITY_INTERNET
            )
            return bool(has_internet)
        except Exception:
            return False

    host = None
    if base_url:
        from urllib.parse import urlparse

        parsed = urlparse(base_url)
        host = parsed.hostname
    candidates = [host, "8.8.8.8"]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            with socket.create_connection((candidate, 80), timeout=timeout):
                return True
        except OSError:
            continue
    return False


def compute_sha256(path: Path) -> Optional[str]:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except Exception:
        return None
